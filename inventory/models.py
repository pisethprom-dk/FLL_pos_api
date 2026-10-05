# v1.0.1 — the stock movement ledger and the three stock documents.
#
# Stock and cost move only when a document is posted (inventory/services.py).
# A draft changes nothing; a posted document is frozen and corrected only by a
# reversing document of the same type.
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from catalogue.models import Category, Product, Unit
from company.models import DocumentType
from company.services import next_document_number
from core.exceptions import DomainError, PostedDocumentError
from core.models import TimeStampedModel
from partners.models import Supplier

TWO = Decimal("0.01")
FOUR = Decimal("0.0001")


class DocStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    POSTED = "POSTED", "Posted"


class AdjustmentReason(models.TextChoices):
    DAMAGE = "DAMAGE", "Damage"
    LOSS = "LOSS", "Loss"
    SHOP_USE = "SHOP_USE", "Shop use"
    WARRANTY_REPLACEMENT = "WARRANTY_REPLACEMENT", "Warranty replacement"
    RETURN_TO_SUPPLIER = "RETURN_TO_SUPPLIER", "Return to supplier"
    SUPPLIER_REPLACEMENT = "SUPPLIER_REPLACEMENT", "Supplier replacement"
    OPENING_BALANCE = "OPENING_BALANCE", "Opening balance"


# The direction of an adjustment is fixed by its reason, never typed per line.
INBOUND_REASONS = {
    AdjustmentReason.SUPPLIER_REPLACEMENT,
    AdjustmentReason.OPENING_BALANCE,
}
SUPPLIER_REASONS = {
    AdjustmentReason.RETURN_TO_SUPPLIER,
    AdjustmentReason.SUPPLIER_REPLACEMENT,
}


class StockMovement(models.Model):
    """One product's change from one posted document line. Append-only.

    `value` is the money that moved, held to six places so that an outbound
    movement at the average (two-place quantity times four-place cost) is exact
    and replays to the same average. qty_after and avg_cost_after make the
    ledger a stock card without re-adding it.
    """

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="movements")
    doc_type = models.CharField(max_length=20, choices=DocumentType.choices)
    doc_id = models.PositiveBigIntegerField()
    doc_number = models.CharField(max_length=30, db_index=True)  # fits a daily invoice number
    line_id = models.PositiveBigIntegerField(null=True, blank=True)
    reason = models.CharField(max_length=25, blank=True, db_index=True)
    is_reversal = models.BooleanField(default=False)

    movement_date = models.DateField(db_index=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4)
    value = models.DecimalField(max_digits=18, decimal_places=6)
    qty_after = models.DecimalField(max_digits=12, decimal_places=2)
    avg_cost_after = models.DecimalField(max_digits=12, decimal_places=4)

    posted_at = models.DateTimeField(auto_now_add=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name="+",
    )

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["product", "id"]),
            models.Index(fields=["doc_type", "doc_id"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(quantity=0), name="movement_quantity_not_zero"
            ),
            models.CheckConstraint(
                condition=models.Q(qty_after__gte=0), name="movement_never_below_zero"
            ),
        ]

    def __str__(self):
        return f"{self.doc_number} {self.product.code} {self.quantity:+}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise DomainError("The stock ledger is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise DomainError("The stock ledger is append-only.")


class StockDocument(TimeStampedModel):
    """What a stock-in, an adjustment and a count share.

    The number is taken when the draft is created, as the mockup shows it on
    drafts, so a deleted draft leaves a gap in the sequence.
    """

    DOC_TYPE = None  # a DocumentType, set by each subclass
    REVERSAL_FIELDS = ()  # header fields a reversing document copies
    LINE_FIELDS = ()  # line fields a reversing document copies

    number = models.CharField(max_length=20, unique=True, editable=False)
    doc_date = models.DateField(default=timezone.localdate)
    note = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=DocStatus.choices, default=DocStatus.DRAFT,
        editable=False, db_index=True,
    )
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
        on_delete=models.PROTECT, related_name="+",
    )
    reverses = models.OneToOneField(
        "self", null=True, blank=True, editable=False,
        on_delete=models.PROTECT, related_name="reversed_by",
    )

    class Meta:
        abstract = True
        ordering = ["-doc_date", "-id"]

    def __str__(self):
        return self.number

    @property
    def is_posted(self):
        return self.status == DocStatus.POSTED

    def was_posted(self):
        """Asks the database, not this instance — the instance may be stale."""
        return bool(self.pk) and type(self).objects.filter(
            pk=self.pk, status=DocStatus.POSTED
        ).exists()

    def save(self, *args, **kwargs):
        if self.was_posted():
            raise PostedDocumentError()
        with transaction.atomic():
            if not self.number:
                self.number = next_document_number(self.DOC_TYPE)
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.was_posted():
            raise PostedDocumentError()
        return super().delete(*args, **kwargs)


class StockLine(models.Model):
    """A line may change only while its document is a draft."""

    class Meta:
        abstract = True
        ordering = ["id"]

    def save(self, *args, **kwargs):
        if self.document.was_posted():
            raise PostedDocumentError()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.document.was_posted():
            raise PostedDocumentError()
        return super().delete(*args, **kwargs)


class StockIn(StockDocument):
    """Goods received from a supplier. Posting recalculates average cost."""

    DOC_TYPE = DocumentType.STOCK_IN
    REVERSAL_FIELDS = ("supplier", "supplier_ref")
    LINE_FIELDS = ("product", "pack_unit", "packs", "pack_size", "pack_cost")

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="stock_ins")
    supplier_ref = models.CharField(
        max_length=50, blank=True,
        help_text="The supplier's invoice or delivery note number.",
    )

    class Meta(StockDocument.Meta):
        verbose_name = "stock in"


class StockInLine(StockLine):
    """Bought as packs, received as units.

    Pack size is stamped here, so a supplier changing their carton later does
    not alter this document. Unit cost is held to four places for display; the
    average is moved by line_total, the money actually paid.
    """

    document = models.ForeignKey(StockIn, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+")
    pack_unit = models.ForeignKey(
        Unit, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Bought as, e.g. Carton. Blank means the product's own unit.",
    )
    packs = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    pack_size = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    pack_cost = models.DecimalField(
        max_digits=12, decimal_places=4, validators=[MinValueValidator(Decimal("0.0001"))]
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2, editable=False)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4, editable=False)
    line_total = models.DecimalField(max_digits=14, decimal_places=2, editable=False)

    class Meta(StockLine.Meta):
        constraints = [
            models.CheckConstraint(
                condition=models.Q(packs__gt=0) & models.Q(pack_size__gt=0)
                & models.Q(pack_cost__gt=0),
                name="stock_in_line_positive",
            ),
        ]

    def save(self, *args, **kwargs):
        self.quantity = (self.packs * self.pack_size).quantize(TWO, ROUND_HALF_UP)
        self.line_total = (self.packs * self.pack_cost).quantize(TWO, ROUND_HALF_UP)
        self.unit_cost = (self.pack_cost / self.pack_size).quantize(FOUR, ROUND_HALF_UP)
        super().save(*args, **kwargs)


class Adjustment(StockDocument):
    """Any change in quantity that is not a purchase, a sale or a count.

    Unit cost is the current average, stamped at posting. Only an opening
    balance types its own, and only for a product the ledger has never moved.
    """

    DOC_TYPE = DocumentType.ADJUSTMENT
    REVERSAL_FIELDS = ("reason", "supplier")
    LINE_FIELDS = ("product", "quantity", "unit_cost", "value")

    reason = models.CharField(max_length=25, choices=AdjustmentReason.choices)
    supplier = models.ForeignKey(
        Supplier, null=True, blank=True, on_delete=models.PROTECT,
        related_name="adjustments",
        help_text="For a return to, or a replacement from, a supplier.",
    )

    class Meta(StockDocument.Meta):
        pass

    @property
    def is_inbound(self):
        return self.reason in INBOUND_REASONS


class AdjustmentLine(StockLine):
    document = models.ForeignKey(Adjustment, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))],
        help_text="How many. Whether they go in or out is set by the reason.",
    )
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        help_text="Typed only for an opening balance; stamped at posting otherwise.",
    )
    value = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, editable=False
    )

    class Meta(StockLine.Meta):
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="adjustment_line_positive"
            ),
        ]


class StockCount(StockDocument):
    """A blind count of one category, sub-categories included."""

    DOC_TYPE = DocumentType.COUNT
    REVERSAL_FIELDS = ("category", "counted_by")
    LINE_FIELDS = (
        "product", "counted_qty", "counted_at", "expected_qty",
        "difference", "unit_cost", "value",
    )

    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="+")
    counted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name="+",
    )

    class Meta(StockDocument.Meta):
        verbose_name = "stock count"


class StockCountLine(StockLine):
    """expected_qty is what the system held at the moment counted_qty was
    entered, so sales made while the count is open do not show up as
    differences. A blank counted_qty is skipped at posting, never zeroed."""

    document = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+")
    counted_qty = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    counted_at = models.DateTimeField(null=True, blank=True)
    expected_qty = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    difference = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    class Meta(StockLine.Meta):
        ordering = ["product__shelf_location", "product__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "product"], name="one_count_line_per_product"
            ),
        ]
