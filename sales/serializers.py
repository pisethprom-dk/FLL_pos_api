# v1.0.2
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from company.currency import usd_to_khr
from core.exceptions import PostedDocumentError
from core.schema import money
from sales.models import (
    Currency,
    CustomerPayment,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    InvoiceTender,
    PaymentAllocation,
    PaymentStatus,
    Quotation,
    QuotationLine,
    QuoteStatus,
    ReturnLine,
    ReturnStatus,
    SalesReturn,
)
from partners.models import PriceTier
from sales.money import TENDER_KINDS
from sales.services import returnable
from users.scopes import has_scope


def _can_see_cost(serializer):
    request = serializer.context.get("request")
    return bool(request and has_scope(request.user, "cost.view"))


class LinesMixin:
    """Lines are sent whole: on update, a `lines` list replaces what was there.
    Leave `lines` out to change the header only."""

    line_parent = None  # the line's FK name to its document

    def create(self, validated):
        lines = validated.pop("lines", [])
        with transaction.atomic():
            doc = super().create(validated)
            self.write_lines(doc, lines)
        return doc

    def update(self, instance, validated):
        lines = validated.pop("lines", None)
        with transaction.atomic():
            doc = super().update(instance, validated)
            # Read the lines afresh: the view may have prefetched them with the
            # document as it was before this update.
            current = doc.lines.model.objects.filter(**{self.line_parent: doc})
            if lines is not None:
                for line in current:
                    line.delete()
                self.write_lines(doc, lines)
            elif self.reprice_on_update:
                for line in current:
                    setattr(line, self.line_parent, doc)
                    line.save()
        return doc

    reprice_on_update = False

    def write_lines(self, doc, lines):
        model = doc.lines.model
        for line in lines:
            model(**{self.line_parent: doc}, **line).save()


PRICED_LINE_FIELDS = [
    "id", "product", "product_code", "product_name", "unit_name", "quantity",
    "unit_price", "discount_type", "discount_value", "net_price", "line_total",
]


class PricedLineSerializer(serializers.ModelSerializer):
    product_code = serializers.CharField(source="product.code", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    unit_name = serializers.CharField(source="product.unit.name", read_only=True)

    def validate_product(self, product):
        if not product.is_active:
            raise serializers.ValidationError(f"{product.code} is inactive and cannot be sold.")
        return product


# --- quotation -------------------------------------------------------------------

class QuotationLineSerializer(PricedLineSerializer):
    remaining = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = QuotationLine
        fields = PRICED_LINE_FIELDS + ["qty_invoiced", "remaining"]


class QuotationSerializer(LinesMixin, serializers.ModelSerializer):
    line_parent = "quotation"

    customer_name = serializers.CharField(source="customer.name", read_only=True)
    status = serializers.ChoiceField(choices=QuoteStatus.choices, read_only=True)
    price_tier = serializers.ChoiceField(choices=PriceTier.choices, read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    total = serializers.SerializerMethodField()
    invoiced_total = serializers.SerializerMethodField()
    remaining_total = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default=None, allow_null=True)
    lines = QuotationLineSerializer(many=True, required=False)

    class Meta:
        model = Quotation
        fields = [
            "id", "number", "customer", "customer_name", "price_tier",
            "quote_date", "valid_until", "status", "is_expired", "accepted_at",
            "terms", "note", "total", "invoiced_total", "remaining_total",
            "created_by_name", "lines",
        ]
        read_only_fields = ["id", "number", "price_tier", "status", "accepted_at"]
        extra_kwargs = {"valid_until": {"required": False}}

    @extend_schema_field(money())
    def get_total(self, obj):
        return str(sum((l.line_total for l in obj.lines.all()), Decimal("0.00")))

    @extend_schema_field(money())
    def get_invoiced_total(self, obj):
        total = sum((l.net_price * l.qty_invoiced for l in obj.lines.all()), Decimal("0.00"))
        return str(total.quantize(Decimal("0.01")))

    @extend_schema_field(money())
    def get_remaining_total(self, obj):
        total = sum((l.net_price * l.remaining for l in obj.lines.all()), Decimal("0.00"))
        return str(total.quantize(Decimal("0.01")))

    def validate_customer(self, customer):
        if self.instance and customer != self.instance.customer:
            raise serializers.ValidationError("The customer is fixed. Raise a new quotation instead.")
        if customer.is_system:
            raise serializers.ValidationError(
                "A quotation is for a store customer. A walk-in sells straight to an invoice."
            )
        if not customer.is_active:
            raise serializers.ValidationError(f"{customer.name} is inactive.")
        return customer

    def validate(self, attrs):
        quote_date = attrs.get("quote_date", getattr(self.instance, "quote_date", None))
        valid_until = attrs.get("valid_until", getattr(self.instance, "valid_until", None))
        if quote_date and valid_until and valid_until < quote_date:
            raise serializers.ValidationError({"valid_until": "Cannot be before the quote date."})
        return attrs

    def update(self, instance, validated):
        if instance.status not in (QuoteStatus.DRAFT, QuoteStatus.SENT):
            raise PostedDocumentError(
                "An accepted, rejected or invoiced quotation cannot be changed. "
                "Raise a new quotation instead."
            )
        return super().update(instance, validated)


# --- invoice -----------------------------------------------------------------------

class InvoiceLineSerializer(PricedLineSerializer):
    quote_line = serializers.PrimaryKeyRelatedField(
        queryset=QuotationLine.objects.all(), required=False, allow_null=True
    )
    qty_returned = serializers.SerializerMethodField()

    class Meta:
        model = InvoiceLine
        fields = PRICED_LINE_FIELDS + ["quote_line", "unit_cost", "qty_returned"]
        extra_kwargs = {"product": {"required": False}}

    @extend_schema_field(serializers.DecimalField(max_digits=12, decimal_places=2))
    def get_qty_returned(self, line):
        if line.invoice.status != "COMPLETED":
            return "0.00"
        return str(line.quantity - returnable(line))

    def validate(self, attrs):
        quote_line = attrs.get("quote_line")
        if quote_line:
            attrs["product"] = quote_line.product
            if attrs.get("discount_value"):
                raise serializers.ValidationError(
                    "A line from a quotation keeps the price and discount agreed on it."
                )
        elif not attrs.get("product"):
            raise serializers.ValidationError({"product": "Choose a product."})
        return attrs

    def to_representation(self, line):
        data = super().to_representation(line)
        if not _can_see_cost(self):
            # Null rather than left out, so the generated client's types stay true.
            data["unit_cost"] = None
        return data


class InvoiceTenderSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceTender
        fields = ["kind", "currency", "amount", "amount_usd", "reference"]


class InvoiceSerializer(LinesMixin, serializers.ModelSerializer):
    """A held sale is written through this. Completing and voiding are actions."""

    line_parent = "invoice"
    reprice_on_update = True  # a new customer may mean a new price tier

    customer_name = serializers.CharField(source="customer.name", read_only=True)
    status = serializers.ChoiceField(choices=InvoiceStatus.choices, read_only=True)
    price_tier = serializers.ChoiceField(choices=PriceTier.choices, read_only=True)
    quotation_number = serializers.CharField(source="quotation.number", read_only=True, default=None, allow_null=True)
    seller_name = serializers.CharField(source="seller.full_name", read_only=True, default=None, allow_null=True)
    voided_by_name = serializers.CharField(source="voided_by.full_name", read_only=True, default=None, allow_null=True)
    held_total = serializers.SerializerMethodField()
    total_khr = serializers.SerializerMethodField()
    cost_total = serializers.SerializerMethodField(help_text="Null unless the user may see cost.")
    profit = serializers.SerializerMethodField(help_text="Null unless the user may see cost.")
    lines = InvoiceLineSerializer(many=True, required=False)
    tenders = InvoiceTenderSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "number", "status", "customer", "customer_name",
            "walk_in_name", "walk_in_phone", "price_tier", "quotation", "quotation_number",
            "hold_label", "seller", "seller_name", "sale_date", "exchange_rate",
            "held_total", "total", "total_khr", "discount_total", "paid_now", "on_credit",
            "change_due", "change_usd", "change_khr", "rounding", "due_date",
            "cost_total", "profit",
            "void_reason", "voided_at", "voided_by_name", "lines", "tenders",
        ]
        read_only_fields = ["id", "number", "status", "price_tier", "seller", "sale_date"]

    @extend_schema_field(money())
    def get_held_total(self, obj):
        """What the sale comes to so far — `total` is stamped only on completion."""
        return str(sum((l.line_total for l in obj.lines.all()), Decimal("0.00")))

    @extend_schema_field(serializers.DecimalField(max_digits=16, decimal_places=0, allow_null=True))
    def get_total_khr(self, obj):
        if obj.exchange_rate is None:
            return None
        return str(usd_to_khr(obj.total, obj.exchange_rate))

    def _cost(self, obj):
        if obj.status == "HELD" or not _can_see_cost(self):
            return None
        return obj.cost_total

    @extend_schema_field(money(allow_null=True))
    def get_cost_total(self, obj):
        cost = self._cost(obj)
        return None if cost is None else str(cost.quantize(Decimal("0.01")))

    @extend_schema_field(money(allow_null=True))
    def get_profit(self, obj):
        cost = self._cost(obj)
        return None if cost is None else str((obj.total - cost).quantize(Decimal("0.01")))

    def validate_customer(self, customer):
        if not customer.is_active:
            raise serializers.ValidationError(f"{customer.name} is inactive and cannot be sold to.")
        return customer

    def validate_quotation(self, quote):
        if self.instance and quote != self.instance.quotation:
            raise serializers.ValidationError("A held sale cannot switch quotations. Start a new sale.")
        if quote and quote.status != QuoteStatus.ACCEPTED:
            raise serializers.ValidationError(
                f"{quote.number} is not accepted. Only an accepted quotation can be invoiced."
            )
        return quote

    def validate(self, attrs):
        quote = attrs.get("quotation", getattr(self.instance, "quotation", None))
        customer = attrs.get("customer", getattr(self.instance, "customer", None))
        if quote:
            if customer and customer != quote.customer:
                raise serializers.ValidationError({"customer": "The customer comes from the quotation."})
            attrs["customer"] = customer = quote.customer
        if customer and not customer.is_system and (attrs.get("walk_in_name") or attrs.get("walk_in_phone")):
            raise serializers.ValidationError(
                {"walk_in_name": "A name and phone are recorded only for a walk-in sale."}
            )
        for line in attrs.get("lines", []):
            ql = line.get("quote_line")
            if ql and (quote is None or ql.quotation_id != quote.pk):
                raise serializers.ValidationError({"lines": "A line is not on this sale's quotation."})
            if quote and not ql:
                raise serializers.ValidationError(
                    {"lines": "A sale from a quotation takes only the quotation's lines."}
                )
        return attrs

    def update(self, instance, validated):
        if not instance.is_held_in_db():
            raise PostedDocumentError("A completed invoice cannot be changed. Void it or take a return.")
        if "customer" in validated and validated["customer"] != instance.customer and not instance.quotation_id:
            # A new customer may be on a different tier; the lines are repriced.
            instance.price_tier = validated["customer"].price_tier
        return super().update(instance, validated)


class TenderInputSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=TENDER_KINDS)
    currency = serializers.ChoiceField(choices=Currency.choices, default=Currency.USD)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    reference = serializers.CharField(required=False, allow_blank=True, default="")


class CompleteSerializer(serializers.Serializer):
    tenders = TenderInputSerializer(many=True, required=False, default=list)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField()


class NoteSerializer(serializers.Serializer):
    note = serializers.CharField()


# --- customer payment ----------------------------------------------------------------

class AllocationSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)

    class Meta:
        model = PaymentAllocation
        fields = ["invoice", "invoice_number", "amount"]


class CustomerPaymentSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    status = serializers.ChoiceField(choices=PaymentStatus.choices, read_only=True)
    taken_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default=None, allow_null=True)
    voided_by_name = serializers.CharField(source="voided_by.full_name", read_only=True, default=None, allow_null=True)
    allocations = AllocationSerializer(many=True, required=False)

    class Meta:
        model = CustomerPayment
        fields = [
            "id", "number", "customer", "customer_name", "payment_date", "tender",
            "currency", "amount_tendered", "exchange_rate", "amount", "reference", "note",
            "status", "taken_by_name", "void_reason", "voided_at", "voided_by_name",
            "allocations",
        ]
        read_only_fields = ["id", "number", "status"]

    def validate_payment_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError("The payment date cannot be in the future.")
        return value

    def validate(self, attrs):
        if attrs.get("currency") == "KHR" and attrs["amount_tendered"] != attrs["amount_tendered"].to_integral_value():
            raise serializers.ValidationError({"amount_tendered": "Riel has no decimals."})
        return attrs


# --- return ------------------------------------------------------------------------------

class ReturnLineSerializer(serializers.ModelSerializer):
    product_code = serializers.CharField(source="invoice_line.product.code", read_only=True)
    product_name = serializers.CharField(source="invoice_line.product.name", read_only=True)
    sold = serializers.DecimalField(
        source="invoice_line.quantity", max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = ReturnLine
        fields = [
            "id", "invoice_line", "product_code", "product_name", "sold",
            "quantity", "fit_to_sell", "unit_price", "line_total",
        ]


class SalesReturnSerializer(LinesMixin, serializers.ModelSerializer):
    line_parent = "sales_return"

    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    status = serializers.ChoiceField(choices=ReturnStatus.choices, read_only=True)
    draft_total = serializers.SerializerMethodField()
    lines = ReturnLineSerializer(many=True, required=False)

    class Meta:
        model = SalesReturn
        fields = [
            "id", "number", "invoice", "invoice_number", "customer", "customer_name",
            "return_date", "reason", "refund_method", "status",
            "draft_total", "total", "credited", "refunded", "posted_at", "lines",
        ]
        read_only_fields = ["id", "number", "customer", "status"]

    @extend_schema_field(money())
    def get_draft_total(self, obj):
        return str(sum((l.line_total for l in obj.lines.all()), Decimal("0.00")))

    def validate_invoice(self, invoice):
        if self.instance and invoice != self.instance.invoice:
            raise serializers.ValidationError("The invoice is fixed. Start a new return instead.")
        if invoice.status != "COMPLETED":
            raise serializers.ValidationError("Goods can only be returned against a completed invoice.")
        return invoice

    def validate_return_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError("The return date cannot be in the future.")
        return value

    def validate(self, attrs):
        invoice = attrs.get("invoice", getattr(self.instance, "invoice", None))
        for line in attrs.get("lines", []):
            il = line["invoice_line"]
            if il.invoice_id != invoice.pk:
                raise serializers.ValidationError({"lines": f"A line is not on {invoice.number}."})
            left = returnable(il)
            if line["quantity"] > left:
                raise serializers.ValidationError(
                    {"lines": f"{il.product.code}: only {left} can still come back."}
                )
        return attrs

    def update(self, instance, validated):
        if not instance.is_draft_in_db():
            raise PostedDocumentError()
        return super().update(instance, validated)


# --- customer account --------------------------------------------------------------

class OpenInvoiceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    number = serializers.CharField()
    sale_date = serializers.DateTimeField()
    due_date = serializers.DateField(allow_null=True)
    on_credit = money()
    paid = money()
    credited = money()
    balance = money()
    overdue = serializers.BooleanField()


class CustomerAccountSerializer(serializers.Serializer):
    """What a customer owes, worked out now — never stored."""

    customer = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    credit_status = serializers.ChoiceField(choices=["NO", "YES", "HOLD"])
    credit_limit = money()
    payment_terms_days = serializers.IntegerField()
    balance = money()
    room_left = money()
    open_invoices = OpenInvoiceSerializer(many=True)
