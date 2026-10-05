# v1.0.3
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from core.models import ActivatableModel, TimeStampedModel


class CompanyProfile(TimeStampedModel):
    """One shop, one row. Printed on every receipt and invoice."""

    name = models.CharField(max_length=150)
    name_kh = models.CharField(max_length=150, blank=True)
    address = models.CharField(max_length=250, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    vat_tin = models.CharField(max_length=50, blank=True)
    logo = models.ImageField(upload_to="company/", null=True, blank=True)

    receipt_header = models.CharField(max_length=150, blank=True)
    receipt_footer = models.TextField(blank=True)
    receipt_paper_width = models.CharField(
        max_length=10,
        choices=[("80mm", "80 mm"), ("58mm", "58 mm"), ("A5", "A5")],
        default="80mm",
    )
    receipt_language = models.CharField(
        max_length=10,
        choices=[("EN", "English"), ("KH", "Khmer"), ("BOTH", "Both")],
        default="EN",
    )
    receipt_show_riel_total = models.BooleanField(default=True)
    receipt_show_rate_used = models.BooleanField(default=True)
    receipt_show_seller = models.BooleanField(default=False)

    class Meta:
        verbose_name = "company profile"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Singleton: always row 1, so nothing has to pick between two profiles.
        # Saving a fresh instance when row 1 already exists has to behave as an
        # update, which means carrying the original created_at across —
        # auto_now_add does not fire on the update path.
        self.pk = 1
        kwargs.pop("force_insert", None)
        if self._state.adding:
            existing = (
                CompanyProfile.objects.filter(pk=1).values("created_at").first()
            )
            if existing:
                self.created_at = existing["created_at"]
                self._state.adding = False
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("The company profile cannot be deleted.")

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"name": "My shop"})
        return obj


class ExchangeRate(TimeStampedModel):
    """Riel per one US dollar, from a date until the next row replaces it.

    Append-only in practice: a rate with sales against it must not be edited,
    because every invoice keeps the rate it was saved with. A correction is a
    new row with a later effective date.
    """

    effective_date = models.DateField(db_index=True)
    rate = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        validators=[MinValueValidator(0.000001)],
        help_text="Riel per one US dollar.",
    )
    note = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["-effective_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["effective_date"], name="uniq_rate_per_date"
            ),
            models.CheckConstraint(
                condition=models.Q(rate__gt=0), name="rate_is_positive"
            ),
        ]

    def __str__(self):
        return f"{self.effective_date}: {self.rate}"


class DocumentType(models.TextChoices):
    QUOTATION = "QUOTATION", "Quotation"
    INVOICE = "INVOICE", "Invoice"
    PAYMENT = "PAYMENT", "Customer payment"
    RETURN = "RETURN", "Return"
    STOCK_IN = "STOCK_IN", "Stock in"
    ADJUSTMENT = "ADJUSTMENT", "Adjustment"
    COUNT = "COUNT", "Stock count"
    WARRANTY = "WARRANTY", "Warranty claim"
    # Not documents, but numbered the same way when the code is left blank.
    CUSTOMER = "CUSTOMER", "Customer"
    SUPPLIER = "SUPPLIER", "Supplier"


class DocumentCounter(models.Model):
    """Next number per document type, and for customer and supplier codes.

    The prefix lives here rather than on the company profile, so the Rules and
    numbering screen edits these rows directly. Padding is fixed at six digits.
    """

    doc_type = models.CharField(
        max_length=20, choices=DocumentType.choices, unique=True
    )
    prefix = models.CharField(max_length=10)
    next_number = models.PositiveIntegerField(default=1)

    PADDING = 6

    class Meta:
        ordering = ["doc_type"]

    def __str__(self):
        return f"{self.doc_type} → {self.peek()}"

    def peek(self):
        return f"{self.prefix}{self.next_number:0{self.PADDING}d}"


class PaymentNote(TimeStampedModel, ActivatableModel):
    """Text and images printed at the foot of an invoice telling the customer
    how to pay.

    Notes only. How a sale was actually settled is recorded on the sale.
    """

    payment_type = models.CharField(max_length=150, unique=True)
    payment_info = models.TextField(blank=True)
    image = models.ImageField(upload_to="payment_notes/", null=True, blank=True)
    row_order = models.IntegerField(default=1)

    class Meta:
        ordering = ["row_order", "payment_type"]

    def __str__(self):
        return self.payment_type
