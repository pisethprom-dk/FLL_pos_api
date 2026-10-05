# v1.0.1
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from core.models import ActivatableModel, TimeStampedModel


class Category(TimeStampedModel, ActivatableModel):
    """Two levels only — a group and its sub-groups.

    Unlimited depth looks flexible and turns into recursive queries and a tree
    nobody maintains. If two levels is ever not enough, that is a migration.
    """

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    name_kh = models.CharField(max_length=100, blank=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    description = models.CharField(max_length=250, blank=True)
    image = models.ImageField(upload_to="categories/", null=True, blank=True)
    display_order = models.IntegerField(default=1)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["display_order", "name"]

    def __str__(self):
        return f"{self.parent.name} → {self.name}" if self.parent_id else self.name

    @property
    def full_name(self):
        return str(self)

    @property
    def is_top_level(self):
        return self.parent_id is None

    def clean(self):
        if self.parent_id:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "A category cannot be its own parent."})
            if self.parent.parent_id is not None:
                raise ValidationError(
                    {"parent": "Only a top-level category can be a parent — two levels is the limit."}
                )
        if self.pk and self.parent_id and self.children.exists():
            raise ValidationError(
                {"parent": "This category has sub-categories, so it must stay top level."}
            )


class Brand(TimeStampedModel, ActivatableModel):
    """A record of its own because customers ask for tools by brand.

    A free-text field on Product would give you Makita, makita and MAKITA
    within a month.
    """

    name = models.CharField(max_length=100, unique=True)
    name_kh = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=60, blank=True)
    logo = models.ImageField(upload_to="brands/", null=True, blank=True)
    display_order = models.IntegerField(default=1)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name


class Unit(TimeStampedModel, ActivatableModel):
    """Piece, set, box of 100, metre. A tool shop needs more than 'each'."""

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=50)
    name_kh = models.CharField(max_length=50, blank=True)
    display_order = models.IntegerField(default=1)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name


class Product(TimeStampedModel, ActivatableModel):
    """What the shop sells.

    qty_on_hand and avg_cost are projections of the stock movement ledger, not
    fields anyone types. They are written only by posting a document, inside
    the same transaction as the movement. A recompute command rebuilds them
    from the ledger when they drift.

    No variants: a 10mm spanner and a 12mm spanner are two products. Variant
    systems earn their place in clothing retail and almost nowhere else.
    """

    # --- identity ---
    code = models.CharField(max_length=30, unique=True)
    barcode = models.CharField(
        max_length=30, unique=True, null=True, blank=True,
        help_text="Leave empty if the product has none.",
    )
    name = models.CharField(max_length=200)
    name_kh = models.CharField(max_length=200, blank=True)
    short_name = models.CharField(
        max_length=60, blank=True, help_text="Must fit one receipt line."
    )
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    brand = models.ForeignKey(
        Brand, null=True, blank=True, on_delete=models.PROTECT, related_name="products"
    )
    model_no = models.CharField(max_length=60, blank=True)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="products/", null=True, blank=True)

    # --- selling ---
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="products")
    retail_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    wholesale_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    is_price_fixed = models.BooleanField(
        default=False, help_text="Blocks line discounting at the till."
    )

    # --- stock ---
    track_stock = models.BooleanField(
        default=True, help_text="Off for services and labour, which have no quantity."
    )
    qty_on_hand = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"), editable=False
    )
    avg_cost = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("0.0000"), editable=False
    )
    reorder_level = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    reorder_qty = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    shelf_location = models.CharField(
        max_length=20, blank=True, db_index=True,
        help_text="Where it sits in the shop, e.g. A3-2. Count sheets sort by it.",
    )

    # --- tool details ---
    warranty_months = models.PositiveIntegerField(
        default=0, help_text="Zero means none. Printed on the receipt."
    )
    origin_country = models.CharField(max_length=60, blank=True)

    notes = models.TextField(blank=True)
    display_order = models.IntegerField(default=1)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(retail_price__gte=0) & models.Q(wholesale_price__gte=0),
                name="product_prices_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(reorder_level__gte=0) & models.Q(reorder_qty__gte=0),
                name="product_reorder_not_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["category", "brand"]),
            models.Index(fields=["is_active", "track_stock"]),
        ]

    def __str__(self):
        return f"{self.code} — {self.name}"

    def clean(self):
        if self.barcode == "":
            # Empty must be NULL, or the second blank row breaks the unique index.
            self.barcode = None

    def save(self, *args, **kwargs):
        if self.barcode == "":
            self.barcode = None
        if not self.short_name:
            self.short_name = self.name[:60]
        if self.shelf_location:
            self.shelf_location = self.shelf_location.upper()
        super().save(*args, **kwargs)

    @property
    def stock_value(self):
        return (self.qty_on_hand * self.avg_cost) if self.track_stock else Decimal("0")

    @property
    def needs_reorder(self):
        return self.track_stock and self.qty_on_hand <= self.reorder_level

    @property
    def is_out_of_stock(self):
        return self.track_stock and self.qty_on_hand <= 0

    def price_for(self, tier):
        """tier is 'RETAIL' or 'WHOLESALE' — stamped on the sale, never looked
        up again afterwards."""
        return self.wholesale_price if tier == "WHOLESALE" else self.retail_price
