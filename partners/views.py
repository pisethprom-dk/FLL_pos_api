# v1.0.2
from django.db.models import Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.schema import ACTIVE, PRODUCT, SEARCH, SUPPLIER, flag, query
from core.views import ActiveFilterMixin, AuditMixin
from partners.models import Customer, ProductSupplier, Supplier
from partners.serializers import (
    CustomerLookupSerializer,
    CustomerSerializer,
    ProductSupplierSerializer,
    SupplierSerializer,
)
from users.permissions import IsAdminOrReadOnly

SEARCHED = (
    "code", "name", "name_kh", "short_name",
    "contact_person", "phone", "phone_alt", "telegram",
)


def search(qs, term):
    q = Q()
    for field in SEARCHED:
        q |= Q(**{f"{field}__icontains": term})
    return qs.filter(q)


CUSTOMER_FILTERS = [
    ACTIVE, SEARCH,
    query("price_tier", str, "Price tier.", enum=["RETAIL", "WHOLESALE"]),
    query("credit", str, "yes: allowed credit (on hold included); no: cash only; hold: on credit hold.",
          enum=["yes", "no", "hold"]),
]


@extend_schema_view(list=extend_schema(parameters=CUSTOMER_FILTERS))
class CustomerViewSet(AuditMixin, ActiveFilterMixin, viewsets.ModelViewSet):
    """No delete: customers have sales against them, so they are deactivated."""

    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params

        if p.get("price_tier"):
            qs = qs.filter(price_tier=p["price_tier"].upper())

        # yes includes customers on hold — hold blocks new credit sales but
        # they are still credit customers with a limit.
        credit = p.get("credit")
        if credit == "yes":
            qs = qs.filter(allow_credit=True)
        elif credit == "no":
            qs = qs.filter(allow_credit=False)
        elif credit == "hold":
            qs = qs.filter(credit_hold=True)

        if p.get("search"):
            qs = search(qs, p["search"])
        return qs

    @extend_schema(parameters=CUSTOMER_FILTERS, responses={200: CustomerLookupSerializer(many=True)})
    @action(detail=False, methods=["get"], pagination_class=None)
    def lookup(self, request):
        """Small payload for the till. Active only, walk-in first."""
        qs = (
            self.filter_queryset(self.get_queryset())
            .filter(is_active=True)
            .order_by("-is_system", "display_order", "code")[:50]
        )
        return Response(CustomerLookupSerializer(qs, many=True).data)


@extend_schema_view(list=extend_schema(parameters=[
    ACTIVE, SEARCH,
    query("supplier_type", str, "Supplier type.", enum=[
        "MANUFACTURER", "IMPORTER", "DISTRIBUTOR", "WHOLESALER", "SERVICE_CENTRE", "LOCAL_MARKET",
    ]),
]))
class SupplierViewSet(AuditMixin, ActiveFilterMixin, viewsets.ModelViewSet):
    """No delete: past stock-ins keep their supplier, so it is deactivated."""

    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("supplier_type"):
            qs = qs.filter(supplier_type=p["supplier_type"].upper())
        if p.get("search"):
            qs = search(qs, p["search"])
        return qs


@extend_schema_view(list=extend_schema(parameters=[
    PRODUCT, SUPPLIER, flag("preferred", "Preferred links only."),
]))
class ProductSupplierViewSet(AuditMixin, viewsets.ModelViewSet):
    """Links may be deleted — they carry no history."""

    queryset = ProductSupplier.objects.select_related("product__unit", "supplier", "pack_unit")
    serializer_class = ProductSupplierSerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("product"):
            qs = qs.filter(product_id=p["product"])
        if p.get("supplier"):
            qs = qs.filter(supplier_id=p["supplier"])
        if p.get("preferred") == "true":
            qs = qs.filter(is_preferred=True)
        return qs
