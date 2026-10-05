# v1.0.1
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from company.currency import BASE_CURRENCY, DECIMALS, NOTES, ROUNDING_STEP, SYMBOL
from company.models import CompanyProfile, DocumentCounter, ExchangeRate, PaymentNote
from company.serializers import (
    CompanyProfileSerializer,
    CurrentRateSerializer,
    DocumentCounterSerializer,
    ExchangeRateSerializer,
    PaymentNoteSerializer,
)
from company.services import current_rate
from core.schema import ACTIVE
from users.permissions import IsAdmin, IsAdminOrReadOnly


class CompanyProfileView(APIView):
    """Singleton. Everyone reads it — receipts need it. Only an Admin writes."""

    permission_classes = [IsAdminOrReadOnly]

    @extend_schema(responses={200: CompanyProfileSerializer})
    def get(self, request):
        return Response(CompanyProfileSerializer(CompanyProfile.get()).data)

    @extend_schema(request=CompanyProfileSerializer, responses={200: CompanyProfileSerializer})
    def patch(self, request):
        serializer = CompanyProfileSerializer(
            CompanyProfile.get(), data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(serializer.data)


class ExchangeRateViewSet(viewsets.ModelViewSet):
    queryset = ExchangeRate.objects.select_related("created_by").all()
    serializer_class = ExchangeRateSerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class CurrentRateView(APIView):
    """What the till needs: today's rate and how riel behaves."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: CurrentRateSerializer})
    def get(self, request):
        rate = current_rate()
        return Response(CurrentRateSerializer({
            "base_currency": BASE_CURRENCY,
            "effective_date": rate.effective_date,
            "rate": rate.rate,
            "decimals": DECIMALS,
            "rounding_step": {k: str(v) for k, v in ROUNDING_STEP.items()},
            "symbol": SYMBOL,
            "notes": {k: [str(n) for n in v] for k, v in NOTES.items()},
        }).data)


class DocumentCounterViewSet(viewsets.ModelViewSet):
    """Prefixes for the Rules and numbering screen. next_number is read-only —
    it is the system's, not the Admin's."""

    queryset = DocumentCounter.objects.all()
    serializer_class = DocumentCounterSerializer
    permission_classes = [IsAdmin]
    http_method_names = ["get", "patch", "head", "options"]


@extend_schema_view(list=extend_schema(parameters=[ACTIVE]))
class PaymentNoteViewSet(viewsets.ModelViewSet):
    queryset = PaymentNote.objects.all()
    serializer_class = PaymentNoteSerializer
    permission_classes = [IsAdminOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("active") == "true":
            qs = qs.filter(is_active=True)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)
