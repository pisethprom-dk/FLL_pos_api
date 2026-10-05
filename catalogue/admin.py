# v1.0.0
from django.contrib import admin

from catalogue.models import Brand, Category, Product, Unit


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "parent", "display_order", "is_active"]
    list_filter = ["is_active", "parent"]
    search_fields = ["code", "name"]


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ["name", "country", "display_order", "is_active"]
    search_fields = ["name"]


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "display_order", "is_active"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = [
        "code", "name", "category", "brand", "shelf_location",
        "qty_on_hand", "avg_cost", "retail_price", "is_active",
    ]
    list_filter = ["is_active", "track_stock", "category", "brand"]
    search_fields = ["code", "barcode", "name", "model_no"]
    readonly_fields = ["qty_on_hand", "avg_cost"]
