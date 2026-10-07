# v1.0.0 — sample selling for the screens that had little: two store
# customers, quotations, sales (completed, voided, held), customer payments,
# returns, warranty claims and a payment note — two of each. For development
# and demos, like seed_demo and seed_stock_demo — never the live shop. Run it
# after them.
#
# Each document goes through sales/services.py, the code the screens post
# through, in date order and as of its own moment: the clock is set to that
# moment in shop hours, so the number (INV-20260902001), the rate, the due
# date, the stock movement and every stamp are what entering it then would
# have made. A daily number starts after what its day already holds, and both
# daily counters are put back as they were at the end, so the next real sale
# takes the number it would have taken anyway.
#
# A back-dated sale takes, for each line, the first of its products that has
# not moved since that day, so every product's ledger stays in date order. The
# days count back from today. Where no rate is held that far back, one is
# added at the earliest held rate (owner's choice, 2026-10-07).
#
#   manage.py seed_sales_demo --admin Piseth --seller Heng
#
# It refuses to run twice. A completed sale cannot be deleted, so going back
# means restoring a dump taken before.
import calendar
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest import mock

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from catalogue.models import Product
from company.models import DocumentCounter, DocumentType, ExchangeRate, PaymentNote
from partners.models import Customer, PriceTier
from sales.models import Invoice, InvoiceLine, Quotation, QuotationLine, ReturnLine, SalesReturn
from sales.money import CASH, CREDIT, Tender
from sales.services import (
    accept_quote,
    complete_invoice,
    post_return,
    record_payment,
    send_quote,
    void_invoice,
)
from users.models import Role, User
from warranty.models import ClaimStatus, WarrantyClaim

NOTE = "Sample data (seed_sales_demo)."

SOK_HENG = {
    "name": "Sok Heng Construction", "price_tier": PriceTier.WHOLESALE,
    "allow_credit": True, "credit_limit": Decimal("2000.00"), "payment_terms_days": 30,
    "contact_person": "Sok Heng", "phone": "012 345 118",
    "address": "No 45, Street 371", "district": "Khan Mean Chey", "province": "Phnom Penh",
}
DARA = {
    "name": "Dara Home Repair", "price_tier": PriceTier.WHOLESALE,
    "allow_credit": True, "credit_limit": Decimal("500.00"), "payment_terms_days": 15,
    "contact_person": "Dara Kim", "phone": "098 220 517",
    "address": "No 12B, Street 598", "district": "Khan Russey Keo", "province": "Phnom Penh",
}

# Each line: the products it may take, in order of choice, and the quantity.
# The first choices last moved at go-live, so they stay free on any day.
SOK_HENG_ORDER = [  # quoted, then invoiced on credit
    (("TL-0132", "TL-0119", "TL-0140"), 1),  # a power tool, claimed under warranty later
    (("TL-0131", "TL-0115"), 2),  # one comes back
    (("FX-0310", "FX-0301"), 20),
    (("TL-0250", "TL-0252"), 6),
]
DARA_SALE = [
    (("TL-0260", "TL-0291"), 2),
    (("TL-0270", "TL-0271"), 3),
    (("TL-0282", "TL-0281"), 1),  # comes back
    (("TL-0244", "TL-0245"), 2),
]
RUNG_TWICE = [(("TL-0263", "TL-0238"), 5)]
WRONG_CUSTOMER = [(("TL-0252", "TL-0250"), 2), (("AC-0510", "FX-0301"), 10)]
DARA_QUOTE = [(("TL-0119", "TL-0115"), 1), (("AC-0510", "AC-0512"), 20)]
HELD_WALK_IN = [(("TL-0281", "TL-0282"), 1), (("TL-0271", "TL-0270"), 1)]
HELD_DARA = [(("FX-0301", "FX-0310"), 5)]


def add_months(day, months):
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


class Command(BaseCommand):
    help = "Load sample customers, quotations, sales, payments, returns and claims (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--admin", required=True, help="The Admin who sells, voids and records.")
        parser.add_argument("--seller", required=True, help="The Seller who sells, voids and records.")

    def handle(self, *args, **options):
        self.admin = self.user(options["admin"], Role.ADMIN)
        self.seller = self.user(options["seller"], Role.SELLER)
        if Customer.objects.filter(name__in=[SOK_HENG["name"], DARA["name"]]).exists():
            raise CommandError("The sample sales are already loaded. Restore a dump to start again.")
        self.today = timezone.localdate()
        with transaction.atomic():
            counters = list(DocumentCounter.objects.filter(doc_type__in=DocumentCounter.DAILY))
            self.load()
            for counter in counters:  # as they were: the next real number is untouched
                DocumentCounter.objects.filter(pk=counter.pk).update(
                    number_date=counter.number_date, next_number=counter.next_number
                )
        self.stdout.write(self.style.SUCCESS("Sample sales loaded."))

    def user(self, username, role):
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"No user {username}.")
        if user.role != role:
            raise CommandError(f"{username} is not a {role.label}.")
        return user

    def at(self, days_back, hour, minute):
        """That moment in shop hours, `days_back` days before today."""
        day = self.today - timedelta(days=days_back)
        return timezone.make_aware(datetime.combine(day, time(hour, minute)))

    @contextmanager
    def entered_at(self, moment):
        """Everything inside reads `moment` from the clock. A quotation or
        invoice numbered then follows on from what that day already holds."""
        day = timezone.localtime(moment).date()
        for counter in DocumentCounter.objects.filter(doc_type__in=DocumentCounter.DAILY):
            model = Quotation if counter.doc_type == DocumentType.QUOTATION else Invoice
            stem = f"{counter.prefix}{day:%Y%m%d}"
            taken = model.objects.filter(number__startswith=stem).values_list("number", flat=True)
            last = max((int(n[len(stem):]) for n in taken), default=0)
            DocumentCounter.objects.filter(pk=counter.pk).update(number_date=day, next_number=last + 1)
        with mock.patch("django.utils.timezone.now", return_value=moment):
            yield

    def pick(self, lines, moving_on=None):
        """For each line, the first product on hand that is free to use: when
        the document moves stock on `moving_on`, one not moved since that day."""
        picked = []
        for codes, qty in lines:
            for code in codes:
                product = Product.objects.filter(code=code, is_active=True).first()
                if product is None or product in [p for p, _ in picked]:
                    continue
                if moving_on and (
                    product.qty_on_hand < qty
                    or product.movements.filter(movement_date__gt=moving_on).exists()
                ):
                    continue
                picked.append((product, Decimal(qty)))
                break
            else:
                raise CommandError(
                    f"None of {', '.join(codes)} is free to use. Run seed_demo and seed_stock_demo first."
                )
        return picked

    def held(self, customer, lines, user, quotation=None, **fields):
        invoice = Invoice.objects.create(customer=customer, quotation=quotation, created_by=user, **fields)
        for item, qty in lines:
            if isinstance(item, QuotationLine):
                InvoiceLine(invoice=invoice, quote_line=item, product=item.product, quantity=qty).save()
            else:
                InvoiceLine(invoice=invoice, product=item, quantity=qty).save()
        return invoice

    def sell(self, moment, customer, lines, user, tenders, **fields):
        day = timezone.localtime(moment).date()
        with self.entered_at(moment):
            invoice = self.held(customer, self.pick(lines, moving_on=day), user, **fields)
            total = sum((line.line_total for line in invoice.lines.all()), Decimal("0"))
            return complete_invoice(invoice, tenders(total), user)

    def give_back(self, moment, invoice, product, qty, reason, refund_method, user):
        with self.entered_at(moment):
            ret = SalesReturn.objects.create(
                invoice=invoice, reason=reason, refund_method=refund_method, created_by=user
            )
            line = invoice.lines.get(product=product)
            ReturnLine(sales_return=ret, invoice_line=line, quantity=Decimal(qty), fit_to_sell=True).save()
            return post_return(ret, user)

    def load(self):
        admin, seller = self.admin, self.seller
        walk_in = Customer.walk_in()

        # Five weeks back: the customers, a rate that far back, and a quotation.
        start = self.at(36, 8, 40)
        with self.entered_at(start):
            first_day = start.date()
            if not ExchangeRate.objects.filter(effective_date__lte=first_day).exists():
                earliest = ExchangeRate.objects.order_by("effective_date").first()
                ExchangeRate.objects.create(
                    effective_date=first_day, rate=earliest.rate if earliest else Decimal("4100"),
                    note=NOTE, created_by=admin,
                )
            sok = Customer.objects.create(**SOK_HENG, notes=NOTE, created_by=admin)
            dara = Customer.objects.create(**DARA, notes=NOTE, created_by=admin)

        sale_day = self.at(35, 9, 20)
        with self.entered_at(self.at(36, 10, 15)):
            quote = Quotation.objects.create(
                customer=sok, created_by=admin,
                terms="Prices held for 30 days. Delivery to site in Khan Mean Chey included.",
            )
            for product, qty in self.pick(SOK_HENG_ORDER, moving_on=sale_day.date()):
                QuotationLine(quotation=quote, product=product, quantity=qty).save()
            send_quote(quote)
        with self.entered_at(self.at(35, 9, 5)):
            accept_quote(quote)
        with self.entered_at(sale_day):
            invoice = self.held(sok, [(l, l.quantity) for l in quote.lines.all()], admin, quotation=quote)
            total = sum((line.line_total for line in invoice.lines.all()), Decimal("0"))
            order = complete_invoice(invoice, [Tender(kind=CREDIT, currency="USD", amount=total)], admin)

        with self.entered_at(self.at(17, 15, 30)):
            record_payment(
                customer=sok, payment_date=timezone.localdate(), tender="CASH", currency="KHR",
                amount_tendered=Decimal("400000"), note="Part payment, brought to the counter.",
                user=seller,
            )
        returned_tool = order.lines.order_by("id")[1].product
        self.give_back(
            self.at(15, 10, 10), order, returned_tool, "1",
            "Ordered one too many; unused, still in its box.", "", admin,
        )

        tool = order.lines.order_by("id")[0].product
        sold = timezone.localtime(order.sale_date).date()
        with self.entered_at(self.at(12, 11, 45)):
            WarrantyClaim.objects.create(
                warranty_number=f"WC-{sold:%y%m%d}-01", product_code=tool.code,
                product_name=tool.name, warranty_months=tool.warranty_months or 6,
                expiry_date=add_months(sold, tool.warranty_months or 6),
                customer_name=sok.name, customer_phone=sok.phone,
                note="Stops under load. Sent to the supplier for repair.",
                status=ClaimStatus.SENT_FOR_REPAIR, created_by=seller,
            )

        # The last week: Dara's sale, part on credit, paid off, one item back.
        dara_sale = self.sell(
            self.at(6, 14, 20), dara, DARA_SALE, seller,
            lambda total: [Tender(kind=CASH, currency="USD", amount=Decimal("20.00")),
                           Tender(kind=CREDIT, currency="USD", amount=total - Decimal("20.00"))],
        )
        with self.entered_at(self.at(4, 9, 50)):
            record_payment(
                customer=dara, payment_date=timezone.localdate(), tender="KHQR", currency="USD",
                amount_tendered=dara_sale.on_credit, reference="ABA 7731", user=admin,
            )
        twice = self.sell(
            self.at(4, 16, 5), walk_in, RUNG_TWICE, seller,
            lambda total: [Tender(kind=CASH, currency="USD", amount=total)],
        )
        with self.entered_at(self.at(4, 16, 12)):
            void_invoice(twice, seller, "Rung up twice.")
        with self.entered_at(self.at(3, 13, 30)):
            ended = self.today - timedelta(days=36)
            grinder = Product.objects.filter(code="TL-0115").first()
            WarrantyClaim.objects.create(
                warranty_number=f"WC-{add_months(ended, -6):%y%m%d}-07",
                product_code=grinder.code if grinder else "",
                product_name=grinder.name if grinder else "Angle grinder 100mm 750W",
                warranty_months=6, expiry_date=ended,
                customer_name="Mr Sophea", customer_phone="017 604 228",
                note="Motor burnt out. Out of warranty: told the repair is charged.",
                status=ClaimStatus.RECEIVED, created_by=seller,
            )
        back = dara_sale.lines.order_by("id")[2].product
        self.give_back(
            self.at(2, 10, 40), dara_sale, back, "1",
            "Already had one; unused.", "CASH", seller,
        )

        wrong = self.sell(
            self.at(1, 11, 15), walk_in, WRONG_CUSTOMER, admin,
            lambda total: [Tender(kind=CASH, currency="USD", amount=total)],
        )
        with self.entered_at(self.at(1, 11, 25)):
            void_invoice(wrong, admin, f"Wrong customer: it goes on {sok.name}'s account.")
        with self.entered_at(self.at(1, 15, 0)):
            offer = Quotation.objects.create(customer=dara, created_by=seller)
            for product, qty in self.pick(DARA_QUOTE):
                QuotationLine(quotation=offer, product=product, quantity=qty).save()
            send_quote(offer)

        # Today: two sales waiting at the till, and a payment note.
        self.held(walk_in, self.pick(HELD_WALK_IN), seller,
                  walk_in_name="Mr Vuthy", hold_label="Mr Vuthy, back after lunch")
        self.held(dara, self.pick(HELD_DARA), seller, hold_label="Dara, waiting for their van")
        PaymentNote.objects.create(
            payment_type="Cash at the counter",
            payment_info="Pay in US dollars or riel at the counter.",
            row_order=PaymentNote.objects.count() + 1, created_by=admin,
        )
