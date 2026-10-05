# v1.0.0 — currency rules.
#
# These live in code, not in a table. The shop prices in US dollars and takes
# riel across the counter; that will not change, and the Currency screen was
# dropped from the design. Only the rate itself is data, because it moves.
from decimal import ROUND_HALF_UP, Decimal

USD = "USD"
KHR = "KHR"

BASE_CURRENCY = USD

#: decimal places used when formatting and storing
DECIMALS = {USD: 2, KHR: 0}

#: cash in this currency can only be given in multiples of this
ROUNDING_STEP = {USD: Decimal("0.01"), KHR: Decimal("100")}

SYMBOL = {USD: "$", KHR: "៛"}

#: notes in circulation, largest first — used by a drawer count
NOTES = {
    USD: [Decimal(n) for n in (100, 50, 20, 10, 5, 1)],
    KHR: [Decimal(n) for n in (100000, 50000, 20000, 10000, 5000, 1000, 500, 100)],
}

CENT = Decimal("0.01")


def quantize_usd(amount):
    return Decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)


def round_khr(amount):
    """Snap a riel amount to the nearest 100.

    ៛2,255 becomes ៛2,300 — nobody has a ៛50 note.
    """
    step = ROUNDING_STEP[KHR]
    return (Decimal(amount) / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step


def usd_to_khr(usd_amount, rate):
    """Convert and round in one step. `rate` is riel per one US dollar."""
    return round_khr(Decimal(usd_amount) * Decimal(rate))


def khr_to_usd(khr_amount, rate):
    return quantize_usd(Decimal(khr_amount) / Decimal(rate))


def split_change(usd_change, rate):
    """Work out how change is actually handed over.

    Whole dollars come out of the dollar drawer; the remainder is paid in riel,
    rounded to the nearest 100. $38.55 at ៛4,100 becomes $38.00 plus ៛2,300.
    """
    usd_change = quantize_usd(usd_change)
    whole = usd_change.quantize(Decimal("1"), rounding="ROUND_DOWN")
    remainder = usd_change - whole
    return whole, usd_to_khr(remainder, rate)
