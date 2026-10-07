# v1.1.0 — customers, suppliers, and which supplier carries which product
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction

from catalogue.models import Product, Unit
from company.models import DocumentType
from company.services import next_document_number
from core.exceptions import DomainError
from core.models import ActivatableModel, TimeStampedModel


class PriceTier(models.TextChoices):
    RETAIL = "RETAIL", "Retail"
    WHOLESALE = "WHOLESALE", "Wholesale"


class SupplierType(models.TextChoices):
    MANUFACTURER = "MANUFACTURER", "Manufacturer"
    IMPORTER = "IMPORTER", "Importer"
    DISTRIBUTOR = "DISTRIBUTOR", "Distributor"
    WHOLESALER = "WHOLESALER", "Wholesaler"
    SERVICE_CENTRE = "SERVICE_CENTRE", "Service centre"
    LOCAL_MARKET = "LOCAL_MARKET", "Local market"


class Partner(TimeStampedModel, ActivatableModel):
    """What a customer and a supplier share: who they are, how to reach them,
    where they are.

    A blank code is numbered from the company counters on first save, so it
    holds whether the row comes from the API, the admin or the seed.
    """

    COUNTER = None  # a DocumentType, set by each subclass

    code = models.CharField(
        max_length=20, unique=True, blank=True,
        help_text="Leave blank to let the system number it.",
    )
    name = models.CharField(max_length=150)
    name_kh = models.CharField(max_length=150, blank=True)
    short_name = models.CharField(
        max_length=60, blank=True, help_text="Used in dropdowns and reports."
    )
    display_order = models.IntegerField(default=1)

    # --- contact ---
    contact_person = models.CharField(max_length=100, blank=True)
    position = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    phone_alt = models.CharField(max_length=30, blank=True)
    telegram = models.CharField(max_length=60, blank=True)
    email = models.EmailField(blank=True)

    # --- address ---
    address = models.CharField(max_length=250, blank=True)
    district = models.CharField(max_length=100, blank=True)
    province = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=60, blank=True, default="Cambodia")

    notes = models.TextField(blank=True)

    class Meta:
        abstract = True
        ordering = ["display_order", "code"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip()
        if not self.short_name:
            self.short_name = self.name[:60]
        with transaction.atomic():
            if not self.code:
                self.code = self._next_free_code()
            super().save(*args, **kwargs)

    def _next_free_code(self):
        """Take numbers until one is free.

        A code typed by hand may already hold the next number. Skipping past it
        beats failing the save on the unique index.
        """
        while True:
            code = next_document_number(self.COUNTER)
            if not type(self).objects.filter(code=code).exists():
                return code


class Customer(Partner):
    """A store customer the shop knows by name, plus the one walk-in row.

    What they owe is not held here. It is worked out from invoices less
    payments, so it cannot drift from the documents that make it up.

    Price tier and credit are independent: a trade buyer who pays cash still
    gets wholesale.
    """

    COUNTER = DocumentType.CUSTOMER

    price_tier = models.CharField(
        max_length=10, choices=PriceTier.choices, default=PriceTier.RETAIL,
        help_text="Loaded automatically at checkout.",
    )
    is_system = models.BooleanField(
        default=False, editable=False,
        help_text="The walk-in customer. Exactly one, created by seed.",
    )

    # --- credit ---
    allow_credit = models.BooleanField(
        default=False, help_text="Off means every sale must be settled at the till."
    )
    credit_limit = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="In US dollars.",
    )
    payment_terms_days = models.PositiveIntegerField(
        default=0, help_text="Days. Sets the due date on each credit sale."
    )
    credit_hold = models.BooleanField(
        default=False,
        help_text="Blocks new credit sales without changing the limit. "
                  "Existing invoices are unaffected.",
    )

    class Meta(Partner.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["is_system"], condition=models.Q(is_system=True),
                name="one_system_customer",
            ),
            models.CheckConstraint(
                condition=models.Q(is_system=False)
                | models.Q(allow_credit=False, price_tier="RETAIL", is_active=True),
                name="system_customer_is_retail_cash_active",
            ),
            models.CheckConstraint(
                condition=models.Q(allow_credit=True, credit_limit__gt=0)
                | models.Q(
                    allow_credit=False, credit_limit=0,
                    payment_terms_days=0, credit_hold=False,
                ),
                name="customer_credit_fields_agree",
            ),
        ]

    @classmethod
    def walk_in(cls):
        return cls.objects.get(is_system=True)

    @property
    def credit_status(self):
        """NO, YES or HOLD — the Credit column on the customer list."""
        if not self.allow_credit:
            return "NO"
        return "HOLD" if self.credit_hold else "YES"

    def _walk_in_errors(self):
        if not self.is_system:
            return {}
        errors = {}
        if self.allow_credit:
            errors["allow_credit"] = "The walk-in customer can never buy on credit."
        if self.price_tier != PriceTier.RETAIL:
            errors["price_tier"] = "The walk-in customer always pays retail prices."
        if not self.is_active:
            errors["is_active"] = "The walk-in customer cannot be deactivated."
        if self.pk:
            was = Customer.objects.filter(pk=self.pk).values("code", "name").first()
            if was and was["name"] != self.name:
                errors["name"] = "The walk-in customer cannot be renamed."
            if was and was["code"] != self.code:
                errors["code"] = "The walk-in customer's code cannot be changed."
        return errors

    def clean(self):
        errors = self._walk_in_errors()
        if self.allow_credit and not (self.credit_limit and self.credit_limit > 0):
            errors.setdefault(
                "credit_limit", "Set a credit limit above zero, or turn credit off."
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        errors = self._walk_in_errors()
        if errors:
            raise DomainError(next(iter(errors.values())))
        if not self.allow_credit:
            # Off means off: no limit or terms left behind to mislead a report.
            self.credit_limit = Decimal("0.00")
            self.payment_terms_days = 0
            self.credit_hold = False
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.is_system:
            raise DomainError("The walk-in customer cannot be deleted.")
        return super().delete(*args, **kwargs)


class Supplier(Partner):
    """Who the shop buys from. Contact details only.

    No prices, terms, bank details or payables: each stock-in records what was
    actually paid. Service centres that repair warranty items are suppliers
    too, told apart by type.
    """

    COUNTER = DocumentType.SUPPLIER

    supplier_type = models.CharField(max_length=20, choices=SupplierType.choices)
    website = models.CharField(max_length=200, blank=True)
    logo = models.ImageField(upload_to="suppliers/", null=True, blank=True)

    class Meta(Partner.Meta):
        pass


class ProductSupplier(TimeStampedModel):
    """Which supplier carries which product, under their code and their pack.

    No price: what was paid lives on each stock-in line, and a copy here would
    go stale. Pack unit and size are the supplier's usual pack ("Carton" of
    24) — a stock-in line stamps its own, so a supplier changing their carton
    does not alter past documents.

    A link carries no history, so unlike the records it joins it may be deleted.
    """

    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="supplier_links"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name="product_links"
    )
    supplier_sku = models.CharField(
        max_length=60, blank=True, help_text="The supplier's own code for this product."
    )
    pack_unit = models.ForeignKey(
        Unit, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="What the supplier's pack is, e.g. Carton. Blank means the product's own unit.",
    )
    pack_size = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Units of the product in the supplier's usual pack.",
    )
    is_preferred = models.BooleanField(
        default=False, help_text="The supplier to buy from first. One per product."
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["product__code", "-is_preferred", "supplier__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "supplier"], name="one_link_per_product_supplier"
            ),
            models.UniqueConstraint(
                fields=["product"], condition=models.Q(is_preferred=True),
                name="one_preferred_supplier_per_product",
            ),
            models.CheckConstraint(
                condition=models.Q(pack_size__gt=0), name="pack_size_is_positive"
            ),
        ]

    def __str__(self):
        return f"{self.product.code} ← {self.supplier.code}"

    def clean(self):
        errors = {}
        # A pack of several needs a name, or a stock-in line would read
        # "24 per Piece".
        if self.pack_unit_id is None and self.pack_size is not None and self.pack_size != 1:
            errors["pack_unit"] = "Say what the pack is, such as Carton, when it holds more than one."
        # Only checked when the link is made: a supplier retired later keeps
        # the links it already had, the same way past stock-ins keep it.
        if self._state.adding:
            if self.supplier_id and not self.supplier.is_active:
                errors["supplier"] = "This supplier is inactive and cannot be linked."
            if self.product_id and not self.product.is_active:
                errors["product"] = "This product is inactive and cannot be linked."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.is_preferred:
                # Marking one preferred unmarks the last, rather than refusing.
                ProductSupplier.objects.filter(
                    product_id=self.product_id, is_preferred=True
                ).exclude(pk=self.pk).update(is_preferred=False)
            super().save(*args, **kwargs)
