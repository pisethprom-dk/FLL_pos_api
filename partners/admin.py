# v1.0.0
from django.contrib import admin

from partners.models import Customer, ProductSupplier, Supplier


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = [
        "code", "name", "price_tier", "allow_credit", "credit_limit",
        "credit_hold", "is_active",
    ]
    list_filter = ["is_active", "price_tier", "allow_credit", "credit_hold"]
    search_fields = ["code", "name", "name_kh", "phone"]
    readonly_fields = ["is_system"]

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_system:
            return False
        return super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        # The bulk delete action skips Customer.delete(), so the walk-in is
        # left out here instead.
        queryset.exclude(is_system=True).delete()


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "supplier_type", "phone", "province", "is_active"]
    list_filter = ["is_active", "supplier_type"]
    search_fields = ["code", "name", "name_kh", "phone"]


@admin.register(ProductSupplier)
class ProductSupplierAdmin(admin.ModelAdmin):
    list_display = ["product", "supplier", "supplier_sku", "pack_size", "is_preferred"]
    list_filter = ["is_preferred", "supplier"]
    search_fields = ["product__code", "product__name", "supplier__name", "supplier_sku"]
