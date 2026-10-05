# v1.0.0 — helpers for the OpenAPI schema the Angular client is generated from.
#
# Anything the schema cannot see — a filter read from query_params, a body
# built by hand — becomes an `any` in the generated client. These keep the
# annotations short enough to put on every view that needs one.
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from rest_framework import serializers


class DetailSerializer(serializers.Serializer):
    """The {"detail": "..."} body of a message or a refusal."""

    detail = serializers.CharField()


def query(name, kind=str, description="", enum=None):
    return OpenApiParameter(
        name=name, type=kind, location=OpenApiParameter.QUERY,
        required=False, description=description, enum=enum,
    )


def flag(name, description=""):
    """A filter switched on by ?name=true."""
    return query(name, str, description, enum=["true", "false"])


ACTIVE = flag("active", "true: active only; false: inactive only.")
SEARCH = query("search", str, "Matches codes, names and numbers.")
DATE_FROM = query("date_from", OpenApiTypes.DATE, "On or after this date.")
DATE_TO = query("date_to", OpenApiTypes.DATE, "On or before this date.")
CUSTOMER = query("customer", int, "Customer id.")
SUPPLIER = query("supplier", int, "Supplier id.")
PRODUCT = query("product", int, "Product id.")


def status_filter(*values):
    return query("status", str, "Document status.", enum=list(values))


def money(**kw):
    """A dollar amount as the API sends it: a decimal string, two places."""
    return serializers.DecimalField(max_digits=14, decimal_places=2, **kw)
