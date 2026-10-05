# v1.0.0
from rest_framework import serializers

from company.models import CompanyProfile, DocumentCounter, ExchangeRate, PaymentNote
from company.services import rate_is_in_use


class CompanyProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanyProfile
        exclude = ["created_by", "updated_by"]
        read_only_fields = ["id", "created_at", "updated_at"]


class ExchangeRateSerializer(serializers.ModelSerializer):
    is_in_use = serializers.SerializerMethodField()
    set_by = serializers.CharField(source="created_by.full_name", read_only=True)

    class Meta:
        model = ExchangeRate
        fields = [
            "id", "effective_date", "rate", "note",
            "is_in_use", "set_by", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

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
