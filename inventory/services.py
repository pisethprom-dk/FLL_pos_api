# v1.1.0 — posting stock documents, and the ledger primitives sales will use.
#
# Every change to qty_on_hand and avg_cost goes through _move(), inside the
# caller's transaction, on a product row locked with select_for_update. Two
# postings that touch the same product therefore queue rather than both reading
# the same quantity.
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from catalogue.models import Product
from core.exceptions import DomainError, InsufficientStockError
from inventory.models import (
    FOUR,
    SUPPLIER_REASONS,
    TWO,
    Adjustment,
    AdjustmentReason,
    DocStatus,
    StockCount,
    StockCountLine,
    StockIn,
    StockMovement,
)
from partners.models import ProductSupplier


@dataclass(frozen=True)
class Ref:
    """Which document a movement belongs to."""

    doc_type: str
    doc_id: int
    doc_number: str
    on: date
    user: object = None
    reason: str = ""
    is_reversal: bool = False


def ref_for(doc, user):
    return Ref(
        doc_type=doc.DOC_TYPE,
        doc_id=doc.pk,
        doc_number=doc.number,
        on=doc.doc_date,
        user=user,
        reason=getattr(doc, "reason", ""),
        is_reversal=doc.reverses_id is not None,
    )


# --- the ledger ------------------------------------------------------------

def next_position(qty, avg, change, value):
    """Where a product stands after one movement.

    The only place the moving average is worked out. Posting and
    recompute_stock both call it, so the ledger replays to exactly the figures
    that were written. An outbound movement at the average leaves the average
    where it was; one that removes an earlier inbound takes its value back out.
    """
    new_qty = qty + change
    if new_qty < 0:
        raise InsufficientStockError()
    if new_qty == 0:
        # Nothing left to average. Keep the last cost so the next adjustment
        # or count still has one to read.
        return new_qty, avg
    new_avg = ((qty * avg + value) / new_qty).quantize(FOUR, ROUND_HALF_UP)
    if new_avg < 0:
        raise DomainError(
            "That would leave a negative stock value. Correct it with an "
            "adjustment instead."
        )
    return new_qty, new_avg


def lock_products(ids):
    """Lock product rows in id order, so two postings never deadlock."""
    return {
        p.pk: p
        for p in Product.objects.select_for_update().filter(pk__in=set(ids)).order_by("pk")
    }


def _move(product, change, value, ref, unit_cost, line_id=None):
    if product.qty_on_hand + change < 0:
        raise InsufficientStockError(
            f"Not enough {product.code} {product.name}: "
            f"{product.qty_on_hand} on hand, {-change} needed."
        )
    qty, avg = next_position(product.qty_on_hand, product.avg_cost, change, value)
    Product.objects.filter(pk=product.pk).update(
        qty_on_hand=qty, avg_cost=avg, updated_at=timezone.now()
    )
    product.qty_on_hand, product.avg_cost = qty, avg
    return StockMovement.objects.create(
        product=product,
        doc_type=ref.doc_type,
        doc_id=ref.doc_id,
        doc_number=ref.doc_number,
        line_id=line_id,
        reason=ref.reason,
        is_reversal=ref.is_reversal,
        movement_date=ref.on,
        quantity=change,
        unit_cost=unit_cost.quantize(FOUR, ROUND_HALF_UP),
        value=value,
        qty_after=qty,
        avg_cost_after=avg,
        posted_by=ref.user,
    )


def receive(product, qty, value, ref, line_id=None):
    """Stock in. `value` is the money it cost; the average moves to take it in.

    `product` must be locked by the caller (lock_products).
    """
    return _move(product, qty, value, ref, value / qty, line_id)


def issue(product, qty, ref, line_id=None):
    """Stock out at the current average, which is stamped on the movement.
    Refuses to go below zero.

    `product` must be locked by the caller (lock_products).
    """
    cost = product.avg_cost
    return _move(product, -qty, -(qty * cost), ref, cost, line_id)


# --- shared document checks -------------------------------------------------

def _lock_draft(model, doc):
    doc = model.objects.select_for_update().get(pk=doc.pk)
    if doc.status != DocStatus.DRAFT:
        raise DomainError(f"{doc.number} is already posted.")
    return doc


def _check_date(doc):
    if doc.doc_date > timezone.localdate():
        raise DomainError("The document date cannot be in the future.")


def _check_tracked(product):
    if not product.track_stock:
        raise DomainError(f"{product.code} does not track stock, so it has none to move.")


def _mark_posted(doc, user):
    doc.status = DocStatus.POSTED
    doc.posted_at = timezone.now()
    doc.posted_by = user
    doc.updated_by = user
    doc.save()


def default_pack(product, supplier):
    """The supplier's usual pack for this product as (unit, size): their
    link's, or the product's own unit one at a time."""
    link = (
        ProductSupplier.objects.select_related("pack_unit")
        .filter(product=product, supplier=supplier).first()
    )
    return (link.pack_unit, link.pack_size) if link else (None, Decimal("1.00"))


# --- stock in ----------------------------------------------------------------

@transaction.atomic
def post_stock_in(doc, user):
    doc = _lock_draft(StockIn, doc)
    _check_date(doc)
    if not doc.supplier.is_active:
        raise DomainError(f"{doc.supplier.name} is inactive and cannot be bought from.")
    lines = list(doc.lines.all())
    if not lines:
        raise DomainError("Add at least one line before posting.")

    products = lock_products(line.product_id for line in lines)
    ref = ref_for(doc, user)
    for line in lines:
        product = products[line.product_id]
        _check_tracked(product)
        if not product.is_active:
            raise DomainError(f"{product.code} is inactive and cannot be bought.")
        # The average takes in the money paid, not the rounded unit cost.
        receive(product, line.quantity, line.line_total, ref, line.pk)

    _mark_posted(doc, user)
    return doc


# --- adjustment ----------------------------------------------------------------

def opening_balance_allowed(product):
    """True while the ledger has never moved this product — or has only moved
    it by opening balances that were reversed to nothing."""
    moves = StockMovement.objects.filter(product=product)
    if moves.exclude(reason=AdjustmentReason.OPENING_BALANCE).exists():
        return False
    return (moves.aggregate(total=Sum("quantity"))["total"] or 0) == 0


def check_adjustment_header(reason, supplier):
    if reason in SUPPLIER_REASONS and supplier is None:
        raise DomainError({"supplier": "Choose the supplier for a return or replacement."})
    if reason not in SUPPLIER_REASONS and supplier is not None:
        raise DomainError(
            {"supplier": "A supplier is recorded only on a return to, or a "
                         "replacement from, a supplier."}
        )


@transaction.atomic
def post_adjustment(doc, user):
    doc = _lock_draft(Adjustment, doc)
    _check_date(doc)
    check_adjustment_header(doc.reason, doc.supplier)
    lines = list(doc.lines.all())
    if not lines:
        raise DomainError("Add at least one line before posting.")

    opening = doc.reason == AdjustmentReason.OPENING_BALANCE
    if opening:
        product_ids = [line.product_id for line in lines]
        if len(product_ids) != len(set(product_ids)):
            raise DomainError("An opening balance lists each product once.")

    products = lock_products(line.product_id for line in lines)
    ref = ref_for(doc, user)
    for line in lines:
        product = products[line.product_id]
        _check_tracked(product)

        if opening:
            if not line.unit_cost or line.unit_cost <= 0:
                raise DomainError(f"{product.code}: an opening balance needs a unit cost.")
            if not opening_balance_allowed(product):
                raise DomainError(
                    f"{product.code} already has stock movements. An opening "
                    "balance is only for a product the ledger has never moved."
                )
            movement = receive(product, line.quantity, line.quantity * line.unit_cost, ref, line.pk)
        else:
            if line.unit_cost is not None:
                raise DomainError(
                    f"{product.code}: unit cost is the current average and "
                    "cannot be typed. Only an opening balance sets its own."
                )
            if doc.is_inbound:
                movement = receive(
                    product, line.quantity, line.quantity * product.avg_cost, ref, line.pk
                )
            else:
                movement = issue(product, line.quantity, ref, line.pk)

        line.unit_cost = movement.unit_cost
        line.value = movement.value.quantize(TWO, ROUND_HALF_UP)
        line.save()

    _mark_posted(doc, user)
    return doc


# --- stock count ---------------------------------------------------------------

@transaction.atomic
def start_count(category, user, counted_by=None, doc_date=None, note=""):
    products = list(
        Product.objects.filter(track_stock=True)
        .filter(Q(category=category) | Q(category__parent=category))
        # A retired product still on the shelf still gets counted.
        .filter(Q(is_active=True) | ~Q(qty_on_hand=0))
    )
    if not products:
        raise DomainError("There are no stock-tracked products in this category.")

    clash = sorted(set(
        StockCountLine.objects.filter(
            document__status=DocStatus.DRAFT, product__in=products
        ).values_list("document__number", flat=True)
    ))
    if clash:
        raise DomainError(
            "Some of these products are already on an open count: "
            + ", ".join(clash) + ". Post or abandon it first."
        )

    count = StockCount(
        category=category,
        counted_by=counted_by or user,
        note=note,
        created_by=user,
    )
    if doc_date:
        count.doc_date = doc_date
    _check_date(count)
    count.save()
    StockCountLine.objects.bulk_create(
        StockCountLine(document=count, product=p) for p in products
    )
    return count


@transaction.atomic
def record_counts(count, entries):
    """entries: [{"line": id, "counted_qty": Decimal or None}].

    The expected quantity is read the moment a count is entered, so a sale
    made after this line was counted is not mistaken for a shortage.
    """
    count = _lock_draft(StockCount, count)
    lines = {line.pk: line for line in count.lines.all()}
    now = timezone.now()
    for entry in entries:
        line = lines.get(entry["line"])
        if line is None:
            raise DomainError(f"Line {entry['line']} is not on {count.number}.")
        qty = entry["counted_qty"]
        if qty is None:
            line.counted_qty = line.expected_qty = line.counted_at = None
        else:
            if qty < 0:
                raise DomainError("A counted quantity cannot be negative.")
            line.counted_qty = qty
            line.expected_qty = (
                Product.objects.values_list("qty_on_hand", flat=True).get(pk=line.product_id)
            )
            line.counted_at = now
        line.save()
    return count


@transaction.atomic
def post_count(count, user):
    count = _lock_draft(StockCount, count)
    _check_date(count)
    lines = [line for line in count.lines.all() if line.counted_qty is not None]
    if not lines:
        raise DomainError("Nothing has been counted yet.")

    products = lock_products(line.product_id for line in lines)
    ref = ref_for(count, user)
    for line in lines:
        product = products[line.product_id]
        _check_tracked(product)
        difference = line.counted_qty - line.expected_qty
        cost = product.avg_cost
        if difference > 0:
            receive(product, difference, difference * cost, ref, line.pk)
        elif difference < 0:
            try:
                issue(product, -difference, ref, line.pk)
            except InsufficientStockError:
                raise InsufficientStockError(
                    f"{product.code} has sold below what this count allows for "
                    "since it was counted. Count that line again."
                )
        line.difference = difference
        line.unit_cost = cost
        line.value = (difference * cost).quantize(TWO, ROUND_HALF_UP)
        line.save()

    _mark_posted(count, user)
    return count


# --- reversal --------------------------------------------------------------------

def reverse_movements(doc_type, doc_id, ref, label):
    """Replay a document's movements with their signs turned, newest first.

    Stock that came in goes back out taking its own value with it; stock that
    went out comes back at the cost it was stamped with. Used by a reversing
    stock document and by an invoice void. `label` names the original
    document in the refusal.
    """
    movements = list(
        StockMovement.objects.filter(doc_type=doc_type, doc_id=doc_id, is_reversal=False)
        .order_by("-id")
    )
    products = lock_products(m.product_id for m in movements)
    for m in movements:
        product = products[m.product_id]
        try:
            _move(product, -m.quantity, -m.value, ref, m.unit_cost)
        except InsufficientStockError:
            raise InsufficientStockError(
                f"{product.code} has already gone out of stock since "
                f"{label} was posted, so it cannot be taken back. "
                "Correct it with an adjustment instead."
            )


@transaction.atomic
def reverse_document(doc, user, note):
    """Post a document of the same type that undoes this one.

    Each movement is replayed with its sign turned: stock that came in goes
    back out taking its own value with it, and stock that went out comes back
    at the cost it was stamped with.
    """
    model = type(doc)
    doc = model.objects.select_for_update().get(pk=doc.pk)
    if doc.status != DocStatus.POSTED:
        raise DomainError("Only a posted document can be reversed. A draft can be deleted.")
    if doc.reverses_id:
        raise DomainError("A reversal cannot itself be reversed. Post a new document instead.")
    if model.objects.filter(reverses=doc).exists():
        raise DomainError(f"{doc.number} has already been reversed.")
    if not (note or "").strip():
        raise DomainError({"note": "Say why the document is being reversed."})

    reversal = model(
        reverses=doc,
        note=note.strip(),
        created_by=user,
        **{field: getattr(doc, field) for field in model.REVERSAL_FIELDS},
    )
    reversal.save()
    line_model = doc.lines.model
    for line in doc.lines.all():
        line_model(
            document=reversal,
            **{field: getattr(line, field) for field in model.LINE_FIELDS},
        ).save()

    reverse_movements(doc.DOC_TYPE, doc.pk, ref_for(reversal, user), doc.number)
    _mark_posted(reversal, user)
    return reversal
