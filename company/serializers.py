# v1.0.3
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from company.models import CompanyProfile, DocumentCounter, ExchangeRate, PaymentNote
from company.services import rate_is_in_use


class CompanyProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanyProfile
        exclude = ["created_by", "updated_by"]
        read_only_fields = ["id", "created_at", "updated_at"]


class CompanyBrandSerializer(serializers.ModelSerializer):
    """What the sign-in page may show before anyone signs in: what is printed on
    every receipt anyway, and nothing else."""

    class Meta:
        model = CompanyProfile
        fields = ["name", "name_kh", "address", "logo"]
        read_only_fields = fields


class ExchangeRateSerializer(serializers.ModelSerializer):
    is_in_use = serializers.SerializerMethodField()
    # A rate nobody set (seed's opening rate) has no created_by: send null rather
    # than leaving the field out, so the schema — and the Angular type — stay true.
    set_by = serializers.CharField(
        source="created_by.full_name", read_only=True, default=None, allow_null=True
    )

    class Meta:
        model = ExchangeRate
        fields = [
            "id", "effective_date", "rate", "note",
            "is_in_use", "set_by", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    @extend_schema_field(serializers.BooleanField())
    def get_is_in_use(self, obj):
        return rate_is_in_use(obj)

    def validate(self, attrs):
        # Editing is only refused once documents depend on the rate.
        if self.instance and rate_is_in_use(self.instance):
            raise serializers.ValidationError(
                "This rate has sales against it. Set a new rate with a later "
                "effective date instead."
            )
        return attrs


class CurrentRateSerializer(serializers.Serializer):
    """Everything the till needs about money in one call."""

    base_currency = serializers.CharField()
    effective_date = serializers.DateField()
    rate = serializers.DecimalField(max_digits=18, decimal_places=6)
    decimals = serializers.DictField(child=serializers.IntegerField())
    rounding_step = serializers.DictField(child=serializers.CharField())
    symbol = serializers.DictField(child=serializers.CharField())
    notes = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))


class DocumentCounterSerializer(serializers.ModelSerializer):
    next_value = serializers.CharField(source="peek", read_only=True)

    class Meta:
        model = DocumentCounter
        fields = ["id", "doc_type", "prefix", "next_number", "next_value"]
        read_only_fields = ["id", "doc_type", "next_number"]


class PaymentNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentNote
        fields = [
            "id", "payment_type", "payment_info", "image",
            "row_order", "is_active",
        ]
