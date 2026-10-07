# v1.0.3 — one place that says what each role may do.
#
# /api/auth/me/ returns this list so the Angular app hides menu items from it
# rather than hard-coding role checks in the frontend. When a rule changes here,
# both ends change together.

ADMIN_SCOPES = [
    "sell",
    "quotation.view", "quotation.edit",
    "payment.view", "payment.record",
    # Voiding a customer payment reopens the invoices it paid (Admin only).
    "payment.void",
    "return.view", "return.create",
    "invoice.void.any",
    "warranty.view", "warranty.edit",
    # Deleting a warranty claim logged by mistake (Admin only).
    "warranty.delete",
    "stock.view", "stock.post",
    "catalogue.view", "catalogue.edit",
    "partner.view", "partner.edit",
    "company.view", "company.edit",
    "user.manage",
    "report.sales.all",
    "report.stock",
    "report.receivables",
    # Cost and margin on invoices. A seller sees prices, never what they cost.
    "cost.view",
]

SELLER_SCOPES = [
    "sell",
    "quotation.view", "quotation.edit",
    "payment.view", "payment.record",
    "return.view", "return.create",
    # A seller may void only their own invoice, and only on the same day.
    "invoice.void.own",
    "warranty.view", "warranty.edit",
    "catalogue.view",
    "partner.view",
    # Daily sales, filtered to this seller. No receivables.
    "report.sales.own",
    "report.stock",
]

SCOPES_BY_ROLE = {
    "ADMIN": ADMIN_SCOPES,
    "SELLER": SELLER_SCOPES,
}


def scopes_for(user):
    return SCOPES_BY_ROLE.get(user.role, [])


def has_scope(user, scope):
    return scope in scopes_for(user)
