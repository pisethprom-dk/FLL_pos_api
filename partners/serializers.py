# v1.0.1
import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from partners.models import Customer, ProductSupplier, Supplier

PARTNER_FIELDS = [
    "id", "code", "name", "name_kh", "short_name", "display_order", "is_active",
    "contact_person", "position", "phone", "phone_alt", "telegram", "email",
    "address", "district", "province", "country",
    "notes",
]


class ModelCleanMixin:
    """Runs the model's clean() on the instance as it would be saved.

    The rules are written once, on the model, and hold for the API the same as
    for the Django admin.
    """

    def validate(self, attrs):
        attrs = super().validate(attrs)
        obj = copy.copy(self.instance) if self.instance else self.Meta.model()
        for field, value in attrs.items():
            setattr(obj, field, value)
        try:
            obj.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict)
        return attrs


CREDIT_STATUS = ["NO", "YES", "HOLD"]


class CustomerSerializer(ModelCleanMixin, serializers.ModelSerializer):
    credit_status = serializers.ChoiceField(choices=CREDIT_STATUS, read_only=True)

    class Meta:
        model = Customer
        fields = PARTNER_FIELDS + [
            "price_tier", "is_system",
            "allow_credit", "credit_limit", "payment_terms_days", "credit_hold",
            "credit_status",
        ]
        # What a customer owes is worked out from invoices and payments, so
        # there is no balance field here to send.
        read_only_fields = ["id", "is_system"]


class CustomerLookupSerializer(serializers.ModelSerializer):
    """Small payload for the till's Choose customer dialog."""

    credit_status = serializers.ChoiceField(choices=CREDIT_STATUS, read_only=True)

    class Meta:
        model = Customer
        fields = [
            "id", "code", "name", "short_name", "phone", "is_system",
            "price_tier", "credit_status", "credit_limit", "payment_terms_days",
        ]


class SupplierSerializer(ModelCleanMixin, serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = PARTNER_FIELDS + ["supplier_type", "website", "logo"]
        read_only_fields = ["id"]


class ProductSupplierSerializer(ModelCleanMixin, serializers.ModelSerializer):
    product_code = serializers.CharField(source="product.code", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    supplier_code = serializers.CharField(source="supplier.code", read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)

    class Meta:
        model = ProductSupplier
        fields = [
            "id", "product", "product_code", "product_name",
            "supplier", "supplier_code", "supplier_name",
            "supplier_sku", "pack_size", "is_preferred", "notes",
        ]
