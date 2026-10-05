# v1.0.0 — view only. Completing, voiding and posting go through
# sales/services.py, which the admin's save would bypass.
from django.contrib import admin

from sales.models import (
    CustomerPayment,
    Invoice,
    InvoiceLine,
    InvoiceTender,
    PaymentAllocation,
    Quotation,
    QuotationLine,
    ReturnLine,
    SalesReturn,
)


class ViewOnly:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class QuotationLineInline(ViewOnly, admin.TabularInline):
    model = QuotationLine


class InvoiceLineInline(ViewOnly, admin.TabularInline):
    model = InvoiceLine


class InvoiceTenderInline(ViewOnly, admin.TabularInline):
    model = InvoiceTender


class PaymentAllocationInline(ViewOnly, admin.TabularInline):
    model = PaymentAllocation


class ReturnLineInline(ViewOnly, admin.TabularInline):
    model = ReturnLine


@admin.register(Quotation)
class QuotationAdmin(ViewOnly, admin.ModelAdmin):
    list_display = ["number", "quote_date", "customer", "valid_until", "status"]
    list_filter = ["status"]
    search_fields = ["number", "customer__name"]
    inlines = [QuotationLineInline]


@admin.register(Invoice)
class InvoiceAdmin(ViewOnly, admin.ModelAdmin):
    list_display = ["number", "sale_date", "customer", "seller", "total", "on_credit", "status"]
    list_filter = ["status", "seller"]
    search_fields = ["number", "customer__name", "walk_in_name"]
    inlines = [InvoiceLineInline, InvoiceTenderInline]


@admin.register(CustomerPayment)
class CustomerPaymentAdmin(ViewOnly, admin.ModelAdmin):
    list_display = ["number", "payment_date", "customer", "tender", "amount", "status"]
    list_filter = ["status", "tender"]
    search_fields = ["number", "customer__name", "reference"]
    inlines = [PaymentAllocationInline]


@admin.register(SalesReturn)
class SalesReturnAdmin(ViewOnly, admin.ModelAdmin):
    list_display = ["number", "return_date", "invoice", "customer", "total", "status"]
    list_filter = ["status"]
    search_fields = ["number", "invoice__number"]
    inlines = [ReturnLineInline]
