# v1.0.2 — /api/inventory/. The whole stock area is Admin only, via scopes.
from django.db.models import Prefetch, Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from core.schema import (
    DATE_FROM,
    DATE_TO,
    PRODUCT,
    SEARCH,
    SUPPLIER,
    query,
    status_filter,
)
from core.views import AuditMixin
from inventory.imports import import_lines
from inventory.models import (
    Adjustment,
    AdjustmentLine,
    StockCount,
    StockCountLine,
    StockIn,
    StockInLine,
    StockMovement,
)
from inventory.serializers import (
    AdjustmentSerializer,
    ImportResultSerializer,
    ImportSerializer,
    RecordCountsSerializer,
    ReverseSerializer,
    StockCountSerializer,
    StockInSerializer,
    StockMovementSerializer,
)
from inventory.services import (
    post_adjustment,
    post_count,
    post_stock_in,
    record_counts,
    reverse_document,
)
from users.permissions import HasReadWriteScope


DOCUMENT_FILTERS = [status_filter("DRAFT", "POSTED"), DATE_FROM, DATE_TO, SEARCH]


def document_schema(serializer, extra_filters=(), importable=True, counts=False):
    """Schema for a stock document viewset: its filters, and what each action
    takes and returns. The actions live on DocumentViewSet, but each subclass
    returns its own document."""
    actions = {
        "list": extend_schema(parameters=DOCUMENT_FILTERS + list(extra_filters)),
        "post_document": extend_schema(request=None, responses={200: serializer}),
        "reverse": extend_schema(request=ReverseSerializer, responses={201: serializer}),
    }
    if importable:
        actions["import_file"] = extend_schema(
            request={"multipart/form-data": ImportSerializer},
            responses={200: ImportResultSerializer},
        )
    if counts:
        actions["record"] = extend_schema(request=RecordCountsSerializer, responses={200: serializer})
    return extend_schema_view(**actions)


class StockAccess:
    permission_classes = [HasReadWriteScope]
    read_scope = "stock.view"
    write_scope = "stock.post"


class DocumentViewSet(StockAccess, AuditMixin, viewsets.ModelViewSet):
    """Drafts are created, edited and deleted freely. Posting and reversing
    are actions; a posted document refuses PATCH and DELETE."""

    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    post_service = None

    def get_queryset(self):
        qs = super().get_queryset().select_related("posted_by", "reverses", "reversed_by")
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"].upper())
        if p.get("date_from"):
            qs = qs.filter(doc_date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(doc_date__lte=p["date_to"])
        if p.get("search"):
            qs = qs.filter(Q(number__icontains=p["search"]) | Q(note__icontains=p["search"]))
        return qs

    def perform_destroy(self, instance):
        instance.delete()  # the model refuses a posted document

    def _detail(self, doc, status_code=status.HTTP_200_OK):
        # The document's own serializer, whichever action is running.
        doc = self.get_queryset().get(pk=doc.pk)
        serializer = type(self).serializer_class(doc, context=self.get_serializer_context())
        return Response(serializer.data, status=status_code)

    # Not named `post`: a method of that name would answer every POST to the
    # detail URL, and a stray POST would post the document to stock.
    @action(detail=True, methods=["post"], url_path="post")
    def post_document(self, request, pk=None):
        doc = type(self).post_service(self.get_object(), request.user)
        return self._detail(doc)

    @action(detail=True, methods=["post"], serializer_class=ReverseSerializer)
    def reverse(self, request, pk=None):
        body = ReverseSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        reversal = reverse_document(self.get_object(), request.user, body.validated_data["note"])
        return self._detail(reversal, status.HTTP_201_CREATED)


class ImportMixin:
    @action(
        detail=True, methods=["post"], url_path="import",
        serializer_class=ImportSerializer,
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def import_file(self, request, pk=None):
        """Check a CSV or Excel file against this draft; with commit=true,
        add the rows that passed."""
        body = ImportSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        result = import_lines(
            self.get_object(),
            body.validated_data["file"],
            has_header=body.validated_data["has_header"],
            replace=body.validated_data["replace"],
            commit=body.validated_data["commit"],
        )
        return Response(ImportResultSerializer(result).data)


@document_schema(StockInSerializer, [SUPPLIER])
class StockInViewSet(ImportMixin, DocumentViewSet):
    queryset = StockIn.objects.select_related("supplier").prefetch_related(
        Prefetch("lines", StockInLine.objects.select_related(
            "product__unit", "product__brand", "product__category", "pack_unit"
        ))
    )
    serializer_class = StockInSerializer
    post_service = post_stock_in

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("supplier"):
            qs = qs.filter(supplier_id=self.request.query_params["supplier"])
        return qs


@document_schema(AdjustmentSerializer, [
    SUPPLIER,
    query("reason", str, "Adjustment reason.", enum=[
        "DAMAGE", "LOSS", "SHOP_USE", "WARRANTY_REPLACEMENT", "RETURN_TO_SUPPLIER",
        "SUPPLIER_REPLACEMENT", "OPENING_BALANCE",
    ]),
])
class AdjustmentViewSet(ImportMixin, DocumentViewSet):
    queryset = Adjustment.objects.select_related("supplier").prefetch_related(
        Prefetch("lines", AdjustmentLine.objects.select_related("product__brand", "product__category"))
    )
    serializer_class = AdjustmentSerializer
    post_service = post_adjustment

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("reason"):
            qs = qs.filter(reason=p["reason"].upper())
        if p.get("supplier"):
            qs = qs.filter(supplier_id=p["supplier"])
        return qs


@document_schema(
    StockCountSerializer, [query("category", int, "Category id.")], importable=False, counts=True,
)
class StockCountViewSet(DocumentViewSet):
    """POST starts a count for a category. DELETE on a draft abandons it."""

    queryset = StockCount.objects.select_related("category__parent", "counted_by").prefetch_related(
        Prefetch(
            "lines",
            StockCountLine.objects.select_related(
                "product__unit", "product__brand", "product__category", "document"
            ),
        )
    )
    serializer_class = StockCountSerializer
    post_service = post_count

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("category"):
            qs = qs.filter(category_id=self.request.query_params["category"])
        return qs

    @action(detail=True, methods=["post"], serializer_class=RecordCountsSerializer)
    def record(self, request, pk=None):
        """Enter counted quantities: {"lines": [{"line": id, "counted_qty": "5"}]}.
        A null counted_qty clears the line."""
        body = RecordCountsSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        count = record_counts(self.get_object(), body.validated_data["lines"])
        return self._detail(count)


@extend_schema_view(list=extend_schema(parameters=[
    PRODUCT, DATE_FROM, DATE_TO,
    query("doc_type", str, "Source document type, e.g. STOCK_IN, ADJUSTMENT, COUNT, INVOICE, RETURN."),
    query("doc_number", str, "Source document number, e.g. GRN-000312."),
    query("reason", str, "Adjustment reason, or VOID for an invoice void."),
]))
class StockMovementViewSet(StockAccess, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                           viewsets.GenericViewSet):
    """The ledger. Filter by product for a stock card."""

    queryset = StockMovement.objects.select_related("product", "posted_by")
    serializer_class = StockMovementSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("product"):
            qs = qs.filter(product_id=p["product"])
        if p.get("doc_type"):
            qs = qs.filter(doc_type=p["doc_type"].upper())
        if p.get("doc_number"):
            qs = qs.filter(doc_number=p["doc_number"])
        if p.get("reason"):
            qs = qs.filter(reason=p["reason"].upper())
        if p.get("date_from"):
            qs = qs.filter(movement_date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(movement_date__lte=p["date_to"])
        return qs
