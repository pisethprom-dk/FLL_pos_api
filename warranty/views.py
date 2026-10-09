# v1.0.0 — /api/warranty/claims/. Both roles log and edit claims; only an
# Admin deletes one (warranty.delete, owner's choice 2026-10-06).
from django.db.models import Q
from django.db.models.functions import TruncDate
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets

from core.schema import flag, query, status_filter
from core.views import AuditMixin
from users.permissions import HasReadWriteScope
from warranty.models import OPEN_STATUSES, ClaimStatus, WarrantyClaim
from warranty.serializers import WarrantyClaimSerializer

SEARCHED = ("warranty_number", "product_code", "product_name", "customer_name", "customer_phone")

CLAIM_FILTERS = [
    status_filter(*ClaimStatus.values),
    flag("open", "true: received, sent for repair or ready for collection."),
    flag("out_of_warranty", "true: claimed after the warranty's end date."),
    query("search", str, "Matches the warranty number, the product's code and name, "
                         "and the customer's name and phone."),
]


@extend_schema_view(list=extend_schema(parameters=CLAIM_FILTERS))
class WarrantyClaimViewSet(AuditMixin, viewsets.ModelViewSet):
    """Newest first. Any status may follow any other."""

    queryset = WarrantyClaim.objects.select_related("created_by", "updated_by")
    serializer_class = WarrantyClaimSerializer
    permission_classes = [HasReadWriteScope]
    read_scope = "warranty.view"
    write_scope = "warranty.edit"
    delete_scope = "warranty.delete"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get("status"):
            qs = qs.filter(status=p["status"].upper())
        if p.get("open") == "true":
            qs = qs.filter(status__in=OPEN_STATUSES)
        elif p.get("open") == "false":
            qs = qs.exclude(status__in=OPEN_STATUSES)
        # The day it was logged, in the shop's time zone, as the model reads it.
        expired = Q(expiry_date__lt=TruncDate("created_at"))
        if p.get("out_of_warranty") == "true":
            qs = qs.filter(expired)
        elif p.get("out_of_warranty") == "false":
            qs = qs.exclude(expired)
        if p.get("search"):
            term = p["search"].strip()
            q = Q()
            for field in SEARCHED:
                q |= Q(**{f"{field}__icontains": term})
            qs = qs.filter(q)
        return qs
