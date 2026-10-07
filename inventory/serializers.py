# v1.2.0
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.exceptions import PostedDocumentError
from core.schema import money
from inventory.models import (
    Adjustment,
    AdjustmentLine,
    AdjustmentReason,
    DocStatus,
    StockCount,
    StockCountLine,
    StockIn,
    StockInLine,
    StockMovement,
)
from inventory.services import (
    check_adjustment_header,
    default_pack,
    opening_balance_allowed,
    start_count,
)

DOCUMENT_FIELDS = [
    "id", "number", "doc_date", "note", "status", "posted_at", "posted_by",
    "posted_by_name", "reverses", "reverses_number", "reversed_by_number",
]
DOCUMENT_READ_ONLY = [
    "id", "number", "status", "posted_at", "posted_by", "reverses",
]


def turned(value):
    """A decimal string with its sign turned: "25.40" → "-25.40". Null stays
    null; Decimal turns "0.00" into "0.00", never "-0.00"."""
    return None if value is None else str(-Decimal(value))


class DocumentSerializer(serializers.ModelSerializer):
    """Header fields every stock document shares.

    Lines are sent whole: on update, a `lines` list replaces what was there.
    Leave `lines` out to change the header only.
    """

    # A reversal stores a copy of its original's lines (a stock-in line may
    # not hold a negative number), so its figures are turned here, as they are
    # sent: each reads the way stock moved (owner's choice, 2026-10-06). What
    # was typed — packs, costs, quantities — is sent as stored.
    TURNED = ()  # document fields turned on a reversal
    TURNED_ON_LINES = ()  # line fields turned on a reversal
    SWAPPED_ON_LINES = ()  # pairs of line fields swapped on a reversal

    status = serializers.ChoiceField(choices=DocStatus.choices, read_only=True)
    posted_by_name = serializers.CharField(source="posted_by.full_name", read_only=True, default=None, allow_null=True)
    reverses_number = serializers.CharField(source="reverses.number", read_only=True, default=None, allow_null=True)
    reversed_by_number = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_reversed_by_number(self, obj):
        reversal = getattr(obj, "reversed_by", None)
        return reversal.number if reversal else None

    def to_representation(self, doc):
        data = super().to_representation(doc)
        if doc.reverses_id:
            for field in self.TURNED:
                data[field] = turned(data[field])
            for line in data.get("lines") or []:
                for field in self.TURNED_ON_LINES:
                    line[field] = turned(line[field])
                for a, b in self.SWAPPED_ON_LINES:
                    line[a], line[b] = line[b], line[a]
        return data

    def validate_doc_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError("The document date cannot be in the future.")
        return value

    def validate_lines(self, lines):
        for line in lines:
            product = line["product"]
            if not product.track_stock:
                raise serializers.ValidationError(
                    f"{product.code} does not track stock, so it has none to move."
                )
        return lines

    def create(self, validated):
        lines = validated.pop("lines", [])
        with transaction.atomic():
            doc = super().create(validated)
            self.write_lines(doc, lines)
        return doc

    def update(self, instance, validated):
        if instance.was_posted():
            raise PostedDocumentError()
        lines = validated.pop("lines", None)
        with transaction.atomic():
            doc = super().update(instance, validated)
            if lines is not None:
                for line in doc.lines.all():
                    line.delete()
                self.write_lines(doc, lines)
        return doc

    def write_lines(self, doc, lines):
        model = doc.lines.model
        for line in lines:
            model(document=doc, **line).save()


# --- stock in ------------------------------------------------------------------

class StockInLineSerializer(serializers.ModelSerializer):
    product_code = serializers.CharField(source="product.code", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    unit_name = serializers.CharField(source="product.unit.name", read_only=True)
    pack_unit_name = serializers.CharField(source="pack_unit.name", read_only=True, default=None, allow_null=True)

    class Meta:
        model = StockInLine
        fields = [
            "id", "product", "product_code", "product_name", "unit_name",
            "pack_unit", "pack_unit_name", "packs", "pack_size", "pack_cost",
            "quantity", "unit_cost", "line_total",
        ]
        extra_kwargs = {"pack_size": {"required": False}}


class StockInSerializer(DocumentSerializer):
    # A reversal took the goods back out.
    TURNED = ("total",)
    TURNED_ON_LINES = ("quantity", "line_total")

    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    lines = StockInLineSerializer(many=True, required=False)
    total = serializers.SerializerMethodField()

    class Meta:
        model = StockIn
        fields = DOCUMENT_FIELDS + ["supplier", "supplier_name", "supplier_ref", "total", "lines"]
        read_only_fields = DOCUMENT_READ_ONLY

    @extend_schema_field(money())
    def get_total(self, obj):
        return str(sum((line.line_total for line in obj.lines.all()), Decimal("0.00")))

    def validate_supplier(self, supplier):
        changing = not self.instance or self.instance.supplier_id != supplier.pk
        if changing and not supplier.is_active:
            raise serializers.ValidationError(f"{supplier.name} is inactive.")
        return supplier

    def validate_lines(self, lines):
        lines = super().validate_lines(lines)
        for line in lines:
            if not line["product"].is_active:
                raise serializers.ValidationError(
                    f"{line['product'].code} is inactive and cannot be bought."
                )
        return lines

    def write_lines(self, doc, lines):
        # A line sent without a pack size takes the supplier's usual pack —
        # its unit too, unless the line names one.
        for line in lines:
            if "pack_size" not in line:
                unit, line["pack_size"] = default_pack(line["product"], doc.supplier)
                line.setdefault("pack_unit", unit)
        super().write_lines(doc, lines)


# --- adjustment ----------------------------------------------------------------

class AdjustmentLineSerializer(serializers.ModelSerializer):
    product_code = serializers.CharField(source="product.code", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    shelf_location = serializers.CharField(source="product.shelf_location", read_only=True)
    on_hand = serializers.DecimalField(
        source="product.qty_on_hand", max_digits=12, decimal_places=2, read_only=True
    )
    current_avg_cost = serializers.DecimalField(
        source="product.avg_cost", max_digits=12, decimal_places=4, read_only=True
    )

    class Meta:
        model = AdjustmentLine
        fields = [
            "id", "product", "product_code", "product_name", "shelf_location",
            "on_hand", "current_avg_cost", "quantity", "unit_cost", "value",
        ]


class AdjustmentSerializer(DocumentSerializer):
    # A reversal moved every line the other way; its direction turns too.
    TURNED = ("total",)
    TURNED_ON_LINES = ("value",)

    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default=None, allow_null=True)
    direction = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()
    lines = AdjustmentLineSerializer(many=True, required=False)

    class Meta:
        model = Adjustment
        fields = DOCUMENT_FIELDS + [
            "reason", "direction", "supplier", "supplier_name", "total", "lines",
        ]
        read_only_fields = DOCUMENT_READ_ONLY

    @extend_schema_field(serializers.ChoiceField(choices=["IN", "OUT"]))
    def get_direction(self, obj):
        # The reason's direction, or the other way for a reversal.
        inbound = obj.is_inbound != bool(obj.reverses_id)
        return "IN" if inbound else "OUT"

    @extend_schema_field(money(allow_null=True))
    def get_total(self, obj):
        """The value that moved, signed: below zero for stock that went out.
        Null on a draft — line values are stamped at posting."""
        if not obj.is_posted:
            return None
        return str(sum((line.value for line in obj.lines.all()), Decimal("0.00")))

    def validate(self, attrs):
        reason = attrs.get("reason", getattr(self.instance, "reason", None))
        supplier = attrs.get("supplier", getattr(self.instance, "supplier", None))
        check_adjustment_header(reason, supplier)

        lines = attrs.get("lines")
        if lines is None and self.instance is not None:
            lines = [
                {"product": line.product, "unit_cost": line.unit_cost}
                for line in self.instance.lines.select_related("product")
            ]
        opening = reason == AdjustmentReason.OPENING_BALANCE
        seen = set()
        for line in lines or []:
            product, cost = line["product"], line.get("unit_cost")
            if opening:
                if not cost or cost <= 0:
                    raise serializers.ValidationError(
                        {"lines": f"{product.code}: an opening balance needs a unit cost."}
                    )
                if product.pk in seen:
                    raise serializers.ValidationError(
                        {"lines": f"{product.code} is listed twice."}
                    )
                if not opening_balance_allowed(product):
                    raise serializers.ValidationError(
                        {"lines": f"{product.code} already has stock movements, so "
                                  "it cannot take an opening balance."}
                    )
                seen.add(product.pk)
            elif cost is not None:
                raise serializers.ValidationError(
                    {"lines": f"{product.code}: unit cost is the current average and "
                              "cannot be typed. Only an opening balance sets its own."}
                )
        return attrs


# --- stock count -----------------------------------------------------------------

class StockCountLineSerializer(serializers.ModelSerializer):
    """Blind while counting: expected quantity and differences appear only
    once the count is posted."""

    product_code = serializers.CharField(source="product.code", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    shelf_location = serializers.CharField(source="product.shelf_location", read_only=True)
    unit_name = serializers.CharField(source="product.unit.name", read_only=True)

    # Sent as null, not left out, so the generated client's types stay true.
    HIDDEN_WHILE_COUNTING = ("expected_qty", "difference", "unit_cost", "value")

    class Meta:
        model = StockCountLine
        fields = [
            "id", "product", "product_code", "product_name", "shelf_location",
            "unit_name", "counted_qty", "counted_at",
            "expected_qty", "difference", "unit_cost", "value",
        ]
        read_only_fields = fields

    def to_representation(self, line):
        data = super().to_representation(line)
        if not line.document.is_posted:
            for field in self.HIDDEN_WHILE_COUNTING:
                data[field] = None
        return data


class StockCountSerializer(DocumentSerializer):
    # A reversal undid each difference. Counted and expected swap, so a line
    # still reads counted − expected = difference.
    TURNED = ("total",)
    TURNED_ON_LINES = ("difference", "value")
    SWAPPED_ON_LINES = (("counted_qty", "expected_qty"),)

    category_name = serializers.CharField(source="category.full_name", read_only=True)
    counted_by_name = serializers.CharField(source="counted_by.full_name", read_only=True, default=None, allow_null=True)
    lines_total = serializers.SerializerMethodField()
    lines_counted = serializers.SerializerMethodField()
    differences = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()
    lines = StockCountLineSerializer(many=True, read_only=True)

    class Meta:
        model = StockCount
        fields = DOCUMENT_FIELDS + [
            "category", "category_name", "counted_by", "counted_by_name",
            "lines_total", "lines_counted", "differences", "total", "lines",
        ]
        read_only_fields = DOCUMENT_READ_ONLY

    @extend_schema_field(serializers.IntegerField())
    def get_lines_total(self, obj):
        return len(obj.lines.all())

    @extend_schema_field(serializers.IntegerField())
    def get_lines_counted(self, obj):
        return sum(1 for line in obj.lines.all() if line.counted_qty is not None)

    # Blind while counting, like the lines: both are null until posted.
    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_differences(self, obj):
        """How many counted lines did not match what the system held."""
        if not obj.is_posted:
            return None
        return sum(1 for line in obj.lines.all() if line.difference)

    @extend_schema_field(money(allow_null=True))
    def get_total(self, obj):
        """The value the differences moved, signed: below zero for a net shortage."""
        if not obj.is_posted:
            return None
        return str(sum(
            (line.value for line in obj.lines.all() if line.value is not None), Decimal("0.00")
        ))

    def validate(self, attrs):
        if self.instance and "category" in attrs and attrs["category"] != self.instance.category:
            raise serializers.ValidationError(
                {"category": "The category is fixed once a count has started. Abandon it and start another."}
            )
        return attrs

    def create(self, validated):
        return start_count(
            validated["category"],
            self.context["request"].user,
            counted_by=validated.get("counted_by"),
            doc_date=validated.get("doc_date"),
            note=validated.get("note", ""),
        )


class CountEntrySerializer(serializers.Serializer):
    line = serializers.IntegerField()
    counted_qty = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0"), allow_null=True
    )


class RecordCountsSerializer(serializers.Serializer):
    lines = CountEntrySerializer(many=True)


class ReverseSerializer(serializers.Serializer):
    note = serializers.CharField(help_text="Why the document is being reversed.")


class ImportSerializer(serializers.Serializer):
    file = serializers.FileField()
    has_header = serializers.BooleanField(default=True)
    replace = serializers.BooleanField(
        default=False, help_text="Replace the lines already on the draft instead of adding to them."
    )
    commit = serializers.BooleanField(
        default=False, help_text="False checks the file and adds nothing."
    )


class ImportRowSerializer(serializers.Serializer):
    row = serializers.IntegerField(help_text="Spreadsheet row number.")
    code = serializers.CharField()
    product = serializers.IntegerField(allow_null=True)
    product_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    unit_cost = serializers.DecimalField(max_digits=12, decimal_places=4, allow_null=True)
    pack_size = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    pack_unit = serializers.IntegerField(allow_null=True)
    result = serializers.CharField(help_text='"Matched", or what is wrong with the row.')


class ImportResultSerializer(serializers.Serializer):
    rows = ImportRowSerializer(many=True)
    ready = serializers.IntegerField()
    to_fix = serializers.IntegerField()
    imported = serializers.IntegerField()


# --- ledger ------------------------------------------------------------------------

class StockMovementSerializer(serializers.ModelSerializer):
    product_code = serializers.CharField(source="product.code", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    posted_by_name = serializers.CharField(source="posted_by.full_name", read_only=True, default=None, allow_null=True)
    value = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = StockMovement
        fields = [
            "id", "product", "product_code", "product_name",
            "doc_type", "doc_id", "doc_number", "reason", "is_reversal",
            "movement_date", "quantity", "unit_cost", "value",
            "qty_after", "avg_cost_after", "posted_at", "posted_by_name",
        ]
        read_only_fields = fields
