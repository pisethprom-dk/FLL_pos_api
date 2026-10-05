# v1.0.2
from django.db.models import F, Q
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
from core.views import ActiveFilterMixin
from users.permissions import IsAdminOrReadOnly


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


class BrandViewSet(ActiveFilterMixin, viewsets.ModelViewSet):
    queryset = Brand.objects.all()
    serializer_class = BrandSerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


class UnitViewSet(ActiveFilterMixin, viewsets.ModelViewSet):
    queryset = Unit.objects.all()
    serializer_class = UnitSerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "patch", "head", "options"]


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

    @action(detail=False, methods=["get"])
    def lookup(self, request):
        """Small payload for the till and stock line pickers."""
        qs = self.filter_queryset(self.get_queryset()).filter(is_active=True)[:50]
        return Response(ProductLookupSerializer(qs, many=True).data)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)
