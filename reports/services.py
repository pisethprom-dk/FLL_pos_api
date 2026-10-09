# v1.3.0 — the reports: read-only figures worked out from the documents every
# time they are asked for. Nothing here is stored.
from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import models
from django.db.models import Max, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from catalogue.models import Product
from company.currency import usd_to_khr
from company.services import NoExchangeRateError, rate_on
from inventory.models import DocStatus, StockCount
from sales.models import (
    CustomerPayment,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    PaymentStatus,
    PaymentTender,
    Quotation,
    QuoteStatus,
    RefundMethod,
    ReturnStatus,
    SalesReturn,
)
from sales.money import CASH, CREDIT, KHQR, TENDER_KINDS
from sales.services import invoices_with_balance
from users.scopes import has_scope
from warranty.models import OPEN_STATUSES, WarrantyClaim

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
TENTH = Decimal("0.1")


def _share(part, whole):
    """A percentage to one place, or None when there is nothing to divide."""
    if not whole:
        return None
    return (part * 100 / whole).quantize(TENTH, rounding=ROUND_HALF_UP)


def _average(total, count):
    return (total / count).quantize(CENT, rounding=ROUND_HALF_UP) if count else ZERO


def daily_sales(date_from, date_to, seller_id=None, with_cost=False):
    """Completed sales between two days (by the day they were sold, in the
    shop's time zone), by day, by seller and by tender, with the returns
    posted in the period. Voided and held sales are left out.

    Cash is what a sale came to less KHQR and credit: what stayed in the
    drawer once change was given, riel rounding included — so cash, KHQR and
    credit always add up to the sales. Cost is the cost stamped on each sale
    line when it was sold; without `with_cost` it is None, as is profit.
    """
    invoices = (
        Invoice.objects.filter(
            status=InvoiceStatus.COMPLETED,
            sale_date__date__gte=date_from,
            sale_date__date__lte=date_to,
        )
        .select_related("seller")
        .prefetch_related("lines", "tenders")
        .order_by("sale_date", "id")
    )
    if seller_id is not None:
        invoices = invoices.filter(seller_id=seller_id)

    def bucket():
        return {"invoices": 0, "cash": ZERO, "khqr": ZERO, "credit": ZERO, "sales": ZERO,
                "cost": ZERO, "discount": ZERO, "sales_khr": Decimal("0")}

    days = defaultdict(bucket)
    sellers = defaultdict(bucket)
    names = {}
    whole = bucket()
    for invoice in invoices:
        khqr = sum((t.amount_usd for t in invoice.tenders.all() if t.kind == KHQR), ZERO)
        credit = invoice.on_credit
        cost = sum((l.quantity * (l.unit_cost or ZERO) for l in invoice.lines.all()), ZERO)
        figures = {
            "invoices": 1,
            "cash": invoice.total - khqr - credit,
            "khqr": khqr,
            "credit": credit,
            "sales": invoice.total,
            "cost": cost,
            "discount": invoice.discount_total,
            "sales_khr": usd_to_khr(invoice.total, invoice.exchange_rate) if invoice.exchange_rate else Decimal("0"),
        }
        day = timezone.localdate(invoice.sale_date)
        for target in (days[day], sellers[invoice.seller_id], whole):
            for key, value in figures.items():
                target[key] += value
        names[invoice.seller_id] = invoice.seller.full_name if invoice.seller else None

    def money(value):
        return value.quantize(CENT, rounding=ROUND_HALF_UP)

    def cost_and_profit(b):
        if not with_cost:
            return None, None
        cost = money(b["cost"])
        return cost, b["sales"] - cost

    returns = SalesReturn.objects.filter(
        status=ReturnStatus.POSTED, return_date__gte=date_from, return_date__lte=date_to
    )
    if seller_id is not None:
        returns = returns.filter(invoice__seller_id=seller_id)

    cost, profit = cost_and_profit(whole)
    by_day = []
    for day in sorted(days, reverse=True):
        b = days[day]
        day_cost, day_profit = cost_and_profit(b)
        by_day.append({
            "date": day, "invoices": b["invoices"], "cash": b["cash"], "khqr": b["khqr"],
            "credit": b["credit"], "sales": b["sales"], "cost": day_cost, "profit": day_profit,
        })
    by_seller = [
        {
            "seller": seller, "seller_name": names[seller], "invoices": b["invoices"],
            "sales": b["sales"], "average": _average(b["sales"], b["invoices"]),
            "discount": b["discount"],
        }
        for seller, b in sorted(sellers.items(), key=lambda item: -item[1]["sales"])
    ]
    by_tender = [
        {"tender": kind, "amount": whole[key], "share": _share(whole[key], whole["sales"])}
        for kind, key in ((CASH, "cash"), (KHQR, "khqr"), (CREDIT, "credit"))
    ]
    return {
        "date_from": date_from,
        "date_to": date_to,
        "seller": seller_id,
        "summary": {
            "sales": whole["sales"],
            "sales_khr": whole["sales_khr"],
            "invoices": whole["invoices"],
            "average": _average(whole["sales"], whole["invoices"]),
            "discount": whole["discount"],
            "cost": cost,
            "profit": profit,
            "margin": _share(profit, whole["sales"]) if with_cost else None,
            "returns_total": sum((r.total for r in returns), ZERO),
            "returns_count": returns.count(),
        },
        "by_day": by_day,
        "by_seller": by_seller,
        "by_tender": by_tender,
    }


# --- stock on hand -----------------------------------------------------------------

# Holding stock with nothing in or out for this long (owner's choice, 2026-10-07).
IDLE_DAYS = 90


class StockStatus(models.TextChoices):
    """The first that applies."""

    OUT = "OUT", "Out of stock"
    REORDER = "REORDER", "Reorder"
    IDLE = "IDLE", "No movement"
    OK = "OK", "OK"


STOCK_FILTERS = ("below_reorder", "out_of_stock", "no_movement")


def stock_on_hand(status=None, category=None, brand=None, search=None, with_cost=False):
    """What is on the shelf and what it is worth at average cost, as at today.

    Lists the products that track stock and are active, and any retired one
    still holding stock — as a count does. The summary covers all of them,
    whatever the filters; the rows are those the filters keep. Average cost
    and value are None without `with_cost`.
    """
    today = timezone.localdate()
    idle_since = today - timedelta(days=IDLE_DAYS)
    products = (
        Product.objects.filter(track_stock=True)
        .filter(Q(is_active=True) | ~Q(qty_on_hand=0))
        .select_related("brand", "unit")
        .annotate(last_moved=Max("movements__movement_date"))
        .order_by("code")
    )

    def idle(p):
        return p.qty_on_hand > 0 and (p.last_moved is None or p.last_moved <= idle_since)

    def status_of(p):
        if p.qty_on_hand <= 0:
            return StockStatus.OUT
        if p.qty_on_hand <= p.reorder_level:
            return StockStatus.REORDER
        if idle(p):
            return StockStatus.IDLE
        return StockStatus.OK

    def value_of(p):
        return (p.qty_on_hand * p.avg_cost).quantize(CENT, rounding=ROUND_HALF_UP)

    every = list(products)
    kept = products
    if category:
        kept = kept.filter(Q(category_id=category) | Q(category__parent_id=category))
    if brand:
        kept = kept.filter(brand_id=brand)
    if search:
        kept = kept.filter(
            Q(code__icontains=search) | Q(name__icontains=search) | Q(barcode__icontains=search)
        )
    rows = list(kept)
    if status == "below_reorder":
        rows = [p for p in rows if status_of(p) in (StockStatus.OUT, StockStatus.REORDER)]
    elif status == "out_of_stock":
        rows = [p for p in rows if status_of(p) == StockStatus.OUT]
    elif status == "no_movement":
        rows = [p for p in rows if idle(p)]

    def money(values):
        return sum(values, ZERO) if with_cost else None

    count = (
        StockCount.objects.filter(status=DocStatus.POSTED, reverses=None)
        .select_related("category").order_by("-doc_date", "-id").first()
    )
    last_count = None
    if count is not None:
        differed = count.lines.exclude(difference=0).exclude(difference=None)
        last_count = {
            "number": count.number,
            "date": count.doc_date,
            "category": count.category.name,
            "differences": differed.count(),
            "value": money(line.value or ZERO for line in differed),
        }

    held_idle = [p for p in every if idle(p)]
    return {
        "as_at": today,
        "summary": {
            "products": len(every),
            "value": money(value_of(p) for p in every),
            "below_reorder": sum(1 for p in every if status_of(p) in (StockStatus.OUT, StockStatus.REORDER)),
            "out_of_stock": sum(1 for p in every if status_of(p) == StockStatus.OUT),
            "no_movement": len(held_idle),
            "no_movement_value": money(value_of(p) for p in held_idle),
            "last_count": last_count,
        },
        "rows": [
            {
                "product": p.pk,
                "code": p.code,
                "name": p.name,
                "brand_name": p.brand.name if p.brand else None,
                "unit_name": p.unit.name,
                "shelf_location": p.shelf_location,
                "qty_on_hand": p.qty_on_hand,
                "reorder_level": p.reorder_level,
                "avg_cost": p.avg_cost if with_cost else None,
                "value": value_of(p) if with_cost else None,
                "last_moved": p.last_moved,
                "status": status_of(p),
            }
            for p in rows
        ],
        "rows_value": money(value_of(p) for p in rows),
    }


# --- receivables -----------------------------------------------------------------------

AGES = ("d0_30", "d31_60", "d61_90", "over_90")
RECEIVABLE_FILTERS = ("overdue", "over_limit", "on_hold")


def _age(days):
    """The column a balance sits in: by the days since its invoice."""
    if days <= 30:
        return "d0_30"
    if days <= 60:
        return "d31_60"
    if days <= 90:
        return "d61_90"
    return "over_90"


def receivables(show=None):
    """What store customers owe and how old it is, as at today.

    Each open invoice's balance (on credit, less payments applied and returns
    credited — voided payments ignored) goes into a column by the days since
    it was sold (owner's choice, 2026-10-07). Only customers who owe are
    listed. The summary covers all of them whatever `show` keeps.
    """
    today = timezone.localdate()
    open_ = (
        invoices_with_balance(
            Invoice.objects.filter(status=InvoiceStatus.COMPLETED).select_related("customer")
        )
        .filter(balance__gt=0)
        .order_by("sale_date", "id")
    )
    rows = {}
    for invoice in open_:
        c = invoice.customer
        row = rows.setdefault(c.pk, {
            "customer": c.pk, "code": c.code, "name": c.name,
            **{age: ZERO for age in AGES}, "owed": ZERO,
            "credit_limit": c.credit_limit, "on_hold": c.credit_hold, "overdue": False,
        })
        row[_age((today - timezone.localdate(invoice.sale_date)).days)] += invoice.balance
        row["owed"] += invoice.balance
        if invoice.due_date and invoice.due_date < today:
            row["overdue"] = True
    for row in rows.values():
        row["room_left"] = row["credit_limit"] - row["owed"]
        row["over_limit"] = row["owed"] > row["credit_limit"]
    everyone = sorted(rows.values(), key=lambda r: (-r["owed"], r["name"]))

    kept = everyone
    if show == "overdue":
        kept = [r for r in everyone if r["overdue"]]
    elif show == "over_limit":
        kept = [r for r in everyone if r["over_limit"]]
    elif show == "on_hold":
        kept = [r for r in everyone if r["on_hold"]]

    owed = sum((r["owed"] for r in everyone), ZERO)
    past_60 = sum((r["d61_90"] + r["over_90"] for r in everyone), ZERO)
    try:
        rate = rate_on(today).rate
    except NoExchangeRateError:
        rate = None
    collected = CustomerPayment.objects.filter(
        status=PaymentStatus.POSTED, payment_date__gte=today.replace(day=1), payment_date__lte=today
    )
    flagged = [r["name"] for r in everyone if r["over_limit"] or r["on_hold"]]
    return {
        "as_at": today,
        "summary": {
            "owed": owed,
            "owed_khr": usd_to_khr(owed, rate) if rate else None,
            "rate": rate,
            "past_60": past_60,
            "past_60_share": _share(past_60, owed),
            "collected_this_month": sum((p.amount for p in collected), ZERO),
            "flagged": flagged,
        },
        "rows": kept,
        "totals": {
            **{age: sum((r[age] for r in kept), ZERO) for age in AGES},
            "owed": sum((r["owed"] for r in kept), ZERO),
            "credit_limit": sum((r["credit_limit"] for r in kept), ZERO),
            "room_left": sum((r["room_left"] for r in kept), ZERO),
        },
    }


# --- dashboard -------------------------------------------------------------------------

TOP = 5  # rows in each of the dashboard's short lists
CATEGORIES_SHOWN = 4  # the biggest; the rest go under Other
# A sent quotation that runs out this soon is worth a call (2026-10-07).
EXPIRING_DAYS = 7
MIXED = "MIXED"
PAID_BY = [*TENDER_KINDS, (MIXED, "Mixed")]


def _top(rows):
    """The first few rows, and how many more there are."""
    return rows[:TOP], max(len(rows) - TOP, 0)


def _paid_by(invoice):
    """Cash, KHQR or Credit when the sale was settled one way; Mixed otherwise."""
    kinds = {t.kind for t in invoice.tenders.all()}
    return kinds.pop() if len(kinds) == 1 else MIXED


def _week(today, seller_id):
    """The last seven days' sales, oldest first, a day without sales as zero;
    each with its share of the week's best day, for the bar's height."""
    week = daily_sales(today - timedelta(days=6), today, seller_id)
    sales = {d["date"]: d["sales"] for d in week["by_day"]}
    best = max(sales.values(), default=ZERO)
    days = []
    for back in range(6, -1, -1):
        day = today - timedelta(days=back)
        amount = sales.get(day, ZERO)
        days.append({"date": day, "sales": amount, "share": _share(amount, best) if best else Decimal("0.0")})
    return days


def _by_category(today, seller_id):
    """Today's sales by main category — a sub-category counts in its group —
    the biggest first and the rest as Other (category None)."""
    lines = InvoiceLine.objects.filter(
        invoice__status=InvoiceStatus.COMPLETED, invoice__sale_date__date=today
    ).select_related("product__category__parent")
    if seller_id is not None:
        lines = lines.filter(invoice__seller_id=seller_id)
    groups = {}
    for line in lines:
        category = line.product.category
        group = category.parent or category
        row = groups.setdefault(group.pk, {"category": group.pk, "name": group.name, "sales": ZERO})
        row["sales"] += line.line_total
    ranked = sorted(groups.values(), key=lambda r: (-r["sales"], r["name"]))
    shown, rest = ranked[:CATEGORIES_SHOWN], ranked[CATEGORIES_SHOWN:]
    if rest:
        shown.append({"category": None, "name": "Other", "sales": sum((r["sales"] for r in rest), ZERO)})
    day = sum((r["sales"] for r in ranked), ZERO)
    return [{**r, "share": _share(r["sales"], day)} for r in shown]


def _recent(seller_id):
    """The last few completed sales, newest first."""
    invoices = (
        Invoice.objects.filter(status=InvoiceStatus.COMPLETED)
        .select_related("customer").prefetch_related("tenders")
        .order_by("-sale_date", "-id")
    )
    if seller_id is not None:
        invoices = invoices.filter(seller_id=seller_id)
    return [
        {
            "id": i.pk, "number": i.number, "sale_date": i.sale_date,
            "customer_name": i.customer.name, "walk_in_name": i.walk_in_name,
            "paid_by": _paid_by(i), "total": i.total,
        }
        for i in invoices[:TOP]
    ]


def _cash_today(today, cash_sales):
    """Cash taken today: from sales once change was given, plus cash payments
    posted, less cash refunds. Not a drawer count — there is no opening
    float, and a refund does not record whether it went out in $ or ៛."""
    payments = CustomerPayment.objects.filter(
        status=PaymentStatus.POSTED, tender=PaymentTender.CASH, payment_date=today
    )
    refunds = SalesReturn.objects.filter(
        status=ReturnStatus.POSTED, refund_method=RefundMethod.CASH, return_date=today
    )
    paid_in = sum((p.amount for p in payments), ZERO)
    paid_out = sum((r.refunded for r in refunds), ZERO)
    return {"sales": cash_sales, "payments": paid_in, "refunds": paid_out,
            "total": cash_sales + paid_in - paid_out}


def dashboard(user):
    """Today at a glance, each part only for whoever may see it — None
    otherwise. Sales are the whole shop's for an Admin and a Seller's own
    for a Seller, as in Daily sales."""
    today = timezone.localdate()
    with_cost = has_scope(user, "cost.view")
    every_sale = has_scope(user, "report.sales.all")

    sales = cash = None
    if every_sale or has_scope(user, "report.sales.own"):
        seller_id = None if every_sale else user.pk
        day = daily_sales(today, today, seller_id, with_cost)
        summary = day["summary"]
        sales = {
            "seller": seller_id,
            "today": {k: summary[k] for k in ("sales", "sales_khr", "invoices", "average", "profit", "margin")},
            "last_7_days": _week(today, seller_id),
            "by_category": _by_category(today, seller_id),
            "recent": _recent(seller_id),
        }
        if every_sale:
            cash_sales = next(t["amount"] for t in day["by_tender"] if t["tender"] == CASH)
            cash = _cash_today(today, cash_sales)

    stock = None
    if has_scope(user, "report.stock"):
        report = stock_on_hand(status="below_reorder", with_cost=with_cost)
        low = sorted(report["rows"], key=lambda r: (r["status"] != StockStatus.OUT, r["code"]))
        running_low, more = _top(low)
        summary = report["summary"]
        stock = {
            "products": summary["products"], "value": summary["value"],
            "below_reorder": summary["below_reorder"], "out_of_stock": summary["out_of_stock"],
            "running_low": running_low, "more": more,
        }

    owed = None
    if has_scope(user, "report.receivables"):
        report = receivables()
        rows, more = _top(report["rows"])
        owed = {"owed": report["summary"]["owed"], "over_90": report["totals"]["over_90"],
                "rows": rows, "totals": report["totals"], "more": more}

    quotations = None
    if has_scope(user, "quotation.view"):
        sent = Quotation.objects.filter(status=QuoteStatus.SENT)
        quotations = {
            "sent": sent.count(),
            "expiring": sent.filter(
                valid_until__gte=today, valid_until__lte=today + timedelta(days=EXPIRING_DAYS)
            ).count(),
            "expired": sent.filter(valid_until__lt=today).count(),
        }

    warranty = None
    if has_scope(user, "warranty.view"):
        open_ = WarrantyClaim.objects.filter(status__in=OPEN_STATUSES)
        # Claimed after its end date, by the day it was logged — as the claims list reads it.
        warranty = {"open": open_.count(),
                    "out_of_warranty": open_.filter(expiry_date__lt=TruncDate("created_at")).count()}

    return {
        "as_at": today,
        "sales": sales,
        "cash_today": cash,
        "stock": stock,
        "owed": owed,
        "held_sales": (
            Invoice.objects.filter(status=InvoiceStatus.HELD).count() if has_scope(user, "sell") else None
        ),
        "quotations": quotations,
        "warranty": warranty,
    }
