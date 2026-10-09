# v1.3.0 — /api/reports/. Read only. Who may see what comes from scopes.py:
# an Admin every seller's sales (report.sales.all), a Seller only their own
# (report.sales.own); stock for both (report.stock); receivables for an Admin
# (report.receivables); cost only with cost.view. The dashboard is for anyone
# signed in, each part by the same scopes.
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.schema import DATE_FROM, DATE_TO, SEARCH, query
from reports.serializers import (
    DailySalesQuerySerializer,
    DailySalesSerializer,
    DashboardSerializer,
    ReceivablesQuerySerializer,
    ReceivablesSerializer,
    StockOnHandSerializer,
    StockQuerySerializer,
)
from reports.services import (
    RECEIVABLE_FILTERS,
    STOCK_FILTERS,
    daily_sales,
    dashboard,
    receivables,
    stock_on_hand,
)
from users.permissions import HasScope
from users.scopes import has_scope


class DailySalesView(APIView):
    """Completed sales by day, by seller and by tender, this month unless
    dates are given. A Seller's report is always their own."""

    permission_classes = [HasScope]
    required_scope = ("report.sales.all", "report.sales.own")

    @extend_schema(
        parameters=[
            DATE_FROM, DATE_TO,
            query("seller", int, "Seller (user) id — an Admin's filter; a Seller always sees their own."),
        ],
        responses={200: DailySalesSerializer},
    )
    def get(self, request):
        params = DailySalesQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        today = timezone.localdate()
        date_from = params.validated_data.get("date_from", today.replace(day=1))
        date_to = params.validated_data.get("date_to", today)
        if date_from > date_to:
            raise ValidationError({"date_from": "The first day comes after the last."})
        user = request.user
        if has_scope(user, "report.sales.all"):
            seller = params.validated_data.get("seller")
        else:
            seller = user.pk
        report = daily_sales(date_from, date_to, seller, with_cost=has_scope(user, "cost.view"))
        return Response(DailySalesSerializer(report).data)


class StockOnHandView(APIView):
    """What is on the shelf and its value at average cost, as at today. The
    summary covers all stock; the rows are those the filters keep."""

    permission_classes = [HasScope]
    required_scope = "report.stock"

    @extend_schema(
        parameters=[
            query("status", str, "below_reorder (out of stock included), out_of_stock, or "
                                  "no_movement (holding stock, nothing in or out for 90 days).",
                  enum=list(STOCK_FILTERS)),
            query("category", int, "Category id; its sub-categories too."),
            query("brand", int, "Brand id."),
            SEARCH,
        ],
        responses={200: StockOnHandSerializer},
    )
    def get(self, request):
        params = StockQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        v = params.validated_data
        report = stock_on_hand(
            status=v.get("status"), category=v.get("category"), brand=v.get("brand"),
            search=(v.get("search") or "").strip() or None,
            with_cost=has_scope(request.user, "cost.view"),
        )
        return Response(StockOnHandSerializer(report).data)


class ReceivablesView(APIView):
    """What store customers owe, aged by the days since each invoice, as at
    today. A customer's open invoices: /api/sales/customers/{id}/account/."""

    permission_classes = [HasScope]
    required_scope = "report.receivables"

    @extend_schema(
        parameters=[query("show", str, "overdue, over_limit or on_hold; everyone who owes when left out.",
                          enum=list(RECEIVABLE_FILTERS))],
        responses={200: ReceivablesSerializer},
    )
    def get(self, request):
        params = ReceivablesQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        return Response(ReceivablesSerializer(receivables(params.validated_data.get("show"))).data)


class DashboardView(APIView):
    """Today at a glance for whoever is signed in. A part they may not see
    is null; a Seller's sales are their own."""

    @extend_schema(responses={200: DashboardSerializer})
    def get(self, request):
        return Response(DashboardSerializer(dashboard(request.user)).data)
