# v1.0.3
from django.db.models import F, Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from catalogue.models import Brand, Category, Product, Unit
from catalogue.serializers import (
    BrandSerializer,
    CategorySerializer,
    ProductLookupSerializer,
    ProductSerializer,
    UnitSerializer,
)
from core.schema import ACTIVE, SEARCH, flag, query
from core.views import ActiveFilterMixin
from users.permissions import IsAdminOrReadOnly


PRODUCT_FILTERS = [
    ACTIVE, SEARCH,
    query("category", int, "Category id; a top-level one includes its sub-categories."),
    query("brand", int, "Brand id."),
    flag("below_reorder", "At or below the reorder level."),
    flag("out_of_stock", "Nothing on hand."),
]


@extend_schema_view(list=extend_schema(parameters=[
    ACTIVE, flag("top_level", "Top-level categories only."), query("parent", int, "Parent category id."),
]))
class CategoryViewSet(ActiveFilterMixin, viewsets.ModelViewSet):
    queryset = Category.objects.select_related("parent").all()
    serializer_class = CategorySerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("top_level") == "true":
            qs = qs.filter(parent__isnull=True)
        parent = self.request.query_params.get("parent")
        if parent:
            qs = qs.filter(parent_id=parent)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


@extend_schema_view(list=extend_schema(parameters=[ACTIVE]))
class BrandViewSet(ActiveFilterMixin, viewsets.ModelViewSet):
    queryset = Brand.objects.all()
    serializer_class = BrandSerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


@extend_schema_view(list=extend_schema(parameters=[ACTIVE]))
class UnitViewSet(ActiveFilterMixin, viewsets.ModelViewSet):
    queryset = Unit.objects.all()
    serializer_class = UnitSerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "patch", "head", "options"]


@extend_schema_view(list=extend_schema(parameters=PRODUCT_FILTERS))
class ProductViewSet(ActiveFilterMixin, viewsets.ModelViewSet):
    queryset = Product.objects.select_related("category", "category__parent", "brand", "unit")
    serializer_class = ProductSerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params

        if p.get("category"):
            qs = qs.filter(
                Q(category_id=p["category"]) | Q(category__parent_id=p["category"])
            )
        if p.get("brand"):
            qs = qs.filter(brand_id=p["brand"])
        if p.get("below_reorder") == "true":
            qs = qs.filter(track_stock=True, qty_on_hand__lte=F("reorder_level"))
        if p.get("out_of_stock") == "true":
            qs = qs.filter(track_stock=True, qty_on_hand__lte=0)

        search = p.get("search")
        if search:
            qs = qs.filter(
                Q(code__icontains=search)
                | Q(barcode__icontains=search)
                | Q(name__icontains=search)
                | Q(name_kh__icontains=search)
                | Q(model_no__icontains=search)
            )
        return qs

    @extend_schema(parameters=PRODUCT_FILTERS, responses={200: ProductLookupSerializer(many=True)})
    @action(detail=False, methods=["get"], pagination_class=None)
    def lookup(self, request):
        """Small payload for the till and stock line pickers."""
        qs = self.filter_queryset(self.get_queryset()).filter(is_active=True)[:50]
        return Response(ProductLookupSerializer(qs, many=True).data)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)
