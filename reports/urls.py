# v1.3.0 — /api/reports/
from django.urls import path

from reports.views import DailySalesView, DashboardView, ReceivablesView, StockOnHandView

urlpatterns = [
    path("daily-sales/", DailySalesView.as_view(), name="report-daily-sales"),
    path("stock-on-hand/", StockOnHandView.as_view(), name="report-stock-on-hand"),
    path("receivables/", ReceivablesView.as_view(), name="report-receivables"),
    path("dashboard/", DashboardView.as_view(), name="report-dashboard"),
]
