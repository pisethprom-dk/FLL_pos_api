# v1.0.0
from django.contrib import admin

from warranty.models import WarrantyClaim


@admin.register(WarrantyClaim)
class WarrantyClaimAdmin(admin.ModelAdmin):
    list_display = ["warranty_number", "product_code", "product_name", "customer_name", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["warranty_number", "product_code", "product_name", "customer_name", "customer_phone"]
