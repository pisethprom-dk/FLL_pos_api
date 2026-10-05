# v1.0.0 — domain errors raised by the service layer
from rest_framework.exceptions import APIException


class DomainError(APIException):
    """A business rule was broken.

    Services raise these so the same rule holds whether the call arrives from
    the API, the Django admin or a management command.
    """

    status_code = 400
    default_detail = "That is not allowed."
    default_code = "domain_error"


class PostedDocumentError(DomainError):
    default_detail = "A posted document cannot be changed. Raise a reversing document instead."
    default_code = "document_posted"


class InsufficientStockError(DomainError):
    default_detail = "Not enough stock. Selling below zero is not allowed."
    default_code = "insufficient_stock"


class DiscountLimitError(DomainError):
    default_detail = "That discount is over the limit."
    default_code = "discount_over_limit"


class CreditLimitError(DomainError):
    default_detail = "That would take the customer past their credit limit."
    default_code = "credit_limit"
