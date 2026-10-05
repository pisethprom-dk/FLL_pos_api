# v1.0.1 — /api/company/
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from company.views import (
    CompanyBrandView,
    CompanyProfileView,
    CurrentRateView,
    DocumentCounterViewSet,
    ExchangeRateViewSet,
    PaymentNoteViewSet,
)

router = DefaultRouter()
router.register("exchange-rates", ExchangeRateViewSet, basename="exchange-rate")
router.register("numbering", DocumentCounterViewSet, basename="numbering")
router.register("payment-notes", PaymentNoteViewSet, basename="payment-note")

urlpatterns = [
    path("brand/", CompanyBrandView.as_view(), name="company-brand"),
    path("profile/", CompanyProfileView.as_view(), name="company-profile"),
    path("rate/", CurrentRateView.as_view(), name="company-rate"),
    path("", include(router.urls)),
]
