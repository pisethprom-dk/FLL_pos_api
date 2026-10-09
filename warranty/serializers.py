# v1.0.0 — DRF trims text and refuses a blank card number or product name.
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from warranty.models import WarrantyClaim


class WarrantyClaimSerializer(serializers.ModelSerializer):
    out_of_warranty = serializers.SerializerMethodField(
        help_text="Claimed after the warranty's end date."
    )
    logged_by_name = serializers.CharField(
        source="created_by.full_name", read_only=True, default=None, allow_null=True
    )
    changed_by_name = serializers.CharField(
        source="updated_by.full_name", read_only=True, default=None, allow_null=True,
        help_text="Null until the claim is edited.",
    )

    class Meta:
        model = WarrantyClaim
        fields = [
            "id", "warranty_number", "product_code", "product_name", "warranty_months",
            "expiry_date", "customer_name", "customer_phone", "note", "status",
            "out_of_warranty", "created_at", "logged_by_name", "updated_at", "changed_by_name",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    @extend_schema_field(serializers.BooleanField())
    def get_out_of_warranty(self, obj):
        return obj.out_of_warranty
