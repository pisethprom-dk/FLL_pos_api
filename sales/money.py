# v1.0.0 — the money rules of a sale, as plain functions.
#
# Discount is per line only — there is no invoice-level or quote-level
# discount (owner's decision). The discounted unit price is the line's price,
# and the total is the sum of the lines.
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from company.currency import KHR, USD, khr_to_usd, round_khr, split_change
from core.exceptions import DiscountLimitError, DomainError

TWO = Decimal("0.01")
ZERO = Decimal("0.00")

# Absolute: no override for anyone, Admin included. Lives in code, not settings.
DISCOUNT_CAP_PERCENT = Decimal("15")

PERCENT = "PERCENT"
AMOUNT = "AMOUNT"

CASH = "CASH"
KHQR = "KHQR"
CREDIT = "CREDIT"


def net_price(price, kind, value, price_fixed=False):
    """The unit price after a line discount.

    `kind` is PERCENT (value is 0–15) or AMOUNT (value is dollars off each
    unit). Over 15% is refused, and a fixed-price product takes no discount.
    """
    value = Decimal(value or 0)
    if value == 0:
        return price
    if value < 0:
        raise DomainError("A discount cannot be negative.")
    if price_fixed:
        raise DiscountLimitError("This product's price is fixed and cannot be discounted.")
    if kind == PERCENT:
        percent = value
        off = price * value / 100
    elif kind == AMOUNT:
        if price <= 0:
            raise DiscountLimitError("A free line cannot be discounted.")
        percent = value / price * 100
        off = value
    else:
        raise DomainError("Choose % or $ for the discount.")
    if percent > DISCOUNT_CAP_PERCENT:
        raise DiscountLimitError(
            f"Over the {DISCOUNT_CAP_PERCENT}% limit — refused. The limit is absolute."
        )
    return (price - off).quantize(TWO, ROUND_HALF_UP)


@dataclass
class Tender:
    kind: str  # CASH, KHQR or CREDIT
    currency: str  # USD or KHR
    amount: Decimal  # in its own currency
    reference: str = ""
    amount_usd: Decimal = field(default=ZERO)


@dataclass
class Settlement:
    total: Decimal
    paid_now: Decimal
    on_credit: Decimal
    change_due: Decimal
    change_usd: Decimal
    change_khr: Decimal
    # What the shop kept (+) or gave away (−) to riel rounding, so cash reconciles.
    rounding: Decimal


def settle(total, tenders, rate):
    """Check the tenders cover the total, and work out change.

    Riel converts at the invoice's stamped rate. Change comes only out of cash;
    KHQR and credit can never exceed what is owed. A shortfall smaller than
    half a ៛100 note is not a shortfall — nobody can hand over less — and is
    recorded as rounding.
    """
    for t in tenders:
        if t.amount <= 0:
            raise DomainError("Each payment must be more than zero.")
        if t.kind not in (CASH, KHQR, CREDIT):
            raise DomainError(f"Unknown payment type {t.kind}.")
        if t.currency == KHR:
            if t.kind == CREDIT:
                raise DomainError("Credit is recorded in US dollars.")
            if t.amount != t.amount.to_integral_value():
                raise DomainError("Riel has no decimals.")
            t.amount_usd = khr_to_usd(t.amount, rate)
        elif t.currency == USD:
            t.amount_usd = t.amount.quantize(TWO, ROUND_HALF_UP)
        else:
            raise DomainError(f"Unknown currency {t.currency}.")

    by_kind = {k: sum((t.amount_usd for t in tenders if t.kind == k), ZERO) for k in (CASH, KHQR, CREDIT)}
    if by_kind[KHQR] + by_kind[CREDIT] > total:
        raise DomainError(
            "KHQR and credit cannot be more than the total — change is given from cash only."
        )

    received = sum(by_kind.values(), ZERO)
    rounding = ZERO
    shortfall = total - received
    if shortfall > 0:
        paid_in_riel = any(t.currency == KHR and t.kind == CASH for t in tenders)
        if paid_in_riel and round_khr(shortfall * rate) == 0:
            rounding = -shortfall
        else:
            raise DomainError(f"${shortfall} is still to be covered.")

    change_due = max(received - total, ZERO)
    change_usd, change_khr = (split_change(change_due, rate) if change_due else (ZERO, Decimal("0")))
    if change_due:
        handed_over = change_usd + khr_to_usd(change_khr, rate)
        rounding += change_due - handed_over

    return Settlement(
        total=total,
        paid_now=total - by_kind[CREDIT],
        on_credit=by_kind[CREDIT],
        change_due=change_due,
        change_usd=change_usd,
        change_khr=change_khr,
        rounding=rounding,
    )
