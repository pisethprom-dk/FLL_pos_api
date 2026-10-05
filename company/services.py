# v1.0.0 — business rules for the company app.
#
# These live here, not in views, so the same rule holds whether the call comes
# from the API, the Django admin or a management command.
from django.db import transaction
from django.utils import timezone

from company.currency import usd_to_khr
from company.models import DocumentCounter, ExchangeRate
from core.exceptions import DomainError


class NoExchangeRateError(DomainError):
    default_detail = "No exchange rate has been set for that date."
    default_code = "no_exchange_rate"


def rate_on(on_date=None):
    """The rate that applied on a given date.

    The greatest effective_date on or before it — not the newest row, and not
    today's rate. This is what keeps an old invoice reprinting as issued.
    """
    on_date = on_date or timezone.localdate()
    rate = (
        ExchangeRate.objects.filter(effective_date__lte=on_date)
        .order_by("-effective_date")
        .first()
    )
    if rate is None:
        raise NoExchangeRateError()
    return rate


def current_rate():
    return rate_on()


def convert_to_khr(usd_amount, on_date=None):
    """Convenience for display. A saved document must use its own stamped rate
    rather than calling this again later.
    """
    return usd_to_khr(usd_amount, rate_on(on_date).rate)


def rate_is_in_use(rate):
    """True once any document has been stamped with this rate.

    The sales app is not built yet, so this returns False until Invoice exists.
    Wired here so the rule has one home when it does.
    """
    try:
        from sales.models import Invoice  # noqa: F401
    except ImportError:
        return False

    nxt = (
        ExchangeRate.objects.filter(effective_date__gt=rate.effective_date)
        .order_by("effective_date")
        .first()
    )
    qs = Invoice.objects.filter(sale_date__date__gte=rate.effective_date)
    if nxt:
        qs = qs.filter(sale_date__date__lt=nxt.effective_date)
    return qs.exists()


@transaction.atomic
def next_document_number(doc_type):
    """Take the next number for a document type and advance the counter.

    select_for_update is what stops two sales grabbing the same invoice number.
    Call this inside the same transaction that saves the document.
    """
    counter = DocumentCounter.objects.select_for_update().get(doc_type=doc_type)
    number = counter.peek()
    counter.next_number += 1
    counter.save(update_fields=["next_number"])
    return number
