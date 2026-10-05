# v1.0.0 — quotation, invoice, customer payment and return.
#
# Each document guards itself: once it leaves its editable state the model
# refuses save() and delete(). State changes after that are made by
# sales/services.py, which writes them with queryset updates inside a locked
# transaction.
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from catalogue.models import Product
from company.models import DocumentType
from company.services import next_document_number
from core.exceptions import DomainError, PostedDocumentError
from core.models import TimeStampedModel
from partners.models import Customer, PriceTier
from sales.money import AMOUNT, CASH, CREDIT, KHQR, PERCENT, net_price

ZERO = Decimal("0.00")
QUOTE_VALID_DAYS = 30


class DiscountType(models.TextChoices):
    PERCENT = PERCENT, "%"
    AMOUNT = AMOUNT, "$ per unit"


class Currency(models.TextChoices):
    USD = "USD", "US dollar"
    KHR = "KHR", "Riel"


def _user_fk(**kw):
    return models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name="+", **kw,
    )


class NumberedDocument(TimeStampedModel):
    """Takes its number on first save."""

    DOC_TYPE = None

    number = models.CharField(max_length=20, unique=True, editable=False)

    class Meta:
        abstract = True

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if not self.number:
                self.number = next_document_number(self.DOC_TYPE)
            super().save(*args, **kwargs)


class PricedLine(models.Model):
    """A product at a stamped price, less a line discount.

    The discount is taken off each unit, so net_price is the price this
    customer pays for one, and line_total is net_price × quantity.
    """

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, editable=False)
    discount_type = models.CharField(max_length=10, choices=DiscountType.choices, blank=True)
    discount_value = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Percent (0–15), or dollars off each unit.",
    )
    net_price = models.DecimalField(max_digits=12, decimal_places=2, editable=False)
    line_total = models.DecimalField(max_digits=14, decimal_places=2, editable=False)

    class Meta:
        abstract = True
        ordering = ["id"]

    @property
    def discount_per_unit(self):
        return self.unit_price - self.net_price

    def stamp_price(self):
        """Set unit_price. Each line type decides where its price comes from."""
        raise NotImplementedError

    def price_line(self):
        self.stamp_price()
        if not self.discount_value:
            self.discount_type = ""
        self.net_price = net_price(
            self.unit_price, self.discount_type, self.discount_value,
            self.product.is_price_fixed,
        )
        self.line_total = (self.net_price * self.quantity).quantize(Decimal("0.01"))


# --- quotation ---------------------------------------------------------------

class QuoteStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    SENT = "SENT", "Sent"
    ACCEPTED = "ACCEPTED", "Accepted"
    REJECTED = "REJECTED", "Rejected"
    INVOICED = "INVOICED", "Invoiced"


QUOTE_EDITABLE = (QuoteStatus.DRAFT, QuoteStatus.SENT)


class Quotation(NumberedDocument):
    """A price offered to a store customer. Nothing moves until an accepted
    quote is invoiced. No cost and no exchange rate are held here — both are
    stamped on the invoice, on the day."""

    DOC_TYPE = DocumentType.QUOTATION

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="quotations")
    price_tier = models.CharField(max_length=10, choices=PriceTier.choices, editable=False)
    quote_date = models.DateField(default=timezone.localdate)
    valid_until = models.DateField()
    status = models.CharField(
        max_length=10, choices=QuoteStatus.choices, default=QuoteStatus.DRAFT,
        editable=False, db_index=True,
    )
    terms = models.TextField(blank=True)
    note = models.TextField(blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-quote_date", "-id"]

    def save(self, *args, **kwargs):
        if self.pk and not Quotation.objects.filter(pk=self.pk, status__in=QUOTE_EDITABLE).exists():
            raise PostedDocumentError(
                "An accepted, rejected or invoiced quotation cannot be changed. "
                "Raise a new quotation instead."
            )
        if not self.price_tier:
            self.price_tier = self.customer.price_tier
        if not self.valid_until:
            self.valid_until = self.quote_date + timedelta(days=QUOTE_VALID_DAYS)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not Quotation.objects.filter(pk=self.pk, status=QuoteStatus.DRAFT).exists():
            raise PostedDocumentError("Only a draft quotation can be deleted.")
        return super().delete(*args, **kwargs)

    @property
    def is_expired(self):
        """Derived, never stored. Warns at the till; does not block."""
        return (
            self.status in (QuoteStatus.DRAFT, QuoteStatus.SENT, QuoteStatus.ACCEPTED)
            and self.valid_until < timezone.localdate()
        )


class QuotationLine(PricedLine):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="lines")
    qty_invoiced = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO, editable=False
    )

    class Meta(PricedLine.Meta):
        constraints = [
            models.CheckConstraint(
                condition=models.Q(qty_invoiced__lte=models.F("quantity")),
                name="quote_line_not_over_invoiced",
            ),
        ]

    @property
    def remaining(self):
        return self.quantity - self.qty_invoiced

    def stamp_price(self):
        self.unit_price = self.product.price_for(self.quotation.price_tier)

    def save(self, *args, **kwargs):
        if not Quotation.objects.filter(pk=self.quotation_id, status__in=QUOTE_EDITABLE).exists():
            raise PostedDocumentError("Lines are fixed once the quotation is accepted.")
        self.price_line()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not Quotation.objects.filter(pk=self.quotation_id, status__in=QUOTE_EDITABLE).exists():
            raise PostedDocumentError("Lines are fixed once the quotation is accepted.")
        return super().delete(*args, **kwargs)


# --- invoice -----------------------------------------------------------------

class InvoiceStatus(models.TextChoices):
    HELD = "HELD", "Held"
    COMPLETED = "COMPLETED", "Completed"
    VOID = "VOID", "Void"


class Invoice(TimeStampedModel):
    """A sale. Held, it has no number, moves no stock and has no rate. On
    completion it takes all three at once, and is frozen from then on.

    seller, sale_date and status are the names users.permissions.CanVoidInvoice
    reads; sale_date is what company.services.rate_is_in_use filters on.
    """

    number = models.CharField(max_length=20, unique=True, null=True, editable=False)
    status = models.CharField(
        max_length=10, choices=InvoiceStatus.choices, default=InvoiceStatus.HELD,
        editable=False, db_index=True,
    )
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices")
    walk_in_name = models.CharField(max_length=100, blank=True)
    walk_in_phone = models.CharField(max_length=30, blank=True)
    price_tier = models.CharField(max_length=10, choices=PriceTier.choices, editable=False)
    quotation = models.ForeignKey(
        Quotation, null=True, blank=True, on_delete=models.PROTECT, related_name="invoices"
    )
    hold_label = models.CharField(max_length=60, blank=True)

    # --- stamped on completion ---
    seller = _user_fk(editable=False)
    sale_date = models.DateTimeField(null=True, blank=True, editable=False, db_index=True)
    exchange_rate = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False
    )
    total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False)
    discount_total = models.DecimalField(
        max_digits=14, decimal_places=2, default=ZERO, editable=False
    )
    paid_now = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False)
    on_credit = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False)
    change_due = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False)
    change_usd = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False)
    change_khr = models.DecimalField(max_digits=14, decimal_places=0, default=0, editable=False)
    rounding = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False)
    due_date = models.DateField(null=True, blank=True, editable=False)

    # --- void ---
    void_reason = models.TextField(blank=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_by = _user_fk(editable=False)

    class Meta:
        ordering = ["-sale_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status="HELD") | models.Q(number__isnull=False),
                name="completed_invoice_has_number",
            ),
        ]

    def __str__(self):
        return self.number or f"Held sale {self.pk}"

    def is_held_in_db(self):
        return Invoice.objects.filter(pk=self.pk, status=InvoiceStatus.HELD).exists()

    def save(self, *args, **kwargs):
        if self.pk and not self.is_held_in_db():
            raise PostedDocumentError("A completed invoice cannot be changed. Void it or take a return.")
        if not self.price_tier:
            self.price_tier = (
                self.quotation.price_tier if self.quotation_id else self.customer.price_tier
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self.is_held_in_db():
            raise PostedDocumentError("A completed invoice is never deleted. Void it instead.")
        return super().delete(*args, **kwargs)

    @property
    def cost_total(self):
        return sum((line.cost_total for line in self.lines.all()), ZERO)


class InvoiceLine(PricedLine):
    """unit_cost is the product's average at the moment of sale, copied from
    the stock movement. Reports read it; nothing recomputes it."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    quote_line = models.ForeignKey(
        QuotationLine, null=True, blank=True, on_delete=models.PROTECT, related_name="invoice_lines"
    )
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True, editable=False
    )

    @property
    def cost_total(self):
        return (self.unit_cost or 0) * self.quantity

    def stamp_price(self):
        if self.quote_line_id:
            # The price and discount agreed on the quote, not today's.
            self.unit_price = self.quote_line.unit_price
            self.discount_type = self.quote_line.discount_type
            self.discount_value = self.quote_line.discount_value
        else:
            self.unit_price = self.product.price_for(self.invoice.price_tier)

    def save(self, *args, **kwargs):
        if not self.invoice.is_held_in_db():
            raise PostedDocumentError("A completed invoice cannot be changed.")
        if self.quote_line_id:
            self.product = self.quote_line.product
        self.price_line()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self.invoice.is_held_in_db():
            raise PostedDocumentError("A completed invoice cannot be changed.")
        return super().delete(*args, **kwargs)


class InvoiceTender(models.Model):
    """How a completed sale was paid at the till. Written once, by completion."""

    KIND_CHOICES = [(CASH, "Cash"), (KHQR, "KHQR"), (CREDIT, "Credit")]

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="tenders")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.USD)
    amount = models.DecimalField(max_digits=14, decimal_places=2, help_text="In its own currency.")
    amount_usd = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["id"]


# --- customer payment --------------------------------------------------------

class PaymentStatus(models.TextChoices):
    POSTED = "POSTED", "Posted"
    VOID = "VOID", "Void"


class PaymentTender(models.TextChoices):
    CASH = "CASH", "Cash"
    KHQR = "KHQR", "KHQR"
    BANK = "BANK", "Bank transfer"


class CustomerPayment(NumberedDocument):
    """Money collected against invoices already raised. Applied in full —
    a payment never sits unallocated. Frozen once saved; an Admin may void it."""

    DOC_TYPE = DocumentType.PAYMENT

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="payments")
    payment_date = models.DateField(default=timezone.localdate)
    tender = models.CharField(max_length=10, choices=PaymentTender.choices)
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.USD)
    amount_tendered = models.DecimalField(max_digits=14, decimal_places=2)
    exchange_rate = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True, editable=False
    )
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, editable=False, help_text="In US dollars."
    )
    reference = models.CharField(max_length=60, blank=True)
    note = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.POSTED,
        editable=False, db_index=True,
    )
    void_reason = models.TextField(blank=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_by = _user_fk(editable=False)

    class Meta:
        ordering = ["-payment_date", "-id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PostedDocumentError("A payment cannot be changed. An Admin may void it.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PostedDocumentError("A payment is never deleted. An Admin may void it.")


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(CustomerPayment, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="allocation_positive"),
        ]


# --- return ----------------------------------------------------------------------

class ReturnStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    POSTED = "POSTED", "Posted"


class RefundMethod(models.TextChoices):
    CASH = "CASH", "Cash refund"
    KHQR = "KHQR", "KHQR refund"


class SalesReturn(NumberedDocument):
    """Goods taken back after the sale, against one invoice.

    On posting, the value first reduces what is still owed on that invoice
    (credited); anything above that is refunded. No loose credit is left on
    the account.
    """

    DOC_TYPE = DocumentType.RETURN

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="returns")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="returns", editable=False)
    return_date = models.DateField(default=timezone.localdate)
    reason = models.TextField()
    refund_method = models.CharField(max_length=10, choices=RefundMethod.choices, blank=True)
    status = models.CharField(
        max_length=10, choices=ReturnStatus.choices, default=ReturnStatus.DRAFT,
        editable=False, db_index=True,
    )
    total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False)
    credited = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False)
    refunded = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    posted_by = _user_fk(editable=False)

    class Meta:
        ordering = ["-return_date", "-id"]

    def is_draft_in_db(self):
        return SalesReturn.objects.filter(pk=self.pk, status=ReturnStatus.DRAFT).exists()

    def save(self, *args, **kwargs):
        if self.pk and not self.is_draft_in_db():
            raise PostedDocumentError()
        self.customer_id = self.invoice.customer_id
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self.is_draft_in_db():
            raise PostedDocumentError()
        return super().delete(*args, **kwargs)


class ReturnLine(models.Model):
    """Price and cost come from the original sale line: the customer gets back
    what they paid, and fit-to-sell goods re-enter stock at the cost they left
    at, not today's average."""

    sales_return = models.ForeignKey(SalesReturn, on_delete=models.CASCADE, related_name="lines")
    invoice_line = models.ForeignKey(InvoiceLine, on_delete=models.PROTECT, related_name="returned")
    quantity = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    fit_to_sell = models.BooleanField(
        default=True, help_text="Off for a faulty item: it does not go back into stock."
    )
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, editable=False)
    line_total = models.DecimalField(max_digits=14, decimal_places=2, editable=False)

    class Meta:
        ordering = ["id"]

    def save(self, *args, **kwargs):
        if not self.sales_return.is_draft_in_db():
            raise PostedDocumentError()
        if self.invoice_line.invoice_id != self.sales_return.invoice_id:
            raise DomainError("That line is not on the invoice being returned.")
        self.unit_price = self.invoice_line.net_price
        self.line_total = (self.unit_price * self.quantity).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self.sales_return.is_draft_in_db():
            raise PostedDocumentError()
        return super().delete(*args, **kwargs)
