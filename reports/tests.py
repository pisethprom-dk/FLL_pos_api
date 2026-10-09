# v1.3.0 — the report rules that matter
from datetime import timedelta
from decimal import Decimal as D

from django.utils import timezone
from rest_framework.test import APIClient

from catalogue.models import Brand, Category, Product, Unit
from inventory.models import StockMovement
from inventory.services import post_count, record_counts, start_count
from sales.models import Invoice, Quotation, QuoteStatus, ReturnLine, SalesReturn
from sales.money import AMOUNT, CASH, CREDIT, KHQR
from partners.models import Customer
from sales.services import post_return, record_payment, void_invoice, void_payment
from sales.tests import SalesTestCase, T
from warranty.models import ClaimStatus, WarrantyClaim

URL = "/api/reports/daily-sales/"


class DailySalesTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        # Dara Meas (the Seller) sells two; Sokha Chan sells one, paid in riel.
        self.discounted = self.sell(
            self.walk_in, (self.drill, "1", AMOUNT, "5.00"), tenders=[T(CASH, "100")]
        )
        self.split = self.sell(self.sok, (self.grinder, "2"), tenders=[T(KHQR, "50"), T(CREDIT, "54")])
        self.riel = self.sell(
            self.walk_in, (self.drill, "1"), tenders=[T(CASH, "320000", "KHR")], user=self.other_seller
        )
        # Neither of these is a sale.
        void_invoice(self.sell(self.walk_in, (self.grinder, "1")), self.admin, "Rung up twice")
        self.held(self.walk_in, (self.drill, "1"))

    def report(self, query="", status=200):
        res = self.client.get(f"{URL}?{query}")
        self.assertEqual(res.status_code, status, res.data)
        return res.data

    def test_the_figures_add_up_by_day_seller_and_tender(self):
        self.as_admin()
        data = self.report()
        self.assertEqual(dict(data["summary"]), {
            "sales": "255.00", "sales_khr": "1045500", "invoices": 3, "average": "85.00",
            "discount": "5.00", "cost": "182.60", "profit": "72.40", "margin": "28.4",
            "returns_total": "0.00", "returns_count": 0,
        })
        # Cash is what stayed in the drawer: $100 less $27 change, and the riel sale.
        self.assertEqual(
            [(t["tender"], t["amount"], t["share"]) for t in data["by_tender"]],
            [(CASH, "151.00", "59.2"), (KHQR, "50.00", "19.6"), (CREDIT, "54.00", "21.2")],
        )
        self.assertEqual(
            [(s["seller_name"], s["invoices"], s["sales"], s["average"], s["discount"]) for s in data["by_seller"]],
            [("Dara Meas", 2, "177.00", "88.50", "5.00"), ("Sokha Chan", 1, "78.00", "78.00", "0.00")],
        )
        [day] = data["by_day"]
        self.assertEqual(
            {k: day[k] for k in ("invoices", "cash", "khqr", "credit", "sales", "cost", "profit")},
            {"invoices": 3, "cash": "151.00", "khqr": "50.00", "credit": "54.00", "sales": "255.00",
             "cost": "182.60", "profit": "72.40"},
        )
        self.assertEqual(day["date"], str(timezone.localdate()))

    def test_a_seller_sees_only_their_own_and_no_cost(self):
        self.as_seller()
        data = self.report(f"seller={self.other_seller.pk}")
        self.assertEqual(data["seller"], self.seller.pk)
        self.assertEqual((data["summary"]["invoices"], data["summary"]["sales"]), (2, "177.00"))
        self.assertEqual(
            [data["summary"][k] for k in ("cost", "profit", "margin")], [None, None, None]
        )
        self.assertEqual([s["seller_name"] for s in data["by_seller"]], ["Dara Meas"])
        self.assertIsNone(data["by_day"][0]["cost"])

    def test_an_admin_filters_by_seller(self):
        self.as_admin()
        data = self.report(f"seller={self.other_seller.pk}")
        self.assertEqual((data["summary"]["invoices"], data["summary"]["sales"]), (1, "78.00"))

    def test_returns_posted_in_the_period_are_shown_apart(self):
        ret = SalesReturn.objects.create(invoice=self.split, reason="Wrong size", refund_method="CASH")
        ReturnLine(sales_return=ret, invoice_line=self.split.lines.get(), quantity=D("1")).save()
        post_return(ret, self.admin)
        self.as_admin()
        summary = self.report()["summary"]
        self.assertEqual((summary["returns_total"], summary["returns_count"]), ("52.00", 1))
        self.assertEqual(summary["sales"], "255.00")

    def test_a_sale_counts_on_the_day_it_was_sold(self):
        Invoice.objects.filter(pk=self.riel.pk).update(sale_date=timezone.now() - timedelta(days=1))
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        self.as_admin()
        self.assertEqual(self.report(f"date_from={today}&date_to={today}")["summary"]["invoices"], 2)
        self.assertEqual(
            [d["date"] for d in self.report(f"date_from={yesterday}&date_to={today}")["by_day"]],
            [str(today), str(yesterday)],
        )
        self.report(f"date_from={today}&date_to={yesterday}", status=400)


STOCK_URL = "/api/reports/stock-on-hand/"


class StockOnHandTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        power = self.drill.category
        self.saws = Category.objects.create(code="CAT-0103", name="Saws", parent=power)
        self.total = Brand.objects.create(name="Total")
        pcs = Unit.objects.get(code="PCS")

        def product(code, name, category, **kw):
            return Product.objects.create(
                code=code, name=name, category=category, unit=pcs,
                retail_price=D("10"), wholesale_price=D("9"), **kw,
            )

        self.saw = product("TL-0130", "Circular saw", self.saws, brand=self.total)
        self.sander = product("TL-0140", "Orbital sander", power)
        self.retired_empty = product("TL-0141", "Old sander", power, is_active=False)
        self.receive(self.saw, "10", "40.00")
        self.receive(self.sander, "3", "20.00")
        Product.objects.filter(pk=self.sander.pk).update(is_active=False)  # retired, still on the shelf
        Product.objects.filter(pk=self.drill.pk).update(reorder_level=D("25"))
        self.sell(self.walk_in, (self.grinder, "5"))  # sold out
        StockMovement.objects.filter(product=self.driver).update(
            movement_date=timezone.localdate() - timedelta(days=100)
        )

    def report(self, query=""):
        res = self.client.get(f"{STOCK_URL}?{query}")
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def codes(self, query):
        return [row["code"] for row in self.report(query)["rows"]]

    def test_each_product_gets_the_first_status_that_applies(self):
        self.as_admin()
        data = self.report()
        self.assertEqual(
            [(r["code"], r["qty_on_hand"], r["status"], r["value"]) for r in data["rows"]],
            [
                ("TL-0101", "20.00", "REORDER", "1048.00"),
                ("TL-0118", "0.00", "OUT", "0.00"),
                ("TL-0130", "10.00", "OK", "400.00"),
                ("TL-0140", "3.00", "OK", "60.00"),
                ("TL-0150", "4.00", "IDLE", "244.00"),
            ],
        )
        self.assertEqual(data["rows"][2]["brand_name"], "Total")
        self.assertEqual(data["rows"][4]["last_moved"], str(timezone.localdate() - timedelta(days=100)))

    def test_the_summary_covers_all_stock_whatever_the_filters(self):
        self.as_admin()
        data = self.report("search=sander")
        self.assertEqual(dict(data["summary"]), {
            "products": 5, "value": "1752.00", "below_reorder": 2, "out_of_stock": 1,
            "no_movement": 1, "no_movement_value": "244.00", "last_count": None,
        })
        self.assertEqual(data["rows_value"], "60.00")

    def test_the_filters(self):
        self.as_admin()
        self.assertEqual(self.codes("status=below_reorder"), ["TL-0101", "TL-0118"])
        self.assertEqual(self.codes("status=out_of_stock"), ["TL-0118"])
        self.assertEqual(self.codes("status=no_movement"), ["TL-0150"])
        self.assertEqual(self.codes(f"category={self.saws.pk}"), ["TL-0130"])
        self.assertEqual(len(self.codes(f"category={self.drill.category_id}")), 5)
        self.assertEqual(self.codes(f"brand={self.total.pk}"), ["TL-0130"])
        self.assertEqual(self.codes("search=tl-0101"), ["TL-0101"])

    def test_the_last_count_and_what_it_found(self):
        count = start_count(self.saws, self.admin)
        record_counts(count, [{"line": count.lines.get().pk, "counted_qty": D("9")}])
        post_count(count, self.admin)
        self.as_admin()
        last = self.report()["summary"]["last_count"]
        self.assertEqual(
            (last["number"], last["category"], last["differences"], last["value"]),
            (count.number, "Saws", 1, "-40.00"),
        )

    def test_a_seller_sees_quantities_but_no_cost(self):
        self.as_seller()
        data = self.report()
        self.assertEqual(data["rows"][0]["qty_on_hand"], "20.00")
        self.assertEqual(
            {data["rows"][0]["avg_cost"], data["rows"][0]["value"], data["summary"]["value"],
             data["summary"]["no_movement_value"], data["rows_value"]},
            {None},
        )


RECEIVABLES_URL = "/api/reports/receivables/"


class ReceivablesTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        today = timezone.localdate()

        def sold_ago(invoice, days):
            Invoice.objects.filter(pk=invoice.pk).update(
                sale_date=timezone.now() - timedelta(days=days),
                due_date=today - timedelta(days=days - 30),
            )

        # Sok Shop: three credit sales, 100 days old, 45 days old and today's.
        old = self.sell(self.sok, (self.drill, "2"), tenders=[T(CREDIT, "138")])
        sold_ago(old, 100)
        middle = self.sell(self.sok, (self.drill, "1"), tenders=[T(CREDIT, "69")])
        sold_ago(middle, 45)
        new = self.sell(self.sok, (self.grinder, "2"), tenders=[T(CREDIT, "104")])
        # $40 off the oldest; a payment voided since counts for nothing.
        pay = dict(customer=self.sok, payment_date=today, tender="CASH", currency="USD", user=self.seller)
        record_payment(amount_tendered=D("40"), **pay)
        void_payment(record_payment(amount_tendered=D("10"), **pay), self.admin, "Entered twice")
        # One grinder back from today's sale: $52 off what it owes.
        ret = SalesReturn.objects.create(invoice=new, reason="Wrong size", refund_method="CASH")
        ReturnLine(sales_return=ret, invoice_line=new.lines.get(), quantity=D("1")).save()
        post_return(ret, self.admin)
        Customer.objects.filter(pk=self.sok.pk).update(credit_limit=D("200"))  # now over it
        # Dara Grocery: one sale 70 days ago, put on hold since.
        Customer.objects.filter(pk=self.dara.pk).update(credit_hold=False)
        self.dara.refresh_from_db()
        sold_ago(self.sell(self.dara, (self.driver, "1"), tenders=[T(CREDIT, "84")]), 70)
        Customer.objects.filter(pk=self.dara.pk).update(credit_hold=True)
        # A cash sale owes nothing.
        self.sell(self.walk_in, (self.drill, "1"))

    def report(self, query="", status=200):
        res = self.client.get(f"{RECEIVABLES_URL}?{query}")
        self.assertEqual(res.status_code, status)
        return res.data

    def test_each_debt_is_aged_by_the_days_since_its_invoice(self):
        self.as_admin()
        rows = self.report()["rows"]
        self.assertEqual(
            [(r["name"], r["d0_30"], r["d31_60"], r["d61_90"], r["over_90"], r["owed"],
              r["credit_limit"], r["room_left"]) for r in rows],
            [
                ("Sok Shop", "52.00", "69.00", "0.00", "98.00", "219.00", "200.00", "-19.00"),
                ("Dara Grocery", "0.00", "0.00", "84.00", "0.00", "84.00", "1200.00", "1116.00"),
            ],
        )
        self.assertEqual(
            [(r["overdue"], r["over_limit"], r["on_hold"]) for r in rows],
            [(True, True, False), (True, False, True)],
        )

    def test_the_summary_covers_everyone_who_owes(self):
        self.as_admin()
        data = self.report("show=on_hold")
        self.assertEqual(dict(data["summary"]), {
            "owed": "303.00", "owed_khr": "1242300", "rate": "4100.000000",
            "past_60": "182.00", "past_60_share": "60.1", "collected_this_month": "40.00",
            "flagged": ["Sok Shop", "Dara Grocery"],
        })
        self.assertEqual([r["name"] for r in data["rows"]], ["Dara Grocery"])
        self.assertEqual((data["totals"]["owed"], data["totals"]["room_left"]), ("84.00", "1116.00"))

    def test_the_filters_and_the_totals(self):
        self.as_admin()
        self.assertEqual(dict(self.report()["totals"]), {
            "d0_30": "52.00", "d31_60": "69.00", "d61_90": "84.00", "over_90": "98.00",
            "owed": "303.00", "credit_limit": "1400.00", "room_left": "1097.00",
        })
        self.assertEqual([r["name"] for r in self.report("show=overdue")["rows"]], ["Sok Shop", "Dara Grocery"])
        self.assertEqual([r["name"] for r in self.report("show=over_limit")["rows"]], ["Sok Shop"])

    def test_only_an_admin_sees_what_customers_owe(self):
        self.as_seller()
        self.report(status=403)


DASHBOARD_URL = "/api/reports/dashboard/"


class DashboardTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        # Dara Meas (the Seller) sells two today; Sokha Chan one today, paid in
        # riel, and one three days ago by KHQR.
        self.cash_sale = self.sell(
            self.walk_in, (self.drill, "1", AMOUNT, "5.00"), tenders=[T(CASH, "100")]
        )
        self.split = self.sell(self.sok, (self.grinder, "2"), tenders=[T(KHQR, "50"), T(CREDIT, "54")])
        self.riel = self.sell(
            self.walk_in, (self.drill, "1"), tenders=[T(CASH, "320000", "KHR")], user=self.other_seller
        )
        self.earlier = self.sell(
            self.walk_in, (self.driver, "1"), tenders=[T(KHQR, "92")], user=self.other_seller
        )
        Invoice.objects.filter(pk=self.earlier.pk).update(sale_date=timezone.now() - timedelta(days=3))
        # Neither of these is a sale.
        void_invoice(self.sell(self.walk_in, (self.grinder, "1")), self.admin, "Rung up twice")
        self.held(self.walk_in, (self.drill, "1"))

        # Grinders (3 left) and drivers (3 left) at their reorder level; six
        # levels never received.
        Product.objects.filter(pk=self.grinder.pk).update(reorder_level=D("5"))
        Product.objects.filter(pk=self.driver.pk).update(reorder_level=D("3"))
        for n in range(6):
            Product.objects.create(
                code=f"TL-020{n}", name=f"Spirit level {n}", category=self.drill.category,
                unit=self.drill.unit, retail_price=D("10"), wholesale_price=D("9"),
            )

        def quote(days_left, status=QuoteStatus.SENT):
            q = self.quote(self.sok, (self.drill, "1"), accept=False)
            Quotation.objects.filter(pk=q.pk).update(
                status=status, valid_until=today + timedelta(days=days_left)
            )

        quote(7)  # runs out within the week: its last day still counts
        quote(-1)  # ran out yesterday
        quote(8)
        quote(2, QuoteStatus.DRAFT)  # not sent, so not waiting on an answer
        quote(-5, QuoteStatus.ACCEPTED)

        claim = dict(warranty_number="W-1001", product_name="Impact drill 13mm 710W")
        WarrantyClaim.objects.create(**claim, expiry_date=today + timedelta(days=200))
        WarrantyClaim.objects.create(**claim, expiry_date=today - timedelta(days=1), status=ClaimStatus.READY)
        WarrantyClaim.objects.create(**claim, expiry_date=today - timedelta(days=1), status=ClaimStatus.CLOSED)

    def dashboard(self):
        res = self.client.get(DASHBOARD_URL)
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def return_one(self, invoice, refund_method):
        ret = SalesReturn.objects.create(invoice=invoice, reason="Not needed", refund_method=refund_method)
        ReturnLine(sales_return=ret, invoice_line=invoice.lines.get(), quantity=D("1")).save()
        post_return(ret, self.admin)

    def test_an_admin_sees_the_whole_shop(self):
        self.as_admin()
        data = self.dashboard()
        sales = data["sales"]
        self.assertIsNone(sales["seller"])
        self.assertEqual(dict(sales["today"]), {
            "sales": "255.00", "sales_khr": "1045500", "invoices": 3, "average": "85.00",
            "profit": "72.40", "margin": "28.4",
        })
        today = timezone.localdate()
        figures = {0: ("255.00", "100.0"), 3: ("92.00", "36.1")}
        self.assertEqual(
            [(d["date"], d["sales"], d["share"]) for d in sales["last_7_days"]],
            [(str(today - timedelta(days=back)), *figures.get(back, ("0.00", "0.0")))
             for back in range(6, -1, -1)],
        )
        self.assertEqual(
            [(r["id"], r["paid_by"]) for r in sales["recent"]],
            [(self.riel.pk, CASH), (self.split.pk, "MIXED"), (self.cash_sale.pk, CASH), (self.earlier.pk, KHQR)],
        )
        self.assertEqual(
            dict(data["cash_today"]),
            {"sales": "151.00", "payments": "0.00", "refunds": "0.00", "total": "151.00"},
        )
        stock = data["stock"]
        self.assertEqual(
            {k: stock[k] for k in ("products", "value", "below_reorder", "out_of_stock", "more")},
            {"products": 9, "value": "1242.90", "below_reorder": 8, "out_of_stock": 6, "more": 3},
        )
        # Out of stock first, though TL-0118 and TL-0150 come first by code.
        self.assertEqual(
            [r["code"] for r in stock["running_low"]], ["TL-0200", "TL-0201", "TL-0202", "TL-0203", "TL-0204"]
        )
        owed = data["owed"]
        self.assertEqual(
            (owed["owed"], owed["over_90"], owed["more"], [r["name"] for r in owed["rows"]]),
            ("54.00", "0.00", 0, ["Sok Shop"]),
        )
        self.assertEqual(data["held_sales"], 1)
        self.assertEqual(dict(data["quotations"]), {"sent": 3, "expiring": 1, "expired": 1})
        self.assertEqual(dict(data["warranty"]), {"open": 2, "out_of_warranty": 1})

    def test_a_seller_sees_their_own_sales_and_no_money_parts(self):
        self.as_seller()
        data = self.dashboard()
        sales = data["sales"]
        self.assertEqual(sales["seller"], self.seller.pk)
        today = sales["today"]
        self.assertEqual(
            (today["invoices"], today["sales"], today["profit"], today["margin"]), (2, "177.00", None, None)
        )
        # Sokha Chan's sale three days ago is not theirs.
        self.assertEqual([d["sales"] for d in sales["last_7_days"]][-4:], ["0.00", "0.00", "0.00", "177.00"])
        self.assertEqual([(c["name"], c["sales"]) for c in sales["by_category"]], [("Power tools", "177.00")])
        self.assertEqual([r["id"] for r in sales["recent"]], [self.split.pk, self.cash_sale.pk])
        self.assertIsNone(data["cash_today"])
        self.assertIsNone(data["owed"])
        self.assertEqual((data["stock"]["below_reorder"], data["stock"]["value"]), (8, None))
        self.assertEqual((data["held_sales"], data["quotations"]["sent"], data["warranty"]["open"]), (1, 3, 2))

        self.client = APIClient()
        self.assertEqual(self.client.get(DASHBOARD_URL).status_code, 401)

    def test_cash_taken_today(self):
        pay = dict(customer=self.sok, payment_date=timezone.localdate(), user=self.seller)
        # ៛82,000 in cash is $20 off what Sok Shop owes. A KHQR payment and a
        # voided one are not cash taken.
        record_payment(tender="CASH", currency="KHR", amount_tendered=D("82000"), **pay)
        record_payment(tender="KHQR", currency="USD", amount_tendered=D("4"), **pay)
        void_payment(
            record_payment(tender="CASH", currency="USD", amount_tendered=D("10"), **pay),
            self.admin, "Entered twice",
        )
        # A grinder ($52) back from the split sale: $30 off what it still owes,
        # $22 refunded in cash. A drill back from the cash sale, by KHQR.
        self.return_one(self.split, "CASH")
        self.return_one(self.cash_sale, "KHQR")
        self.as_admin()
        self.assertEqual(
            dict(self.dashboard()["cash_today"]),
            {"sales": "151.00", "payments": "20.00", "refunds": "22.00", "total": "149.00"},
        )

    def test_today_by_main_category_the_biggest_four_then_other(self):
        saws = Category.objects.create(code="CAT-0103", name="Saws", parent=self.drill.category)

        def service(code, name, price, category):  # tracks no stock, so it sells without any
            return Product.objects.create(
                code=code, name=name, category=category, unit=self.drill.unit,
                retail_price=D(price), wholesale_price=D(price), track_stock=False,
            )

        lines = [(service("SV-0100", "Saw sharpening", "10", saws), "1")]  # counts in Power tools
        for n, (name, price) in enumerate(
            [("Hand tools", "5"), ("Paint", "4"), ("Plumbing", "3"), ("Electrical", "2"), ("Garden", "1")]
        ):
            group = Category.objects.create(code=f"CAT-1{n}", name=name)
            lines.append((service(f"SV-020{n}", f"{name} service", price, group), "1"))
        self.sell(self.walk_in, *lines)
        self.as_admin()
        by_category = self.dashboard()["sales"]["by_category"]
        self.assertEqual(
            [(c["name"], c["sales"], c["share"]) for c in by_category],
            [("Power tools", "265.00", "94.6"), ("Hand tools", "5.00", "1.8"), ("Paint", "4.00", "1.4"),
             ("Plumbing", "3.00", "1.1"), ("Other", "3.00", "1.1")],
        )
        self.assertIsNone(by_category[-1]["category"])
