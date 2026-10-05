# v1.0.0 — the sales rules: quotation states, completing and voiding a sale,
# customer payments, returns, and what a customer owes.
#
# Every function that changes a posted document runs in one transaction and
# locks what it reads: the document, the customer (so two tills cannot both
# spend the same credit room), and product rows through inventory's
# lock_products.
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from company.currency import KHR, khr_to_usd
from company.models import DocumentType
from company.services import rate_on
from core.exceptions import CreditLimitError, DomainError
from inventory.services import Ref, issue, lock_products, receive, reverse_movements
from partners.models import Customer
from sales.models import (
    CustomerPayment,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    InvoiceTender,
    PaymentAllocation,
    PaymentStatus,
    Quotation,
    QuotationLine,
    QuoteStatus,
    ReturnLine,
    ReturnStatus,
    SalesReturn,
)
from sales.money import CREDIT, settle

ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=14, decimal_places=2)


# --- balances ------------------------------------------------------------------

def invoices_with_balance(qs=None):
    """Completed invoices annotated with paid, credited and balance.

    Balance = what went on credit − payments applied (voided payments ignored)
    − returns credited against it. Worked out every time; never stored.
    """
    qs = Invoice.objects.filter(status=InvoiceStatus.COMPLETED) if qs is None else qs
    paid = (
        PaymentAllocation.objects.filter(invoice=OuterRef("pk"), payment__status=PaymentStatus.POSTED)
        .values("invoice").annotate(s=Sum("amount")).values("s")
    )
    credited = (
        SalesReturn.objects.filter(invoice=OuterRef("pk"), status=ReturnStatus.POSTED)
        .values("invoice").annotate(s=Sum("credited")).values("s")
    )
    return qs.annotate(
        paid=Coalesce(Subquery(paid, output_field=MONEY), Value(ZERO), output_field=MONEY),
        credited=Coalesce(Subquery(credited, output_field=MONEY), Value(ZERO), output_field=MONEY),
    ).annotate(balance=F("on_credit") - F("paid") - F("credited"))


def open_invoices(customer):
    """Invoices still owing, oldest due first — the order a payment is applied in."""
    return (
        invoices_with_balance(Invoice.objects.filter(customer=customer, status=InvoiceStatus.COMPLETED))
        .filter(balance__gt=0)
        .order_by("due_date", "sale_date", "id")
    )


def customer_balance(customer):
    total = open_invoices(customer).aggregate(s=Sum("balance"))["s"]
    return total or ZERO


def invoice_balance(invoice):
    return invoices_with_balance(Invoice.objects.filter(pk=invoice.pk)).get().balance


# --- quotation -------------------------------------------------------------------

def _quote_for_update(quote):
    return Quotation.objects.select_for_update().get(pk=quote.pk)


def _set_quote_status(quote, status, **fields):
    Quotation.objects.filter(pk=quote.pk).update(status=status, updated_at=timezone.now(), **fields)
    quote.refresh_from_db()
    return quote


@transaction.atomic
def send_quote(quote):
    quote = _quote_for_update(quote)
    if quote.status != QuoteStatus.DRAFT:
        raise DomainError("Only a draft quotation can be sent.")
    if not quote.lines.exists():
        raise DomainError("Add at least one line first.")
    return _set_quote_status(quote, QuoteStatus.SENT)


@transaction.atomic
def accept_quote(quote):
    quote = _quote_for_update(quote)
    if quote.status not in (QuoteStatus.DRAFT, QuoteStatus.SENT):
        raise DomainError("Only a draft or sent quotation can be accepted.")
    if not quote.lines.exists():
        raise DomainError("Add at least one line first.")
    return _set_quote_status(quote, QuoteStatus.ACCEPTED, accepted_at=timezone.now())


@transaction.atomic
def reject_quote(quote, note):
    """Closes the quote. On a part-invoiced one, this closes what is left."""
    quote = _quote_for_update(quote)
    if quote.status not in (QuoteStatus.DRAFT, QuoteStatus.SENT, QuoteStatus.ACCEPTED):
        raise DomainError("This quotation is already closed.")
    if not (note or "").strip():
        raise DomainError({"note": "Say why the quotation was rejected."})
    return _set_quote_status(quote, QuoteStatus.REJECTED, note=note.strip())


def _refresh_quote_status(quote):
    """Invoiced once every line is fully taken; back to accepted if a void
    puts quantity back."""
    open_lines = quote.lines.filter(qty_invoiced__lt=F("quantity")).exists()
    if not open_lines and quote.status == QuoteStatus.ACCEPTED:
        _set_quote_status(quote, QuoteStatus.INVOICED)
    elif open_lines and quote.status == QuoteStatus.INVOICED:
        _set_quote_status(quote, QuoteStatus.ACCEPTED)


# --- invoice -----------------------------------------------------------------------

def _invoice_ref(invoice, number, on, user, is_reversal=False):
    return Ref(
        doc_type=DocumentType.INVOICE, doc_id=invoice.pk, doc_number=number,
        on=on, user=user, reason="VOID" if is_reversal else "", is_reversal=is_reversal,
    )


@transaction.atomic
def complete_invoice(invoice, tenders, user):
    """Take the number, stamp the rate, move the stock, record how it was paid.

    `tenders` are sales.money.Tender. All of it happens or none of it does.
    """
    from company.services import next_document_number

    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != InvoiceStatus.HELD:
        raise DomainError("This sale is already completed.")
    lines = list(invoice.lines.select_related("product", "quote_line"))
    if not lines:
        raise DomainError("Add at least one item before completing the sale.")

    customer = Customer.objects.select_for_update().get(pk=invoice.customer_id)
    if not customer.is_active:
        raise DomainError(f"{customer.name} is inactive and cannot be sold to.")

    quote = None
    if invoice.quotation_id:
        quote = _quote_for_update(invoice.quotation)
        if quote.status != QuoteStatus.ACCEPTED:
            raise DomainError(f"{quote.number} is not accepted, so it cannot be invoiced.")
        quote_lines = {
            ql.pk: ql for ql in QuotationLine.objects.select_for_update().filter(quotation=quote)
        }
        for line in lines:
            ql = quote_lines.get(line.quote_line_id)
            if ql is None or line.quantity > ql.remaining:
                raise DomainError(
                    f"{line.product.code}: only {ql.remaining if ql else 0} left to invoice on {quote.number}."
                )

    # Who may have credit at all is told before whether the money adds up.
    if any(t.kind == CREDIT for t in tenders):
        if not customer.allow_credit or customer.is_system:
            raise CreditLimitError(f"{customer.name} does not buy on credit. The sale must be paid now.")
        if customer.credit_hold:
            raise CreditLimitError(f"{customer.name} is on credit hold. The sale must be paid now.")

    today = timezone.localdate()
    rate = rate_on(today).rate
    total = sum((line.line_total for line in lines), ZERO)
    settlement = settle(total, tenders, rate)

    if settlement.on_credit:
        owed = customer_balance(customer)
        if owed + settlement.on_credit > customer.credit_limit:
            room = max(customer.credit_limit - owed, ZERO)
            raise CreditLimitError(
                f"{customer.name} owes ${owed} against a ${customer.credit_limit} limit; "
                f"only ${room} more can go on credit."
            )

    number = next_document_number(DocumentType.INVOICE)
    ref = _invoice_ref(invoice, number, today, user)
    products = lock_products(line.product_id for line in lines if line.product.track_stock)
    for line in lines:
        if line.product.track_stock:
            movement = issue(products[line.product_id], line.quantity, ref, line.pk)
            cost = movement.unit_cost
        else:
            cost = Decimal("0")  # services and labour carry no stock cost
        InvoiceLine.objects.filter(pk=line.pk).update(unit_cost=cost)

    for t in tenders:
        InvoiceTender.objects.create(
            invoice=invoice, kind=t.kind, currency=t.currency,
            amount=t.amount, amount_usd=t.amount_usd, reference=t.reference,
        )

    Invoice.objects.filter(pk=invoice.pk).update(
        number=number,
        status=InvoiceStatus.COMPLETED,
        seller=user,
        sale_date=timezone.now(),
        exchange_rate=rate,
        total=total,
        discount_total=sum(
            ((line.unit_price - line.net_price) * line.quantity for line in lines), ZERO
        ),
        paid_now=settlement.paid_now,
        on_credit=settlement.on_credit,
        change_due=settlement.change_due,
        change_usd=settlement.change_usd,
        change_khr=settlement.change_khr,
        rounding=settlement.rounding,
        due_date=(
            today + timedelta(days=customer.payment_terms_days) if settlement.on_credit else None
        ),
        updated_by=user,
        updated_at=timezone.now(),
    )

    if quote:
        for line in lines:
            QuotationLine.objects.filter(pk=line.quote_line_id).update(
                qty_invoiced=F("qty_invoiced") + line.quantity
            )
        _refresh_quote_status(quote)

    invoice.refresh_from_db()
    return invoice


@transaction.atomic
def void_invoice(invoice, user, reason):
    """Cancel a sale raised in error. Who may void is CanVoidInvoice's call;
    this checks what the void would undo."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != InvoiceStatus.COMPLETED:
        raise DomainError("Only a completed invoice can be voided.")
    if not (reason or "").strip():
        raise DomainError({"reason": "Say why the invoice is being voided."})
    if invoice.allocations.filter(payment__status=PaymentStatus.POSTED).exists():
        raise DomainError("A payment has been applied to this invoice. Void the payment first.")
    if invoice.returns.exists():
        raise DomainError("Goods have been returned against this invoice, so it cannot be voided.")

    ref = _invoice_ref(invoice, invoice.number, timezone.localdate(), user, is_reversal=True)
    reverse_movements(DocumentType.INVOICE, invoice.pk, ref, invoice.number)

    if invoice.quotation_id:
        quote = _quote_for_update(invoice.quotation)
        for line in invoice.lines.filter(quote_line__isnull=False):
            QuotationLine.objects.filter(pk=line.quote_line_id).update(
                qty_invoiced=F("qty_invoiced") - line.quantity
            )
        _refresh_quote_status(quote)

    Invoice.objects.filter(pk=invoice.pk).update(
        status=InvoiceStatus.VOID,
        void_reason=reason.strip(),
        voided_at=timezone.now(),
        voided_by=user,
        updated_by=user,
        updated_at=timezone.now(),
    )
    invoice.refresh_from_db()
    return invoice


# --- customer payment ----------------------------------------------------------------

@transaction.atomic
def record_payment(*, customer, payment_date, tender, currency, amount_tendered,
                   reference="", note="", allocations=None, user=None):
    """allocations: [{"invoice": Invoice, "amount": Decimal}], or None to apply
    the payment to the oldest invoices first. Either way it is applied in full."""
    customer = Customer.objects.select_for_update().get(pk=customer.pk)
    if payment_date > timezone.localdate():
        raise DomainError("The payment date cannot be in the future.")
    if amount_tendered <= 0:
        raise DomainError("The amount must be more than zero.")

    rate = None
    if currency == KHR:
        rate = rate_on(payment_date).rate
        amount = khr_to_usd(amount_tendered, rate)
    else:
        amount = amount_tendered

    # Lock the customer's open invoices so two payments cannot both settle one.
    owing = {
        inv.pk: inv
        for inv in open_invoices(customer).select_for_update(of=("self",))
    }

    if allocations is None:
        allocations, left = [], amount
        for inv in owing.values():
            if left <= 0:
                break
            take = min(left, inv.balance)
            allocations.append({"invoice": inv, "amount": take})
            left -= take

    applied = ZERO
    seen = set()
    for a in allocations:
        inv = owing.get(a["invoice"].pk)
        if inv is None:
            raise DomainError(f"{a['invoice']} is not an open invoice of {customer.name}.")
        if inv.pk in seen:
            raise DomainError(f"{inv.number} is listed twice.")
        seen.add(inv.pk)
        if a["amount"] <= 0:
            raise DomainError("Each amount applied must be more than zero.")
        if a["amount"] > inv.balance:
            raise DomainError(f"{inv.number} owes only ${inv.balance}.")
        applied += a["amount"]
    if applied != amount:
        raise DomainError(
            f"${amount} received but ${applied} applied. A payment cannot sit "
            "unallocated — what is left to apply must reach zero."
        )

    payment = CustomerPayment(
        customer=customer, payment_date=payment_date, tender=tender, currency=currency,
        amount_tendered=amount_tendered, exchange_rate=rate, amount=amount,
        reference=reference, note=note, created_by=user,
    )
    payment.save()
    PaymentAllocation.objects.bulk_create(
        PaymentAllocation(payment=payment, invoice=a["invoice"], amount=a["amount"])
        for a in allocations
    )
    return payment


@transaction.atomic
def void_payment(payment, user, reason):
    payment = CustomerPayment.objects.select_for_update().get(pk=payment.pk)
    if payment.status != PaymentStatus.POSTED:
        raise DomainError("This payment is already void.")
    if not (reason or "").strip():
        raise DomainError({"reason": "Say why the payment is being voided."})
    CustomerPayment.objects.filter(pk=payment.pk).update(
        status=PaymentStatus.VOID, void_reason=reason.strip(),
        voided_at=timezone.now(), voided_by=user, updated_at=timezone.now(),
    )
    payment.refresh_from_db()
    return payment


# --- return ---------------------------------------------------------------------------

def returnable(invoice_line, exclude_return=None):
    """Sold, less what posted returns have already brought back."""
    back = ReturnLine.objects.filter(
        invoice_line=invoice_line, sales_return__status=ReturnStatus.POSTED
    )
    if exclude_return is not None:
        back = back.exclude(sales_return=exclude_return)
    return invoice_line.quantity - (back.aggregate(s=Sum("quantity"))["s"] or 0)


@transaction.atomic
def post_return(ret, user):
    ret = SalesReturn.objects.select_for_update().get(pk=ret.pk)
    if ret.status != ReturnStatus.DRAFT:
        raise DomainError(f"{ret.number} is already posted.")
    if ret.return_date > timezone.localdate():
        raise DomainError("The return date cannot be in the future.")
    # Locking the invoice queues every return against it, so two cannot both
    # take back the last unit.
    invoice = Invoice.objects.select_for_update().get(pk=ret.invoice_id)
    if invoice.status != InvoiceStatus.COMPLETED:
        raise DomainError("Goods can only be returned against a completed invoice.")
    lines = list(ret.lines.select_related("invoice_line__product"))
    if not lines:
        raise DomainError("Add at least one line before posting.")

    per_line = {}
    for line in lines:
        per_line[line.invoice_line_id] = per_line.get(line.invoice_line_id, ZERO) + line.quantity
    for line in lines:
        left = returnable(line.invoice_line)
        if per_line[line.invoice_line_id] > left:
            raise DomainError(
                f"{line.invoice_line.product.code}: only {left} can still come back on {invoice.number}."
            )

    total = sum((line.line_total for line in lines), ZERO)
    credited = min(total, max(invoice_balance(invoice), ZERO))
    refunded = total - credited
    if refunded > 0 and not ret.refund_method:
        raise DomainError(
            {"refund_method": f"${refunded} is to be refunded. Choose cash or KHQR."}
        )

    ref = Ref(
        doc_type=DocumentType.RETURN, doc_id=ret.pk, doc_number=ret.number,
        on=ret.return_date, user=user,
    )
    back_in = [l for l in lines if l.fit_to_sell and l.invoice_line.product.track_stock]
    products = lock_products(l.invoice_line.product_id for l in back_in)
    for line in back_in:
        # Back at the cost it left at, so a return never moves the average for no reason.
        cost = line.invoice_line.unit_cost or Decimal("0")
        receive(products[line.invoice_line.product_id], line.quantity, line.quantity * cost, ref, line.pk)

    SalesReturn.objects.filter(pk=ret.pk).update(
        status=ReturnStatus.POSTED, total=total, credited=credited, refunded=refunded,
        refund_method=ret.refund_method if refunded > 0 else "",
        posted_at=timezone.now(), posted_by=user, updated_by=user, updated_at=timezone.now(),
    )
    ret.refresh_from_db()
    return ret
