# v1.0.0 — /api/sales/
from django.urls import path
from rest_framework.routers import DefaultRouter

from sales.views import (
    CustomerAccountView,
    InvoiceViewSet,
    PaymentViewSet,
    QuotationViewSet,
    ReturnViewSet,
)

router = DefaultRouter()
router.register("quotations", QuotationViewSet, basename="quotation")
router.register("invoices", InvoiceViewSet, basename="invoice")
router.register("payments", PaymentViewSet, basename="payment")
router.register("returns", ReturnViewSet, basename="return")

urlpatterns = router.urls + [
    path("customers/<int:pk>/account/", CustomerAccountView.as_view(), name="customer-account"),
]
