# v1.0.0 — view only. Posting goes through inventory/services.py, which the
# admin's save would bypass.
from django.contrib import admin

from inventory.models import (
    Adjustment,
    AdjustmentLine,
    StockCount,
    StockCountLine,
    StockIn,
    StockInLine,
    StockMovement,
)


class ViewOnly:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class StockInLineInline(ViewOnly, admin.TabularInline):
    model = StockInLine


class AdjustmentLineInline(ViewOnly, admin.TabularInline):
    model = AdjustmentLine


class StockCountLineInline(ViewOnly, admin.TabularInline):
    model = StockCountLine


@admin.register(StockIn)
class StockInAdmin(ViewOnly, admin.ModelAdmin):
    list_display = ["number", "doc_date", "supplier", "supplier_ref", "status"]
    list_filter = ["status", "supplier"]
    search_fields = ["number", "supplier_ref"]
    inlines = [StockInLineInline]


@admin.register(Adjustment)
class AdjustmentAdmin(ViewOnly, admin.ModelAdmin):
    list_display = ["number", "doc_date", "reason", "supplier", "status"]
    list_filter = ["status", "reason"]
    search_fields = ["number", "note"]
    inlines = [AdjustmentLineInline]


@admin.register(StockCount)
class StockCountAdmin(ViewOnly, admin.ModelAdmin):
    list_display = ["number", "doc_date", "category", "counted_by", "status"]
    list_filter = ["status"]
    search_fields = ["number"]
    inlines = [StockCountLineInline]


@admin.register(StockMovement)
class StockMovementAdmin(ViewOnly, admin.ModelAdmin):
    list_display = [
        "id", "movement_date", "doc_number", "product", "quantity",
        "unit_cost", "qty_after", "avg_cost_after",
    ]
    list_filter = ["doc_type", "reason", "is_reversal"]
    search_fields = ["doc_number", "product__code", "product__name"]
