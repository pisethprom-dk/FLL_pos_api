# POS backend — slices 1–5: users, company, catalogue, partners, inventory, sales

Django + DRF + Postgres, in Docker.

Built so far: users and auth, company settings, the catalogue, partners,
inventory and sales. The reports app comes next. Inventory came first because
a sale has to decrement stock and stamp a cost, and both live in the movement
ledger.

## Running it

    cp .env.example .env
    docker compose up --build

The entrypoint waits for Postgres and runs migrations. Then, in another shell:

If the web container exits straight away and the browser says the connection
was refused, the entrypoint's `migrate` failed before the server started. The
reason is in `docker compose logs web` — the container is gone from
`docker compose ps`, but `logs` still has its output.

    docker compose exec web python manage.py seed
    docker compose exec web python manage.py createsuperuser
    docker compose exec web python manage.py test

`seed` creates the rows the system assumes exist: the company profile, ten
counters with their prefixes (eight documents plus customer and supplier
codes), seven units, an opening exchange rate, and the walk-in customer. It is
safe to run again.

For development, `seed_demo` adds a sample tool-shop catalogue — 11 units,
8 brands (Total, Makita, Bosch and others), 23 categories and 87 products,
including every product the mockup shows:

    docker compose exec web python manage.py seed_demo

The data lives in `catalogue/demo_data.py`. Re-running leaves existing rows
alone. Products start with no stock: quantity and average cost come from the
movement ledger, not from this command. Do not run it on the live shop.

API docs: http://localhost:8000/api/docs/

## How auth works

Access token (15 minutes) is returned in the response body and kept in Angular
memory only. The refresh token (60 minutes) is set as an httpOnly cookie scoped
to `/api/auth/`, so it is not sent on ordinary API calls and JavaScript cannot
read it.

Every refresh rotates the token and blacklists the old one. That gives a
sliding 60-minute idle window: an active till never expires, an idle one is
logged out after an hour. The `auth_time` claim is carried through every
rotation and caps the whole session at 12 hours, so a terminal left on
overnight is dead by morning.

Serve Angular and the API from one origin through nginx in production.
Cross-origin cookies with credentials are a harder problem than it looks.

## Endpoints

    POST /api/auth/login/      username + password -> access, sets refresh cookie
    POST /api/auth/refresh/    reads cookie, rotates, returns new access
    POST /api/auth/logout/     blacklists refresh, clears cookie
    GET  /api/auth/me/         current user, role and scopes
    POST /api/auth/password/   change own password

    GET    /api/users/                      admin only
    POST   /api/users/                      returns a one-time initial password
    PATCH  /api/users/{id}/
    POST   /api/users/{id}/deactivate/
    POST   /api/users/{id}/activate/
    POST   /api/users/{id}/reset-password/

## Roles

Two roles, held as a field on User rather than Django groups — groups earn
their place at four or five roles with overlapping rights, not two.

`users/scopes.py` is the single place that says what each role may do.
`/api/auth/me/` returns that list so Angular hides menu items from it instead
of hard-coding role checks, and the two ends stay in step.

Admin has everything. A Seller may sell, quote, take payments, create returns,
void their own invoice on the day it was raised, and see daily sales filtered
to themselves plus read-only stock. No stock documents, no receivables, no
company setup, no user management.

## What permissions do not cover

Permission classes gate endpoints. They do not enforce business rules — the
15% discount cap, no selling below zero, posted documents being frozen, a
quotation needing to be Accepted before it can be invoiced, an adjustment not
being allowed to set its own cost.

Those belong in a service layer so they hold whether the call arrives from the
API, the Django admin or a management command. `core/exceptions.py` has the
domain errors ready for it. Posting must run inside `transaction.atomic()` with
`select_for_update()` on the product rows, or two concurrent sales will corrupt
`qty_on_hand`.

## Decisions baked in here

- `AUTH_USER_MODEL` is custom from the first migration — swapping later on a
  live database is painful
- Login is by username, not email: shop staff often have no work email
- Users are deactivated, never deleted; sales hold PROTECT keys to them
- `TIME_ZONE = Asia/Phnom_Penh` — every "today's sales" query depends on it
- There are no cash sessions, so the void window is same seller, same day

## Running the tests without Postgres

The suite needs a database. Inside Docker it uses Postgres as configured. To
run it on a bare machine, add a settings override that points at sqlite:

    # test_settings_local.py  (not committed)
    from config.settings import *
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

    python manage.py test --settings=test_settings_local

191 tests cover login, cookie flags, rotation and replay, the absolute cap,
scopes per role, the void window, user creation, riel rounding, historical
rate lookup, document numbering, the singleton profile, category depth, the
catalogue read-only rules, the walk-in guards, customer credit rules, partner
code numbering, product–supplier links, the demo catalogue loader, the moving average,
posting and reversal, blind counts, opening balances, the import, the discount cap, riel change
and rounding, credit limits, quote-to-invoice, voids, payments, returns, plus smoke
checks for Django system errors, missing migrations, and a clean OpenAPI
schema (zero warnings — the Angular client is generated from it).


## Company

Currency rules live in `company/currency.py`, not in a table. The shop prices
in dollars and takes riel across the counter; that will not change, and the
Currency screen was dropped from the design. Decimal places, the riel rounding
step of 100, the notes in circulation and the change-splitting helper are all
there. Only the rate itself is data, because it moves.

`rate_on(date)` returns the greatest effective date on or before that day —
not the newest row, and not today's rate. That is what keeps an old invoice
reprinting exactly as it was issued. A sale stamps its rate; nothing recomputes
it afterwards.

`next_document_number()` takes the number and advances the counter under
`select_for_update()`, so two concurrent sales cannot grab the same invoice
number. Call it inside the transaction that saves the document. Prefixes live
on the counter rows, which is what the Rules and numbering screen edits;
padding is fixed at six digits.

`GET /api/company/rate/` gives the till everything it needs in one call:
today's rate, decimals, rounding step, symbols and note denominations.

## Catalogue

Categories are two levels. The limit is enforced in `clean()` and again in the
serializer, including the case of moving a parent under another parent.

`qty_on_hand` and `avg_cost` are `editable=False` on the model and read-only on
the serializer. They are projections of the stock movement ledger, written only
by posting a document. A test confirms that sending them to the API changes
nothing.

A blank barcode is stored as NULL rather than an empty string, or the second
product without one would break the unique index.

No variants. A 10mm spanner and a 12mm spanner are two products.

## Endpoints added in slice 2

    GET   PATCH  /api/company/profile/
    GET          /api/company/rate/
    GET   POST   /api/company/exchange-rates/
    GET   PATCH  /api/company/numbering/          admin only
    GET   POST   /api/company/payment-notes/

    GET   POST   /api/catalogue/categories/
    GET   POST   /api/catalogue/brands/
    GET   POST   /api/catalogue/units/
    GET   POST   /api/catalogue/products/
    GET          /api/catalogue/products/lookup/  small payload for the till

Writes are Admin only; a Seller reads. Product filters: `category` (includes
sub-categories), `brand`, `search`, `below_reorder`, `out_of_stock`, `active`.


## Partners

Customers and suppliers share an abstract `Partner` base. Neither holds any
money figure: what a customer owes is worked out from invoices less payments
once the sales app exists, and suppliers have no terms or payables at all.

The walk-in customer (`CUS-000000`) is one system row created by `seed`. Every
sale without a named customer is recorded against it, so it cannot be renamed,
renumbered, given credit, set to wholesale, deactivated or deleted. The model,
the serializer and database constraints each refuse it.

Price tier and credit are independent. Credit off clears the limit, terms and
hold on save; credit on needs a limit above zero.

A blank code is numbered from the company counters with six digits —
`CUS-000001`, `SUP-000001`. A code typed by hand is kept and skipped later.

`ProductSupplier` records which supplier carries which product: their own SKU,
their usual pack size, whether they are preferred, and a note. One preferred
per product; marking another unmarks the first. No price — that lives on the
stock-in line.

## Endpoints added in slice 3

    GET   POST   PATCH          /api/partners/customers/
    GET                         /api/partners/customers/lookup/   till payload, walk-in first
    GET   POST   PATCH          /api/partners/suppliers/
    GET   POST   PATCH  DELETE  /api/partners/product-suppliers/

Writes are Admin only; a Seller reads. Customer filters: `active`,
`price_tier`, `credit` (`yes` includes on hold, `no`, `hold`), `search`.
Supplier filters: `active`, `supplier_type`, `search`. Link filters:
`product`, `supplier`, `preferred`.


## Inventory

Stock and cost move only when a document is posted. A draft changes nothing; a
posted document is frozen, and a mistake is put right by reversing it, which
posts a document of the same type with every movement turned round.

Every change lands in `StockMovement`, an append-only ledger whose
`qty_after` and `avg_cost_after` make it a stock card. `Product.qty_on_hand`
and `avg_cost` are kept in step in the same transaction, on locked rows.
`recompute_stock --check` confirms they still agree with the ledger.

- **Stock-in** — bought in packs, received in units. Pack size defaults from
  the supplier link and is stamped on the line. The average takes in the money
  actually paid.
- **Adjustment** — the reason sets the direction. Damage, loss, shop use,
  warranty replacement and return to supplier go out at the average; supplier
  replacement comes in at it. Only an opening balance types a cost, and only
  for a product the ledger has never moved.
- **Count** — one category at a time, blind until posted. Blank lines are
  skipped. Differences are worked out against what the system held when each
  line was entered, so the till can keep selling during a count.

Opening stock for a new shop: create an Opening balance adjustment, import a
CSV or Excel file (code, quantity, unit cost) onto it, check, then post.

The whole stock area is Admin only.

## Endpoints added in slice 4

    GET POST PATCH DELETE  /api/inventory/stock-ins/          DELETE on drafts only
    POST                   /api/inventory/stock-ins/{id}/post/
    POST                   /api/inventory/stock-ins/{id}/reverse/   {"note": "why"}
    POST                   /api/inventory/stock-ins/{id}/import/    multipart: file, has_header, replace, commit
    (the same four for)    /api/inventory/adjustments/
    GET POST PATCH DELETE  /api/inventory/counts/             POST starts a count; DELETE abandons a draft
    POST                   /api/inventory/counts/{id}/record/       {"lines": [{"line": id, "counted_qty": "5"}]}
    POST                   /api/inventory/counts/{id}/post/   and /reverse/
    GET                    /api/inventory/movements/          the ledger

Lines are sent whole with the document: on PATCH, a `lines` list replaces the
draft's lines. Document filters: `status`, `date_from`, `date_to`, `search`,
plus `supplier` (stock-ins, adjustments), `reason` (adjustments), `category`
(counts). Movement filters: `product`, `doc_type`, `doc_number`, `reason`,
`date_from`, `date_to`.


## Sales

A sale is held (no number, no stock, no rate) until it is completed. Completing
it takes the number, stamps the day's exchange rate, takes each item out of
stock at the current average — copying that cost onto the line — and records
how it was paid. All of it happens or none of it does.

- **Discount** is per line only, % or $ off each unit, never more than 15%, and
  none at all on a fixed-price product. There is no invoice-level discount.
- **Payment at the till** — cash in dollars or riel, KHQR, credit, in any mix.
  Change comes from cash only, as dollars plus riel rounded to ៛100.
- **Credit** — only for a customer allowed it and not on hold, never past the
  limit, due after the customer's payment terms.
- **Quotation** — accepted quotes are invoiced in parts at the agreed prices.
- **Void** — same seller, same day, or an Admin; stock comes back at the cost
  it left at.
- **Customer payment** — applied in full to open invoices, oldest first unless
  told otherwise.
- **Return** — capped at what was sold; fit goods back in stock at their sale
  cost; the value pays down that invoice first, the rest is refunded.

What a customer owes is never stored: it is credit taken, less payments, less
returns credited.

## Endpoints added in slice 5

    GET POST PATCH DELETE  /api/sales/quotations/           DELETE on drafts only
    POST                   /api/sales/quotations/{id}/send/  /accept/  /reject/ {"note"}
    GET POST PATCH DELETE  /api/sales/invoices/             POST holds a sale; DELETE cancels a held one
    POST                   /api/sales/invoices/{id}/complete/   {"tenders": [{"kind","currency","amount","reference"}]}
    POST                   /api/sales/invoices/{id}/void/       {"reason"}
    GET POST               /api/sales/payments/             allocations optional — oldest first
    POST                   /api/sales/payments/{id}/void/       {"reason"}  Admin only
    GET POST PATCH DELETE  /api/sales/returns/              DELETE on drafts only
    POST                   /api/sales/returns/{id}/post/
    GET                    /api/sales/customers/{id}/account/   balance, room left, open invoices

Tender kinds: `CASH`, `KHQR`, `CREDIT`; currency `USD` or `KHR`. Filters:
`status`, `customer`, `date_from`, `date_to` on all lists, plus `seller`,
`quotation`, `search` (invoices), `open`, `search` (quotations), `invoice`
(returns).
