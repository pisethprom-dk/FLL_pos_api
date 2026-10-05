# v1.0.0
from django.contrib import admin

from company.models import CompanyProfile, DocumentCounter, ExchangeRate, PaymentNote


@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    list_display = ["name", "phone", "vat_tin"]

    def has_add_permission(self, request):
        return not CompanyProfile.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ["effective_date", "rate", "created_by", "created_at"]
    list_filter = ["effective_date"]


@admin.register(DocumentCounter)
class DocumentCounterAdmin(admin.ModelAdmin):
    list_display = ["doc_type", "prefix", "next_number"]
    readonly_fields = ["doc_type"]


@admin.register(PaymentNote)
class PaymentNoteAdmin(admin.ModelAdmin):
    list_display = ["row_order", "payment_type", "is_active"]
    list_filter = ["is_active"]
