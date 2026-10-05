# v1.0.0 — rebuild qty_on_hand and avg_cost from the stock ledger.
#
# They are cached projections of StockMovement and will drift if anything ever
# writes them outside inventory/services.py. --check reports without writing
# and exits non-zero on drift, so it can run on a schedule.
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from catalogue.models import Product
from inventory.services import next_position


class Command(BaseCommand):
    help = "Rebuild product stock and average cost from the ledger."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="Report drift; change nothing.")
        parser.add_argument("--product", help="Only this product code.")

    def handle(self, *args, **options):
        products = Product.objects.filter(
            Q(track_stock=True) | Q(movements__isnull=False)
        ).distinct().order_by("code")
        if options["product"]:
            products = products.filter(code=options["product"])

        drifted = 0
        for product_id in products.values_list("pk", flat=True):
            with transaction.atomic():
                product = Product.objects.select_for_update().get(pk=product_id)
                qty, avg = Decimal("0"), Decimal("0")
                for quantity, value in product.movements.order_by("id").values_list("quantity", "value"):
                    qty, avg = next_position(qty, avg, quantity, value)
                if qty == product.qty_on_hand and avg == product.avg_cost:
                    continue
                drifted += 1
                self.stdout.write(
                    f"{product.code}: held {product.qty_on_hand} @ {product.avg_cost}, "
                    f"ledger says {qty} @ {avg}"
                )
                if not options["check"]:
                    Product.objects.filter(pk=product.pk).update(qty_on_hand=qty, avg_cost=avg)

        if options["check"]:
            if drifted:
                raise CommandError(f"{drifted} product(s) differ from the ledger.")
            self.stdout.write(self.style.SUCCESS("Every product agrees with the ledger."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Recomputed. {drifted} product(s) corrected."))
