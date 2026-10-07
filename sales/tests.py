# v1.0.5 — the sales rules that matter
from datetime import timedelta
from decimal import Decimal as D
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from catalogue.models import Category, Product, Unit
from company.models import ExchangeRate
from company.services import rate_is_in_use
from core.exceptions import CreditLimitError, DiscountLimitError, DomainError, InsufficientStockError
from core.exceptions import PostedDocumentError
from inventory.models import StockIn, StockInLine, StockMovement
from inventory.services import post_stock_in
from partners.models import Customer, PriceTier, Supplier, SupplierType
from sales.models import Invoice, InvoiceLine, Quotation, QuotationLine, QuoteStatus, ReturnLine, SalesReturn
from sales.money import AMOUNT, CASH, CREDIT, KHQR, PERCENT, Tender, net_price, settle
from sales.services import (
    accept_quote,
    complete_invoice,
    customer_balance,
    post_return,
    record_payment,
    send_quote,
    void_invoice,
)
from users.models import Role, User

RATE = D("4100")


def first_invoice_number():
    """Invoices are numbered by day: INV-YYYYMMDD001 is today's first."""
    return f"INV-{timezone.localdate():%Y%m%d}001"


def T(kind, amount, currency="USD"):
    return Tender(kind=kind, currency=currency, amount=D(amount))


class MoneyTests(TestCase):
    def test_a_line_discount_comes_off_each_unit(self):
        self.assertEqual(net_price(D("52.00"), PERCENT, D("5")), D("49.40"))
        self.assertEqual(net_price(D("1.70"), AMOUNT, D("0.20")), D("1.50"))
        self.assertEqual(net_price(D("78.00"), "", D("0")), D("78.00"))

    def test_the_cap_is_fifteen_percent_and_absolute(self):
        self.assertEqual(net_price(D("100.00"), PERCENT, D("15")), D("85.00"))
        with self.assertRaises(DiscountLimitError):
            net_price(D("100.00"), PERCENT, D("15.01"))
        with self.assertRaises(DiscountLimitError):
            net_price(D("25.50"), AMOUNT, D("4.00"))  # 15.7%

    def test_a_fixed_price_takes_no_discount(self):
        with self.assertRaises(DiscountLimitError):
            net_price(D("92.00"), PERCENT, D("1"), price_fixed=True)

    def test_dollars_riel_and_credit_together(self):
        # The mockup's sale: $150 + ៛410,000 + $70 on credit against $320.
        s = settle(D("320.00"), [T(CASH, "150"), T(CASH, "410000", "KHR"), T(CREDIT, "70")], RATE)
        self.assertEqual((s.paid_now, s.on_credit, s.change_due), (D("250.00"), D("70.00"), D("0.00")))

    def test_change_is_split_into_dollars_and_riel(self):
        s = settle(D("361.45"), [T(CASH, "400")], RATE)
        self.assertEqual((s.change_due, s.change_usd, s.change_khr), (D("38.55"), D("38"), D("2300")))
        self.assertEqual(s.rounding, D("-0.01"))  # ៛2,300 is worth $0.56, a cent over

    def test_a_shortfall_under_half_a_riel_note_is_rounding(self):
        # $12.40 is ៛50,840; the customer hands over ៛50,800.
        s = settle(D("12.40"), [T(CASH, "50800", "KHR")], RATE)
        self.assertEqual(s.rounding, D("-0.01"))

    def test_a_real_shortfall_and_change_from_khqr_are_refused(self):
        with self.assertRaises(DomainError):
            settle(D("12.40"), [T(CASH, "12.00")], RATE)
        with self.assertRaises(DomainError):
            settle(D("12.40"), [T(KHQR, "20.00")], RATE)


class SalesTestCase(TestCase):
    def setUp(self):
        call_command("seed", stdout=StringIO())
        ExchangeRate.objects.update(effective_date=timezone.localdate() - timedelta(days=30), rate=RATE)
        self.admin = User.objects.create_user(
            username="owner", password="owner-pass-99", full_name="Bopha Ly", role=Role.ADMIN,
        )
        self.seller = User.objects.create_user(
            username="dara", password="counter-pass-99", full_name="Dara Meas", role=Role.SELLER,
        )
        self.other_seller = User.objects.create_user(
            username="sokha", password="counter-pass-98", full_name="Sokha Chan", role=Role.SELLER,
        )
        cat = Category.objects.create(code="CAT-01", name="Power tools")
        pcs = Unit.objects.get(code="PCS")

        def product(code, name, retail, wholesale, **kw):
            return Product.objects.create(
                code=code, name=name, category=cat, unit=pcs,
                retail_price=D(retail), wholesale_price=D(wholesale), **kw,
            )

        self.drill = product("TL-0101", "Impact drill 13mm 710W", "78.00", "69.00")
        self.grinder = product("TL-0118", "Angle grinder 100mm", "59.00", "52.00")
        self.driver = product("TL-0150", "Cordless driver 12V", "92.00", "84.00", is_price_fixed=True)
        self.key_cutting = product("SV-0001", "Key cutting", "1.50", "1.50", track_stock=False)
        self.supplier = Supplier.objects.create(name="Total Tools", supplier_type=SupplierType.DISTRIBUTOR)
        self.receive(self.drill, "20", "52.40")
        self.receive(self.grinder, "5", "38.90")
        self.receive(self.driver, "4", "61.00")

        self.walk_in = Customer.walk_in()
        self.sok = Customer.objects.create(
            name="Sok Shop", price_tier=PriceTier.WHOLESALE,
            allow_credit=True, credit_limit=D("1500"), payment_terms_days=30,
        )
        self.dara = Customer.objects.create(
            name="Dara Grocery", price_tier=PriceTier.WHOLESALE,
            allow_credit=True, credit_limit=D("1200"), credit_hold=True,
        )
        self.bopha = Customer.objects.create(name="Bopha (Street 315)", price_tier=PriceTier.WHOLESALE)

    def receive(self, product, qty, cost):
        doc = StockIn.objects.create(supplier=self.supplier)
        StockInLine(document=doc, product=product, packs=D(qty), pack_cost=D(cost)).save()
        post_stock_in(doc, self.admin)

    def held(self, customer, *lines, quotation=None):
        """lines: (product, qty[, discount kind, value]) or (quote_line, qty)"""
        invoice = Invoice.objects.create(customer=customer, quotation=quotation)
        for item, qty, *disc in lines:
            if isinstance(item, QuotationLine):
                line = InvoiceLine(invoice=invoice, quote_line=item, product=item.product, quantity=D(qty))
            else:
                line = InvoiceLine(
                    invoice=invoice, product=item, quantity=D(qty),
                    discount_type=disc[0] if disc else "", discount_value=D(disc[1]) if disc else D("0"),
                )
            line.save()
        return invoice

    def sell(self, customer, *lines, tenders=None, user=None, quotation=None):
        invoice = self.held(customer, *lines, quotation=quotation)
        if tenders is None:
            total = sum(l.line_total for l in invoice.lines.all())
            tenders = [T(CASH, str(total))]
        return complete_invoice(invoice, tenders, user or self.seller)

    def quote(self, customer, *lines, accept=True):
        q = Quotation.objects.create(customer=customer)
        for product, qty in lines:
            QuotationLine(quotation=q, product=product, quantity=D(qty)).save()
        return accept_quote(q) if accept else q

    def on_hand(self, product):
        product.refresh_from_db()
        return product.qty_on_hand

    def _as(self, user, password):
        self.client = APIClient()
        res = self.client.post(
            reverse("auth-login"), {"username": user.username, "password": password}, format="json"
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def as_seller(self):
        self._as(self.seller, "counter-pass-99")

    def as_admin(self):
        self._as(self.admin, "owner-pass-99")


class QuotationTests(SalesTestCase):
    def test_prices_are_stamped_from_the_customer_tier(self):
        q = self.quote(self.sok, (self.drill, "10"), accept=False)
        line = q.lines.get()
        self.assertEqual((q.price_tier, line.unit_price, line.line_total), ("WHOLESALE", D("69.00"), D("690.00")))

    def test_a_quotation_carries_the_customer_s_phone_and_address(self):
        Customer.objects.filter(pk=self.sok.pk).update(
            phone="012 330 441", address="No 10, Street 271", district="", province="Phnom Penh",
        )
        q = self.quote(self.sok, (self.drill, "1"), accept=False)
        self.as_seller()
        data = self.client.get(f"/api/sales/quotations/{q.pk}/").data
        self.assertEqual(
            (data["customer_phone"], data["customer_address"]),
            ("012 330 441", "No 10, Street 271, Phnom Penh"),
        )

    def test_a_walk_in_gets_no_quotation(self):
        self.as_seller()
        res = self.client.post("/api/sales/quotations/", {"customer": self.walk_in.pk}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("customer", res.data)

    def test_lines_are_fixed_once_accepted(self):
        q = self.quote(self.sok, (self.drill, "10"))
        with self.assertRaises(PostedDocumentError):
            q.lines.get().save()
        self.as_seller()
        res = self.client.patch(f"/api/sales/quotations/{q.pk}/", {"terms": "x"}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_send_accept_and_reject(self):
        q = self.quote(self.sok, (self.drill, "1"), accept=False)
        self.assertEqual(send_quote(q).status, QuoteStatus.SENT)
        self.as_seller()
        url = f"/api/sales/quotations/{q.pk}/"
        self.assertEqual(self.client.post(url + "reject/", {}, format="json").status_code, 400)
        res = self.client.post(url + "reject/", {"note": "Bought elsewhere"}, format="json")
        self.assertEqual((res.status_code, res.data["status"]), (200, "REJECTED"))

    def test_expiry_is_worked_out_not_stored(self):
        q = self.quote(self.sok, (self.drill, "1"), accept=False)
        Quotation.objects.filter(pk=q.pk).update(valid_until=timezone.localdate() - timedelta(days=1))
        q.refresh_from_db()
        self.assertTrue(q.is_expired)
        self.assertEqual(q.status, QuoteStatus.DRAFT)

    def test_quote_lines_obey_the_discount_cap(self):
        self.as_seller()
        res = self.client.post("/api/sales/quotations/", {
            "customer": self.sok.pk,
            "lines": [{"product": self.drill.pk, "quantity": "1", "discount_type": "PERCENT", "discount_value": "20"}],
        }, format="json")
        self.assertEqual(res.status_code, 400)


class InvoiceTests(SalesTestCase):
    def test_a_held_sale_has_no_number_no_stock_and_no_rate(self):
        invoice = self.held(self.walk_in, (self.drill, "1"))
        self.assertEqual((invoice.number, invoice.exchange_rate), (None, None))
        self.assertEqual(self.on_hand(self.drill), D("20"))

    def test_completing_stamps_number_rate_cost_and_moves_stock(self):
        invoice = self.sell(self.walk_in, (self.drill, "2"), (self.grinder, "1", PERCENT, "5"))
        self.assertEqual(invoice.number, first_invoice_number())
        self.assertEqual(invoice.status, "COMPLETED")
        self.assertEqual(invoice.exchange_rate, RATE)
        self.assertEqual(invoice.seller, self.seller)
        self.assertEqual(invoice.total, D("212.05"))  # 2 × 78 + 59 less 5%
        self.assertEqual(invoice.discount_total, D("2.95"))
        self.assertEqual(self.on_hand(self.drill), D("18"))
        costs = dict(invoice.lines.values_list("product__code", "unit_cost"))
        self.assertEqual(costs, {"TL-0101": D("52.40"), "TL-0118": D("38.90")})

    def test_no_sale_past_zero_and_nothing_half_done(self):
        invoice = self.held(self.walk_in, (self.drill, "2"), (self.grinder, "6"))
        with self.assertRaises(InsufficientStockError):
            complete_invoice(invoice, [T(CASH, "1000")], self.seller)
        self.assertEqual(self.on_hand(self.drill), D("20"))
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, invoice.number), ("HELD", None))
        # The failed sale did not use up a number.
        self.assertEqual(self.sell(self.walk_in, (self.drill, "1")).number, first_invoice_number())

    def test_a_line_carries_what_the_printed_invoice_needs(self):
        Product.objects.filter(pk=self.drill.pk).update(short_name="Impact drill 710W", warranty_months=12)
        sold = self.sell(self.walk_in, (self.drill, "1"), (self.grinder, "1"))
        self.as_seller()
        lines = self.client.get(f"/api/sales/invoices/{sold.pk}/").data["lines"]
        self.assertEqual(
            [(l["product_code"], l["product_short_name"], l["warranty_months"]) for l in lines],
            [("TL-0101", "Impact drill 710W", 12), ("TL-0118", "Angle grinder 100mm", 0)],
        )

    def test_the_sales_list_can_leave_held_sales_out(self):
        sold = self.sell(self.walk_in, (self.drill, "1"))
        voided = void_invoice(self.sell(self.walk_in, (self.grinder, "1")), self.admin, "Rung up twice")
        held = self.held(self.walk_in, (self.drill, "1"))
        self.as_seller()

        def ids(query):
            res = self.client.get(f"/api/sales/invoices/?{query}")
            self.assertEqual(res.status_code, 200)
            return {row["id"] for row in res.data["results"]}

        self.assertEqual(ids("held=false"), {sold.pk, voided.pk})
        self.assertEqual(ids("held=true"), {held.pk})
        self.assertEqual(ids(""), {sold.pk, voided.pk, held.pk})

    def test_the_api_refuses_a_discount_over_the_cap_or_on_a_fixed_price(self):
        self.as_seller()
        for product, value in [(self.drill, "16"), (self.driver, "1")]:
            res = self.client.post("/api/sales/invoices/", {
                "customer": self.walk_in.pk,
                "lines": [{"product": product.pk, "quantity": "1", "discount_type": "PERCENT", "discount_value": value}],
            }, format="json")
            self.assertEqual(res.status_code, 400, product.code)

    def test_choosing_a_customer_reprices_the_held_sale(self):
        self.as_seller()
        res = self.client.post("/api/sales/invoices/", {
            "customer": self.walk_in.pk, "lines": [{"product": self.drill.pk, "quantity": "2"}],
        }, format="json")
        self.assertEqual(res.data["held_total"], "156.00")
        res = self.client.patch(f"/api/sales/invoices/{res.data['id']}/", {"customer": self.sok.pk}, format="json")
        self.assertEqual((res.data["price_tier"], res.data["held_total"]), ("WHOLESALE", "138.00"))

    def test_services_sell_without_stock(self):
        invoice = self.sell(self.walk_in, (self.key_cutting, "2"))
        self.assertEqual(invoice.lines.get().unit_cost, D("0"))
        self.assertFalse(StockMovement.objects.filter(product=self.key_cutting).exists())

    def test_a_completed_invoice_is_frozen_and_a_held_one_can_be_cancelled(self):
        invoice = self.sell(self.walk_in, (self.drill, "1"))
        held = self.held(self.walk_in, (self.drill, "1"))
        self.as_seller()
        url = f"/api/sales/invoices/{invoice.pk}/"
        self.assertEqual(self.client.patch(url, {"hold_label": "x"}, format="json").status_code, 400)
        self.assertEqual(self.client.delete(url).status_code, 400)
        self.assertEqual(self.client.delete(f"/api/sales/invoices/{held.pk}/").status_code, 204)

    def test_a_seller_never_sees_cost(self):
        invoice = self.sell(self.walk_in, (self.drill, "1"))
        self.as_seller()
        res = self.client.get(f"/api/sales/invoices/{invoice.pk}/")
        self.assertIsNone(res.data["lines"][0]["unit_cost"])
        self.assertIsNone(res.data["cost_total"])
        self.assertIsNone(res.data["profit"])
        self.as_admin()
        res = self.client.get(f"/api/sales/invoices/{invoice.pk}/")
        self.assertEqual((res.data["lines"][0]["unit_cost"], res.data["profit"]), ("52.4000", "25.60"))

    def test_a_name_and_phone_are_for_walk_ins_only(self):
        self.as_seller()
        res = self.client.post("/api/sales/invoices/", {
            "customer": self.sok.pk, "walk_in_name": "Mr Chan",
            "lines": [{"product": self.drill.pk, "quantity": "1"}],
        }, format="json")
        self.assertEqual(res.status_code, 400)

    def test_complete_through_the_api_with_riel_change(self):
        self.as_seller()
        res = self.client.post("/api/sales/invoices/", {
            "customer": self.walk_in.pk, "walk_in_name": "Mr Chan",
            "lines": [{"product": self.grinder.pk, "quantity": "1"}],
        }, format="json")
        res = self.client.post(f"/api/sales/invoices/{res.data['id']}/complete/", {
            "tenders": [{"kind": "CASH", "currency": "USD", "amount": "60.00"}],
        }, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data["change_due"], res.data["change_usd"], res.data["change_khr"]), ("1.00", "1.00", "0"))
        self.assertEqual(res.data["total_khr"], "241900")

    def test_the_rate_is_in_use_once_a_sale_stamps_it(self):
        rate = ExchangeRate.objects.get()
        self.assertFalse(rate_is_in_use(rate))
        self.sell(self.walk_in, (self.drill, "1"))
        self.assertTrue(rate_is_in_use(rate))


class CreditTests(SalesTestCase):
    def test_a_walk_in_and_a_cash_only_customer_never_buy_on_credit(self):
        for customer in (self.walk_in, self.bopha):
            invoice = self.held(customer, (self.drill, "1"))
            total = invoice.lines.get().line_total
            with self.assertRaises(CreditLimitError):
                complete_invoice(invoice, [T(CREDIT, str(total))], self.seller)

    def test_a_customer_on_hold_must_pay_now(self):
        invoice = self.held(self.dara, (self.drill, "1"))
        with self.assertRaises(CreditLimitError):
            complete_invoice(invoice, [T(CREDIT, "69")], self.admin)

    def test_the_limit_counts_what_is_already_owed_and_binds_admin_too(self):
        self.sell(self.sok, (self.drill, "15"), tenders=[T(CREDIT, "1035")])  # 15 × 69
        invoice = self.held(self.sok, (self.drill, "5"))  # $345 more makes $1,380, within $1,500
        complete_invoice(invoice, [T(CREDIT, "345")], self.seller)
        self.assertEqual(customer_balance(self.sok), D("1380.00"))
        over = self.held(self.sok, (self.grinder, "3"))  # $156 → $1,536
        with self.assertRaises(CreditLimitError):
            complete_invoice(over, [T(CREDIT, "156")], self.admin)

    def test_a_credit_sale_is_due_after_the_customer_terms(self):
        invoice = self.sell(self.sok, (self.drill, "1"), tenders=[T(CREDIT, "69")])
        self.assertEqual(invoice.due_date, timezone.localdate() + timedelta(days=30))
        self.assertEqual((invoice.paid_now, invoice.on_credit), (D("0.00"), D("69.00")))


class QuoteInvoiceTests(SalesTestCase):
    def test_an_accepted_quote_is_invoiced_in_parts_and_closes_when_taken(self):
        q = self.quote(self.sok, (self.drill, "10"))
        ql = q.lines.get()
        self.sell(self.sok, (ql, "4"), quotation=q)
        ql.refresh_from_db()
        q.refresh_from_db()
        self.assertEqual((ql.qty_invoiced, q.status), (D("4"), QuoteStatus.ACCEPTED))
        self.sell(self.sok, (ql, "6"), quotation=q)
        q.refresh_from_db()
        self.assertEqual(q.status, QuoteStatus.INVOICED)

    def test_the_agreed_price_holds_after_the_list_price_moves(self):
        q = self.quote(self.sok, (self.drill, "2"))
        Product.objects.filter(pk=self.drill.pk).update(wholesale_price=D("75.00"))
        invoice = self.sell(self.sok, (q.lines.get(), "2"), quotation=q)
        self.assertEqual(invoice.total, D("138.00"))

    def test_only_an_accepted_quote_is_invoiced_and_only_what_is_left(self):
        sent = self.quote(self.sok, (self.drill, "2"), accept=False)
        send_quote(sent)
        self.as_seller()
        res = self.client.post("/api/sales/invoices/", {"customer": self.sok.pk, "quotation": sent.pk}, format="json")
        self.assertEqual(res.status_code, 400)

        q = self.quote(self.sok, (self.drill, "2"))
        invoice = self.held(self.sok, (q.lines.get(), "3"), quotation=q)
        with self.assertRaises(DomainError):
            complete_invoice(invoice, [T(CASH, "500")], self.seller)

    def test_an_expired_quote_warns_but_still_invoices(self):
        q = self.quote(self.sok, (self.drill, "1"))
        Quotation.objects.filter(pk=q.pk).update(valid_until=timezone.localdate() - timedelta(days=1))
        q.refresh_from_db()
        self.assertTrue(q.is_expired)
        self.assertEqual(self.sell(self.sok, (q.lines.get(), "1"), quotation=q).status, "COMPLETED")

    def test_a_void_puts_quantity_back_on_the_quote(self):
        q = self.quote(self.sok, (self.drill, "2"))
        invoice = self.sell(self.sok, (q.lines.get(), "2"), quotation=q)
        void_invoice(invoice, self.seller, "Wrong customer")
        q.refresh_from_db()
        self.assertEqual((q.status, q.lines.get().qty_invoiced), (QuoteStatus.ACCEPTED, D("0")))


class VoidTests(SalesTestCase):
    def void_url(self, invoice):
        return f"/api/sales/invoices/{invoice.pk}/void/"

    def test_a_seller_voids_their_own_sale_the_same_day(self):
        invoice = self.sell(self.walk_in, (self.drill, "2"))
        self.receive(self.drill, "10", "60.00")  # the average moves on
        self.as_seller()
        res = self.client.post(self.void_url(invoice), {"reason": "Rung up twice"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data["status"], res.data["number"]), ("VOID", first_invoice_number()))
        self.assertEqual(self.on_hand(self.drill), D("30"))
        back = StockMovement.objects.filter(doc_number=first_invoice_number(), is_reversal=True).get()
        self.assertEqual(back.unit_cost, D("52.40"))  # at the cost it left at

    def test_a_seller_cannot_void_another_sellers_or_yesterdays_sale(self):
        theirs = self.sell(self.walk_in, (self.drill, "1"), user=self.other_seller)
        mine = self.sell(self.walk_in, (self.drill, "1"))
        Invoice.objects.filter(pk=mine.pk).update(sale_date=timezone.now() - timedelta(days=1))
        self.as_seller()
        for invoice in (theirs, mine):
            res = self.client.post(self.void_url(invoice), {"reason": "x"}, format="json")
            self.assertEqual(res.status_code, 403, invoice.number)

    def test_an_admin_voids_any_and_a_reason_is_required(self):
        invoice = self.sell(self.walk_in, (self.drill, "1"), user=self.other_seller)
        Invoice.objects.filter(pk=invoice.pk).update(sale_date=timezone.now() - timedelta(days=3))
        self.as_admin()
        self.assertEqual(self.client.post(self.void_url(invoice), {}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.void_url(invoice), {"reason": "Duplicate"}, format="json").status_code, 200)

    def test_no_void_once_paid_against_or_returned(self):
        paid = self.sell(self.sok, (self.drill, "1"), tenders=[T(CREDIT, "69")])
        record_payment(customer=self.sok, payment_date=timezone.localdate(), tender="CASH",
                       currency="USD", amount_tendered=D("69"), user=self.seller)
        with self.assertRaises(DomainError):
            void_invoice(paid, self.admin, "x")

        returned = self.sell(self.walk_in, (self.drill, "1"))
        ret = SalesReturn.objects.create(invoice=returned, reason="Wrong size", refund_method="CASH")
        ReturnLine(sales_return=ret, invoice_line=returned.lines.get(), quantity=D("1")).save()
        with self.assertRaises(DomainError):
            void_invoice(returned, self.admin, "x")


class PaymentTests(SalesTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.sell(self.sok, (self.drill, "2"), tenders=[T(CASH, "68"), T(CREDIT, "70")])
        self.second = self.sell(self.sok, (self.grinder, "2"), tenders=[T(CREDIT, "104")])

    def test_balance_is_credit_less_payments(self):
        self.assertEqual(customer_balance(self.sok), D("174.00"))
        record_payment(customer=self.sok, payment_date=timezone.localdate(), tender="CASH",
                       currency="USD", amount_tendered=D("100"), user=self.seller)
        self.assertEqual(customer_balance(self.sok), D("74.00"))

    def test_left_out_allocations_go_to_the_oldest_first(self):
        payment = record_payment(customer=self.sok, payment_date=timezone.localdate(), tender="CASH",
                                 currency="USD", amount_tendered=D("100"), user=self.seller)
        applied = dict(payment.allocations.values_list("invoice__number", "amount"))
        self.assertEqual(applied, {self.first.number: D("70.00"), self.second.number: D("30.00")})

    def test_a_payment_never_sits_unallocated(self):
        self.as_seller()
        url = "/api/sales/payments/"
        base = {"customer": self.sok.pk, "tender": "CASH", "currency": "USD"}
        # More than is owed.
        self.assertEqual(self.client.post(url, {**base, "amount_tendered": "200"}, format="json").status_code, 400)
        # Applied amounts that do not add up.
        res = self.client.post(url, {**base, "amount_tendered": "50",
                                     "allocations": [{"invoice": self.first.pk, "amount": "40"}]}, format="json")
        self.assertEqual(res.status_code, 400)
        # More than one invoice owes.
        res = self.client.post(url, {**base, "amount_tendered": "80",
                                     "allocations": [{"invoice": self.first.pk, "amount": "80"}]}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.post(url, {**base, "amount_tendered": "70",
                                     "allocations": [{"invoice": self.first.pk, "amount": "70"}]}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["number"], "PAY-000001")

    def test_a_riel_payment_stamps_its_rate(self):
        payment = record_payment(customer=self.sok, payment_date=timezone.localdate(), tender="CASH",
                                 currency="KHR", amount_tendered=D("287000"), user=self.seller)
        self.assertEqual((payment.amount, payment.exchange_rate), (D("70.00"), RATE))

    def test_only_an_admin_voids_a_payment_and_the_invoices_reopen(self):
        payment = record_payment(customer=self.sok, payment_date=timezone.localdate(), tender="CASH",
                                 currency="USD", amount_tendered=D("174"), user=self.seller)
        self.assertEqual(customer_balance(self.sok), D("0"))
        url = f"/api/sales/payments/{payment.pk}/void/"
        self.as_seller()
        self.assertEqual(self.client.post(url, {"reason": "Bounced"}, format="json").status_code, 403)
        self.as_admin()
        self.assertEqual(self.client.post(url, {"reason": "Bounced"}, format="json").status_code, 200)
        self.assertEqual(customer_balance(self.sok), D("174.00"))

    def test_the_account_shows_balance_room_and_open_invoices(self):
        self.as_seller()
        res = self.client.get(f"/api/sales/customers/{self.sok.pk}/account/")
        self.assertEqual((res.data["balance"], res.data["room_left"]), ("174.00", "1326.00"))
        self.assertEqual([i["number"] for i in res.data["open_invoices"]], [self.first.number, self.second.number])


class ReturnTests(SalesTestCase):
    def make_return(self, invoice, *lines, refund_method="CASH"):
        ret = SalesReturn.objects.create(invoice=invoice, reason="Customer changed mind", refund_method=refund_method)
        for invoice_line, qty, *fit in lines:
            ReturnLine(sales_return=ret, invoice_line=invoice_line, quantity=D(qty),
                       fit_to_sell=fit[0] if fit else True).save()
        return ret

    def test_fit_goods_come_back_at_the_cost_they_left_at(self):
        invoice = self.sell(self.walk_in, (self.drill, "2"))
        self.receive(self.drill, "18", "60.00")  # average now (18×52.40 + 18×60) / 36 = 56.20
        ret = post_return(self.make_return(invoice, (invoice.lines.get(), "1")), self.seller)
        movement = StockMovement.objects.get(doc_number=ret.number)
        self.assertEqual((movement.quantity, movement.unit_cost), (D("1"), D("52.40")))
        self.assertEqual((ret.total, ret.refunded), (D("78.00"), D("78.00")))

    def test_a_faulty_item_does_not_go_back_into_stock(self):
        invoice = self.sell(self.walk_in, (self.grinder, "1"))
        post_return(self.make_return(invoice, (invoice.lines.get(), "1", False)), self.seller)
        self.assertEqual(self.on_hand(self.grinder), D("4"))

    def test_returns_are_capped_at_what_was_sold(self):
        invoice = self.sell(self.walk_in, (self.drill, "2"))
        line = invoice.lines.get()
        post_return(self.make_return(invoice, (line, "1")), self.seller)
        with self.assertRaises(DomainError):
            post_return(self.make_return(invoice, (line, "2")), self.seller)
        self.as_seller()
        res = self.client.post("/api/sales/returns/", {
            "invoice": invoice.pk, "reason": "x", "refund_method": "CASH",
            "lines": [{"invoice_line": line.pk, "quantity": "2"}],
        }, format="json")
        self.assertEqual(res.status_code, 400)

    def test_the_balance_is_reduced_first_and_the_rest_refunded(self):
        invoice = self.sell(self.sok, (self.drill, "2"), tenders=[T(CASH, "68"), T(CREDIT, "70")])
        ret = post_return(self.make_return(invoice, (invoice.lines.get(), "2")), self.seller)
        self.assertEqual((ret.total, ret.credited, ret.refunded), (D("138.00"), D("70.00"), D("68.00")))
        self.assertEqual(customer_balance(self.sok), D("0"))

    def test_a_refund_needs_a_method_but_a_pure_credit_does_not(self):
        cash_sale = self.sell(self.walk_in, (self.drill, "1"))
        with self.assertRaises(DomainError):
            post_return(self.make_return(cash_sale, (cash_sale.lines.get(), "1"), refund_method=""), self.seller)

        credit_sale = self.sell(self.sok, (self.drill, "1"), tenders=[T(CREDIT, "69")])
        ret = post_return(self.make_return(credit_sale, (credit_sale.lines.get(), "1"), refund_method=""), self.seller)
        self.assertEqual((ret.credited, ret.refunded), (D("69.00"), D("0.00")))

    def test_a_posted_return_is_frozen(self):
        invoice = self.sell(self.walk_in, (self.drill, "1"))
        ret = post_return(self.make_return(invoice, (invoice.lines.get(), "1")), self.seller)
        with self.assertRaises(PostedDocumentError):
            ret.save()


class ScopeTests(SalesTestCase):
    def test_cost_is_an_admin_scope(self):
        from users.scopes import has_scope

        self.assertTrue(has_scope(self.admin, "cost.view"))
        self.assertFalse(has_scope(self.seller, "cost.view"))
