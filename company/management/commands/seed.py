# v1.0.1 — the rows the system assumes exist.
#
# Several rules depend on these: there must be an exchange rate before anything
# can be priced in riel, and a document counter before anything can be numbered.
# Safe to run more than once.
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from catalogue.models import Unit
from company.models import CompanyProfile, DocumentCounter, DocumentType, ExchangeRate
from partners.models import Customer, PriceTier

PREFIXES = {
    DocumentType.QUOTATION: "QUO-",
    DocumentType.INVOICE: "INV-",
    DocumentType.PAYMENT: "PAY-",
    DocumentType.RETURN: "RTN-",
    DocumentType.STOCK_IN: "GRN-",
    DocumentType.ADJUSTMENT: "ADJ-",
    DocumentType.COUNT: "CNT-",
    DocumentType.WARRANTY: "WRC-",
    DocumentType.CUSTOMER: "CUS-",
    DocumentType.SUPPLIER: "SUP-",
}

UNITS = [
    ("PCS", "Piece"),
    ("SET", "Set"),
    ("BOX", "Box"),
    ("PAIR", "Pair"),
    ("M", "Metre"),
    ("ROLL", "Roll"),
    ("CTN", "Carton"),
]


class Command(BaseCommand):
    help = "Create the rows the system assumes exist. Safe to run again."

    def add_arguments(self, parser):
        parser.add_argument(
            "--rate", type=str, default="4100",
            help="Opening riel per US dollar (default 4100).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        profile = CompanyProfile.get()
        self.stdout.write(f"Company profile: {profile.name}")

        made = 0
        for doc_type, prefix in PREFIXES.items():
            _, created = DocumentCounter.objects.get_or_create(
                doc_type=doc_type, defaults={"prefix": prefix, "next_number": 1}
            )
            made += int(created)
        self.stdout.write(f"Document counters: {made} created, {len(PREFIXES)} total")

        made = 0
        for order, (code, name) in enumerate(UNITS, start=1):
            _, created = Unit.objects.get_or_create(
                code=code, defaults={"name": name, "display_order": order}
            )
            made += int(created)
        self.stdout.write(f"Units: {made} created, {len(UNITS)} total")

        if ExchangeRate.objects.exists():
            self.stdout.write("Exchange rate: already set, left alone")
        else:
            ExchangeRate.objects.create(
                effective_date=date.today(),
                rate=Decimal(options["rate"]),
                note="Opening rate, created by seed",
            )
            self.stdout.write(f"Exchange rate: {options['rate']} riel per USD")

        # Every sale has a customer; a sale with no named one is recorded
        # against this row. Number 0, so the counter starts real codes at 1.
        _, created = Customer.objects.get_or_create(
            is_system=True,
            defaults={
                "code": "CUS-000000",
                "name": "Walk-in customer",
                "short_name": "Walk-in",
                "price_tier": PriceTier.RETAIL,
                "display_order": 0,
            },
        )
        self.stdout.write(
            "Walk-in customer: " + ("created" if created else "already there")
        )

        self.stdout.write(self.style.SUCCESS("Seed complete."))
