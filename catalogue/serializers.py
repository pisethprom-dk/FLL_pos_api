# v1.0.5
from rest_framework import serializers

from catalogue.models import Brand, Category, Product, Unit
from users.scopes import has_scope


def _can_see_cost(serializer):
    """Cost and stock value are for cost.view (Admin) only, as on sales."""
    request = serializer.context.get("request")
    return bool(request and has_scope(request.user, "cost.view"))


class CategorySerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    parent_name = serializers.CharField(source="parent.name", read_only=True, default=None, allow_null=True)

    class Meta:
        model = Category
        fields = [
            "id", "code", "name", "name_kh", "parent", "parent_name", "full_name",
            "description", "image", "display_order", "is_active",
        ]

    def validate_parent(self, value):
        if value is None:
            return value
        if value.parent_id is not None:
            raise serializers.ValidationError(
                "Only a top-level category can be a parent — two levels is the limit."
            )
        if self.instance and value.pk == self.instance.pk:
            raise serializers.ValidationError("A category cannot be its own parent.")
        return value

    def validate(self, attrs):
        # Moving a parent under someone else would create a third level.
        if self.instance and attrs.get("parent") and self.instance.children.exists():
            raise serializers.ValidationError(
                {"parent": "This category has sub-categories, so it must stay top level."}
            )
        return attrs


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = [
            "id", "name", "name_kh", "country", "logo",
            "display_order", "is_active",
        ]


class UnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unit
        fields = ["id", "code", "name", "name_kh", "display_order", "is_active"]


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.full_name", read_only=True)
    brand_name = serializers.CharField(source="brand.name", read_only=True, default=None, allow_null=True)
    unit_name = serializers.CharField(source="unit.name", read_only=True)
    # Declared, not left to the model, so the schema can say null: a Seller gets
    # null here (to_representation), never the number and never a missing key.
    avg_cost = serializers.DecimalField(
        max_digits=12, decimal_places=4, read_only=True, allow_null=True
    )
    stock_value = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True, allow_null=True
    )
    needs_reorder = serializers.BooleanField(read_only=True)
    is_out_of_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id", "code", "barcode", "name", "name_kh", "short_name",
            "category", "category_name", "brand", "brand_name",
            "model_no", "description", "image",
            "unit", "unit_name", "retail_price", "wholesale_price", "is_price_fixed",
            "track_stock", "qty_on_hand", "avg_cost", "stock_value",
            "reorder_level", "reorder_qty", "shelf_location",
            "needs_reorder", "is_out_of_stock",
            "warranty_months", "origin_country",
            "notes", "display_order", "is_active",
        ]
        # Quantity and cost are the ledger's, never typed on this form.
        read_only_fields = ["id", "qty_on_hand"]

    def to_representation(self, product):
        data = super().to_representation(product)
        if not _can_see_cost(self):
            data["avg_cost"] = None
            data["stock_value"] = None
        return data

    def validate_barcode(self, value):
        return value or None

    def validate(self, attrs):
        track = attrs.get(
            "track_stock", self.instance.track_stock if self.instance else True
        )
        if (
            not track and self.instance and self.instance.track_stock
            and (self.instance.qty_on_hand or self.instance.movements.exists())
        ):
            raise serializers.ValidationError(
                {"track_stock": "This product has stock history, so it must keep tracking stock."}
            )
        if not track:
            for field in ("reorder_level", "reorder_qty"):
                if attrs.get(field):
                    raise serializers.ValidationError(
                        {field: "A product that does not track stock has no reorder level."}
                    )
        return attrs


class ProductLookupSerializer(serializers.ModelSerializer):
    """Small payload for the till and the stock document line pickers."""

    brand_name = serializers.CharField(source="brand.name", read_only=True, default=None, allow_null=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    unit_name = serializers.CharField(source="unit.name", read_only=True)

    class Meta:
        model = Product
        fields = [
            "id", "code", "barcode", "name", "short_name", "model_no",
            "brand_name", "category_name", "unit_name", "shelf_location",
            "retail_price", "wholesale_price", "is_price_fixed",
            "track_stock", "qty_on_hand", "warranty_months",
        ]
