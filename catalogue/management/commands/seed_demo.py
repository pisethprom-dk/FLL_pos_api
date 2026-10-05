# v1.0.0 — demo catalogue for a tool shop: units, brands, categories, products.
#
# Separate from `seed`, which makes only the rows the system needs. This is
# sample data for development and demos — do not run it on the live shop.
#
# Safe to run again: a row whose code (or brand name) already exists is left
# alone, so edits made since are kept. The one exception is a blank Khmer name
# on a unit, which is filled in.
#
# Stock is not touched. qty_on_hand and avg_cost belong to the movement ledger
# and start at zero; an opening-balance stock-in sets them once inventory is built.
import re
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from catalogue.demo_data import BRANDS, CATEGORIES, PRODUCTS, UNITS
from catalogue.models import Brand, Category, Product, Unit


def in_store_ean13(number):
    """A valid EAN-13 in the 200 range, which GS1 keeps for in-store use, so a
    demo barcode can never clash with a real product's."""
    body = f"200{number:09d}"
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
    return body + str((10 - total % 10) % 10)


class Command(BaseCommand):
    help = "Load a demo tool-shop catalogue. Safe to run again."

    @transaction.atomic
    def handle(self, *args, **options):
        units = self._units()
        brands = self._brands()
        categories = self._categories()

        made = 0
        total = 0
        for category_code, rows in PRODUCTS.items():
            for row in rows:
                total += 1
                if Product.objects.filter(code=row.code).exists():
                    continue
                product = Product(
                    code=row.code,
                    barcode=(
                        in_store_ean13(int(re.sub(r"\D", "", row.code)))
                        if row.track else None
                    ),
                    name=row.name,
                    category=categories[category_code],
                    brand=brands[row.brand] if row.brand else None,
                    model_no=row.model_no,
                    unit=units[row.unit],
                    retail_price=Decimal(row.retail),
                    wholesale_price=Decimal(row.wholesale),
                    is_price_fixed=row.fixed,
                    track_stock=row.track,
                    reorder_level=Decimal(row.reorder),
                    reorder_qty=Decimal(row.reorder_qty),
                    shelf_location=row.shelf,
                    warranty_months=row.warranty,
                )
                product.full_clean()
                product.save()
                made += 1
        self.stdout.write(f"Products: {made} created, {total} in the demo set")
        self.stdout.write(self.style.SUCCESS("Demo catalogue loaded."))

    def _units(self):
        made = 0
        by_code = {}
        for order, (code, name, name_kh) in enumerate(UNITS, start=1):
            unit, created = Unit.objects.get_or_create(
                code=code,
                defaults={"name": name, "name_kh": name_kh, "display_order": order},
            )
            if not created and not unit.name_kh:
                unit.name_kh = name_kh
                unit.save(update_fields=["name_kh"])
            made += int(created)
            by_code[code] = unit
        self.stdout.write(f"Units: {made} created, {len(UNITS)} in the demo set")
        return by_code

    def _brands(self):
        made = 0
        by_name = {}
        for name, country, order in BRANDS:
            brand, created = Brand.objects.get_or_create(
                name=name, defaults={"country": country, "display_order": order}
            )
            made += int(created)
            by_name[name] = brand
        self.stdout.write(f"Brands: {made} created, {len(BRANDS)} in the demo set")
        return by_name

    def _categories(self):
        made = 0
        by_code = {}
        for code, name, name_kh, parent_code, order in CATEGORIES:
            category = Category.objects.filter(code=code).first()
            if category is None:
                category = Category(
                    code=code,
                    name=name,
                    name_kh=name_kh,
                    parent=by_code[parent_code] if parent_code else None,
                    display_order=order,
                )
                category.full_clean()  # holds the two-level limit
                category.save()
                made += 1
            by_code[code] = category
        self.stdout.write(
            f"Categories: {made} created, {len(CATEGORIES)} in the demo set"
        )
        return by_code
