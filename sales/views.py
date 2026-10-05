# v1.0.1 — /api/sales/
from decimal import Decimal

from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.schema import CUSTOMER, DATE_FROM, DATE_TO, SEARCH, flag, query, status_filter
from core.views import AuditMixin
from partners.models import Customer
from sales.models import (
    CustomerPayment,
    Invoice,
    InvoiceLine,
    PaymentAllocation,
    Quotation,
    QuotationLine,
    ReturnLine,
    SalesReturn,
)
from sales.money import Tender
from sales.serializers import (
    CompleteSerializer,
    CustomerAccountSerializer,
    CustomerPaymentSerializer,
    InvoiceSerializer,
    NoteSerializer,
    QuotationSerializer,
    ReasonSerializer,
    SalesReturnSerializer,
)
from sales.services import (
    accept_quote,
    complete_invoice,
    customer_balance,
    open_invoices,
    post_return,
    record_payment,
    reject_quote,
    send_quote,
    void_invoice,
    void_payment,
)
from users.permissions import CanVoidInvoice, HasReadWriteScope, IsAdmin


ZERO = Decimal("0.00")


class DateRangeMixin:
    date_field = None

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("date_from"):
            qs = qs.filter(**{f"{self.date_field}__gte": p["date_from"]})
        if p.get("date_to"):
            qs = qs.filter(**{f"{self.date_field}__lte": p["date_to"]})
        if p.get("customer"):
            qs = qs.filter(customer_id=p["customer"])
        if p.get("status"):
            qs = qs.filter(status=p["status"].upper())
        return qs


def list_filters(*statuses, extra=()):
    return [status_filter(*statuses), CUSTOMER, DATE_FROM, DATE_TO, *extra]


class SalesViewSet(AuditMixin, DateRangeMixin, viewsets.ModelViewSet):
    permission_classes = [HasReadWriteScope]

    def _detail(self, obj, status_code=status.HTTP_200_OK):
        obj = self.get_queryset().get(pk=obj.pk)
        serializer = type(self).serializer_class(obj, context=self.get_serializer_context())
        return Response(serializer.data, status=status_code)


@extend_schema_view(
    list=extend_schema(parameters=list_filters(
        "DRAFT", "SENT", "ACCEPTED", "REJECTED", "INVOICED",
        extra=[flag("open", "Draft, sent or accepted."), SEARCH],
    )),
    send=extend_schema(request=None, responses={200: QuotationSerializer}),
    accept=extend_schema(request=None, responses={200: QuotationSerializer}),
    reject=extend_schema(request=NoteSerializer, responses={200: QuotationSerializer}),
)
class QuotationViewSet(SalesViewSet):
    """Draft and sent quotations are edited freely; send, accept and reject
    are actions. Only a draft can be deleted."""

    queryset = Quotation.objects.select_related("customer", "created_by").prefetch_related(
        Prefetch("lines", QuotationLine.objects.select_related("product__unit"))
    )
    serializer_class = QuotationSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    read_scope = "quotation.view"
    write_scope = "quotation.edit"
    date_field = "quote_date"

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("open") == "true":
            qs = qs.filter(status__in=["DRAFT", "SENT", "ACCEPTED"])
        if self.request.query_params.get("search"):
            qs = qs.filter(number__icontains=self.request.query_params["search"])
        return qs

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        return self._detail(send_quote(self.get_object()))

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._detail(accept_quote(self.get_object()))

    @action(detail=True, methods=["post"], serializer_class=NoteSerializer)
    def reject(self, request, pk=None):
        body = NoteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return self._detail(reject_quote(self.get_object(), body.validated_data["note"]))


@extend_schema_view(
    list=extend_schema(parameters=list_filters(
        "HELD", "COMPLETED", "VOID",
        extra=[query("seller", int, "Seller (user) id."), query("quotation", int, "Quotation id."), SEARCH],
    )),
    complete=extend_schema(request=CompleteSerializer, responses={200: InvoiceSerializer}),
    void=extend_schema(request=ReasonSerializer, responses={200: InvoiceSerializer}),
)
class InvoiceViewSet(SalesViewSet):
    """POST makes a held sale (no number, no stock). PATCH edits it while
    held; DELETE cancels it. /complete/ finishes the sale; /void/ cancels a
    completed one."""

    queryset = Invoice.objects.select_related(
        "customer", "quotation", "seller", "voided_by"
    ).prefetch_related(
        Prefetch("lines", InvoiceLine.objects.select_related("product__unit", "invoice")),
        "tenders",
    )
    serializer_class = InvoiceSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    read_scope = "sell"
    write_scope = "sell"
    date_field = "sale_date__date"

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("seller"):
            qs = qs.filter(seller_id=p["seller"])
        if p.get("quotation"):
            qs = qs.filter(quotation_id=p["quotation"])
        if p.get("search"):
            qs = qs.filter(
                Q(number__icontains=p["search"]) | Q(walk_in_name__icontains=p["search"])
                | Q(hold_label__icontains=p["search"])
            )
        return qs

    def perform_destroy(self, instance):
        instance.delete()  # the model refuses anything but a held sale

    @action(detail=True, methods=["post"], serializer_class=CompleteSerializer)
    def complete(self, request, pk=None):
        body = CompleteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        tenders = [Tender(**t) for t in body.validated_data["tenders"]]
        return self._detail(complete_invoice(self.get_object(), tenders, request.user))

    @action(
        detail=True, methods=["post"], serializer_class=ReasonSerializer,
        permission_classes=[IsAuthenticated, CanVoidInvoice],
    )
    def void(self, request, pk=None):
        body = ReasonSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        invoice = self.get_object()  # CanVoidInvoice: Admin any, Seller own and same day
        return self._detail(void_invoice(invoice, request.user, body.validated_data["reason"]))


@extend_schema_view(
    list=extend_schema(parameters=list_filters("POSTED", "VOID")),
    void=extend_schema(request=ReasonSerializer, responses={200: CustomerPaymentSerializer}),
)
class PaymentViewSet(AuditMixin, DateRangeMixin, mixins.CreateModelMixin, mixins.ListModelMixin,
                     mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """A payment is applied in full when it is saved, and frozen after.
    Leave `allocations` out to apply it to the oldest invoices first."""

    queryset = CustomerPayment.objects.select_related(
        "customer", "created_by", "voided_by"
    ).prefetch_related(Prefetch("allocations", PaymentAllocation.objects.select_related("invoice")))
    serializer_class = CustomerPaymentSerializer
    permission_classes = [HasReadWriteScope]
    read_scope = "payment.view"
    write_scope = "payment.record"
    date_field = "payment_date"

    def create(self, request, *args, **kwargs):
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        payment = record_payment(
            customer=data["customer"],
            payment_date=data.get("payment_date") or timezone.localdate(),
            tender=data["tender"],
            currency=data.get("currency", "USD"),
            amount_tendered=data["amount_tendered"],
            reference=data.get("reference", ""),
            note=data.get("note", ""),
            allocations=data.get("allocations"),
            user=request.user,
        )
        obj = self.get_queryset().get(pk=payment.pk)
        return Response(self.get_serializer(obj).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True, methods=["post"], serializer_class=ReasonSerializer,
        permission_classes=[IsAdmin],
    )
    def void(self, request, pk=None):
        body = ReasonSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        payment = void_payment(self.get_object(), request.user, body.validated_data["reason"])
        obj = self.get_queryset().get(pk=payment.pk)
        return Response(CustomerPaymentSerializer(obj, context=self.get_serializer_context()).data)


@extend_schema_view(
    list=extend_schema(parameters=list_filters("DRAFT", "POSTED", extra=[query("invoice", int, "Invoice id.")])),
    post_return=extend_schema(request=None, responses={200: SalesReturnSerializer}),
)
class ReturnViewSet(SalesViewSet):
    queryset = SalesReturn.objects.select_related("invoice", "customer").prefetch_related(
        Prefetch("lines", ReturnLine.objects.select_related("invoice_line__product"))
    )
    serializer_class = SalesReturnSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    read_scope = "return.view"
    write_scope = "return.create"
    date_field = "return_date"

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("invoice"):
            qs = qs.filter(invoice_id=self.request.query_params["invoice"])
        return qs

    def perform_destroy(self, instance):
        instance.delete()  # drafts only

    @action(detail=True, methods=["post"], url_path="post")
    def post_return(self, request, pk=None):
        return self._detail(post_return(self.get_object(), request.user))


class CustomerAccountView(APIView):
    """What a customer owes, their room left, and the invoices still open —
    oldest first, the order a payment is applied in."""

    permission_classes = [HasReadWriteScope]
    read_scope = "sell"
    write_scope = "sell"

    @extend_schema(responses={200: CustomerAccountSerializer})
    def get(self, request, pk):
        customer = get_object_or_404(Customer, pk=pk)
        today = timezone.localdate()
        owed = customer_balance(customer)
        room = max(customer.credit_limit - owed, ZERO) if customer.allow_credit else ZERO
        return Response(CustomerAccountSerializer({
            "customer": customer.pk,
            "code": customer.code,
            "name": customer.name,
            "credit_status": customer.credit_status,
            "credit_limit": customer.credit_limit,
            "payment_terms_days": customer.payment_terms_days,
            "balance": owed,
            "room_left": room,
            "open_invoices": [
                {
                    "id": inv.pk,
                    "number": inv.number,
                    "sale_date": inv.sale_date,
                    "due_date": inv.due_date,
                    "on_credit": inv.on_credit,
                    "paid": inv.paid,
                    "credited": inv.credited,
                    "balance": inv.balance,
                    "overdue": bool(inv.due_date and inv.due_date < today),
                }
                for inv in open_invoices(customer)
            ],
        }).data)
