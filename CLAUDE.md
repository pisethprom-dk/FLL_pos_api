<!-- v1.3.2 — handover context for Claude Code. Place at the repo root as CLAUDE.md. -->

# POS — tool shop, single outlet

Back office for one hardware shop in Phnom Penh. Prices in US dollars, takes
riel across the counter, sells to walk-ins and to trade customers on credit,
quotes contractors, tracks stock by moving average, logs warranty claims.

**Stack:** Django 5.1 + DRF + Postgres 16, in Docker. Angular 22 frontend in a
separate sibling project, `../pos_frontend` (not started). Deployed on Linux.

---

## How to work on this project

**Discuss before coding.** The concept and approach must be agreed before any
code is generated. This is a firm rule from the project owner, not a
preference. When a decision is ambiguous, ask — do not pick one and build it.

**Every file gets a version comment on line 1** (`# v1.0.0`), bumped on every
change to that file.

**Run the tests before claiming anything works.** The suite is the contract.
Two bugs in this codebase were found only by running tests — a removed Django
API and a missing dependency — both of which looked fine on inspection.

**UI work is delivered as a downloadable file, never as code pasted in chat.**

---

## Running it

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec web python manage.py seed
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py test
```

`seed` creates the rows the system assumes exist: company profile, ten
counters with prefixes (eight documents plus customer and supplier codes), seven
units, an opening exchange rate and the walk-in customer. Safe to re-run.

`seed_demo` loads a sample tool-shop catalogue for development: 11 units, 8
brands, 23 categories, 87 products (data in `catalogue/demo_data.py`, Khmer on
categories and units). It keeps the mockup's codes and prices, leaves existing
rows alone, and never touches stock. Not for the live shop.

`recompute_stock` rebuilds `qty_on_hand` and `avg_cost` from the ledger;
`--check` only reports, and exits non-zero on drift. After a change to
`requirements.txt`, rebuild the image (`docker compose up -d --build`).

API docs at `http://localhost:8000/api/docs/`. There is **no root view** — `/`
returns 404 by design.

### Running tests without Postgres

```python
# test_settings_local.py  (not committed)
from config.settings import *
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
```
```bash
python manage.py test --settings=test_settings_local
```

### When the web container dies on startup

`ERR_CONNECTION_REFUSED` in the browser means the container exited, not a 404.
The entrypoint runs `migrate` before the server, so a system check error or a
migration failure kills it first. `docker compose ps` won't show a dead
container but `docker compose logs web` keeps its output.

---

## Current state

| Slice | App | Status |
|---|---|---|
| 1 | `users` | Done — custom user, JWT auth, roles, permissions |
| 2 | `company`, `catalogue` | Done — profile, exchange rates, numbering, payment notes, categories, brands, units, products |
| 3 | `partners` | Done — customers, walk-in, suppliers, product–supplier links |
| 4 | `inventory` | Done — movement ledger, stock-in, adjustment, count, reversal, CSV/Excel import |
| 5 | `sales` | Done — quotation, invoice (held/complete), void, customer payment, return, balances |
| 6 | `reports` | **Next** — read-only endpoints |

191 tests passing.

**Inventory must come before sales.** A sale decrements stock and stamps a
cost; both live in the movement ledger. Building sales first means writing the
costing twice.

---

## Architecture rules

These are load-bearing. Breaking one produces wrong money figures, not a crash.

### 1. Stock and cost move only when a document is posted

Drafts change nothing. Posting writes `StockMovement` rows and updates the
product projections in the **same transaction**.

### 2. Posted documents are frozen

No edits, no deletes. A correction is a reversing document. This is what stops
last month's numbers changing.

### 3. Inbound cost changes the average; every outbound stamps it

- Stock-in with a cost recalculates `Product.avg_cost`
- A sale line **copies** `avg_cost` onto itself at the moment of sale
- An adjustment **reads** the current average and never sets one
- Opening balance is the only exception — it asserts its own cost, and only
  for a product the ledger has never moved (owner's choice: per product, not
  shop-wide, so a product forgotten at go-live can still come in later)

This is why September's profit report does not change when October stock is
bought at a new price.

### 4. Every sale stores the exchange rate it used

`company.services.rate_on(date)` returns the greatest effective date on or
before that day — not the newest row, not today's rate. A sale stamps the rate;
nothing recomputes it. Reprinting an old invoice must show what was issued.

### 5. Balances and stock positions are derived, not typed

Customer balance = invoices minus payments, computed. `Product.qty_on_hand` and
`avg_cost` are cached projections of the ledger (`editable=False`, read-only on
serializers). Write a recompute command; they will drift.

### 6. Records with history are deactivated, never deleted

FKs to users, products, customers and suppliers are `PROTECT`.

### 7. Business rules live in services, not views

`core/exceptions.py` holds the domain errors. Rules must hold whether the call
comes from the API, the Django admin or a management command. Posting runs
inside `transaction.atomic()` with `select_for_update()` on product rows —
without it two concurrent sales corrupt `qty_on_hand`.

---

## Domain decisions already made

Do not re-litigate these without asking.

**Currency.** USD is the base; every amount is stored in it. Rules live in
`company/currency.py`, not a table — there is no Currency screen. KHR has 0
decimals and rounds to the nearest 100. `split_change($38.55, 4100)` returns
`($38, ៛2300)`.

**Costing.** Moving average, not FIFO. `avg_cost` is `Decimal(12,4)` — four
places, because $26.40 over a carton of 23 does not divide evenly. Round at
display, not at storage.

**Pricing.** Two prices per product: retail and wholesale. Price tier and
credit are **independent** fields on Customer — a cash-paying trade buyer still
gets wholesale. The tier is stamped on the sale.

**Discounts.** **Per line only** — there is no invoice-level or quote-level
discount (owner's decision, replacing the earlier per-invoice discount). A
discount is % or $ off **each unit**: the discounted unit price is the line's
price, line total = net price × quantity, and the invoice total is the sum of
the lines. Cap is 15% per line, **absolute** — no override for anyone,
including Admin. Products flagged `is_price_fixed` cannot be discounted at all.

**Credit limit.** Absolute — no override for anyone. Over the limit, the rest
is paid now, or an Admin raises the limit on the customer first.

**Sale pipeline.** Quotation → Accepted → Invoice. There is **no sale order**.
A quote may be invoiced in parts (`qty_invoiced` per line); it closes only when
every line is fully taken. An expired quote warns but does not block. No stock
reservation.

**Walk-in.** One system customer row (`is_system=True`), retail tier, never
credit, cannot be deleted. Every sale has a customer — no nulls.

**Void and return.** A void cancels an invoice raised in error: **same seller,
same day** (there are no cash sessions in this system), reason required, number
cancelled but never deleted, Admin may void any. A return takes goods back
afterwards, capped at what was sold, per line marked fit-to-sell or not.
Returned goods re-enter stock at the cost stamped on the original sale line,
not today's average. A return's value first reduces what is still owed on
that invoice; anything above that is refunded by cash or KHQR (owner's
choice) — no loose credit is ever left on an account. A void is refused once a
payment or a return is recorded against the invoice.

**Stock.** Whole stock area is **Admin only**. Sellers cannot sell past zero
and there is no override. Carton conversion happens on the stock-in line with
pack size stamped on the line. No landed cost — freight is ignored. Stock count
is partial by category and **blind**: expected quantity hidden while counting,
blank lines skipped rather than zeroed. A replacement from a supplier comes back
by the "Supplier replacement" adjustment reason, at the current average.

**Catalogue.** Categories are two levels, enforced. Brand is its own model. No
variants — a 10mm spanner and a 12mm spanner are two products. Blank barcode
stores as NULL, not `""`.

**Suppliers.** Contact details only. No terms, no bank details, no payables.
Service centres reuse the Supplier model via `supplier_type`.

**Product–supplier link.** A `ProductSupplier` table (owner's choice):
`supplier_sku`, `pack_size`, `is_preferred`, `notes`. One link per pair, at
most one preferred per product — marking a new one unmarks the old. No price on
it: what was paid lives on stock-in lines. Links carry no history, so they may
be deleted; customers and suppliers may not.

**Partner codes.** A blank code is numbered from `DocumentCounter` with the
same fixed six digits as documents: `CUS-000001`, `SUP-000001`. A code typed by
hand is kept, and the counter skips past it. The walk-in is `CUS-000000`.

**Warranty claims.** A tracking log, not a transaction. Seven fields: warranty
number, product code, product name, duration, expiry, note, status. No invoice
link, no money, no stock effect.

**Not in scope:** payables, serial tracking, landed cost, stock reservation,
multiple outlets, variants, loan units, cash sessions. Riel rounding, the
discount cap, the costing method and the negative-stock rule all live in code,
not settings.

---

## Auth

Access token 15 min, in Angular memory only. Refresh token 60 min in an
httpOnly cookie scoped to `/api/auth/`, rotated and blacklisted on every use.
That gives a sliding 60-minute idle logout: an active till never expires, an
idle one dies after an hour. The `auth_time` claim rides through rotations and
caps the whole session at 12 hours.

Serve Angular and the API from one origin through nginx. Cross-origin cookies
with credentials are a harder problem than it looks.

### Frontend (decided, not started)

- Angular 22 in `../pos_frontend`, its own git repo and its own CLAUDE.md.
  Node 22 LTS via nvm (`.nvmrc`); this Mac's default Node 16 is too old.
- Development: `ng serve` proxies `/api` to `http://localhost:8000`, so the
  browser sees one origin, as nginx gives in production. No CORS needed.
- Screens: the mockup's own CSS ported as the app stylesheet, with Angular CDK
  for behaviour only (dialogs, overlays, tables, keyboard access).
- API client: generated from `/api/schema/` (drf-spectacular, request and
  response models split). The schema has **0 errors and 0 warnings**, and
  `core/tests.py` fails the suite the moment it gets one.

### Keeping the schema clean (every new endpoint)

The schema is the contract the Angular client is generated from; anything it
cannot see becomes `any` or a wrong type there.

- An `APIView` or a hand-built body: `@extend_schema(request=..., responses=...)`,
  and render the body through a serializer so schema and output cannot differ.
- A `@action`: declare its request and response — by default the schema
  assumes it takes and returns the action's `serializer_class`.
- A `SerializerMethodField`: `@extend_schema_field(...)`. Money is
  `core.schema.money()` — a decimal string, never a float.
- A filter read from `query_params`: list it with `extend_schema_view(list=...)`
  using the helpers in `core/schema.py`.
- A field some users may not see (cost for a Seller, expected quantity while
  counting) is sent as **null, never left out**, so the generated type is true.
- A read-only status needs an explicit `ChoiceField(read_only=True)`; a new
  enum whose name collides goes in `ENUM_NAME_OVERRIDES`.

### Roles

Two roles as a field on User, not Django groups — groups earn their place at
four or five overlapping roles, not two.

**`users/scopes.py` is the single source of truth.** `/api/auth/me/` returns
the scope list; Angular hides menu items from it rather than hard-coding role
checks, so both ends stay in step.

| Area | Admin | Seller |
|---|---|---|
| Sell, quotations, payments, returns | yes | yes |
| Void | any | own, same day |
| Warranty claims | yes | yes |
| Stock documents | yes | **none** |
| Catalogue, customers, suppliers | write | read |
| Company setup, users | yes | none |
| Reports | all | daily sales filtered to self, stock read-only, **no receivables** |

Permission classes gate **endpoints**. They do not enforce the discount cap,
the negative-stock rule, frozen-after-posting, or quote-must-be-accepted —
those are service-layer rules.

---

## Layout

```
config/       settings, urls
core/         abstract models (TimeStampedModel, ActivatableModel), domain exceptions,
              view mixins, schema.py (OpenAPI helpers)
users/        custom user, JWT cookie auth, scopes, permissions
company/      profile (singleton), exchange rates, document counters, payment notes,
              currency.py (rules in code), services.py (rate_on, next_document_number)
catalogue/    category (2 levels), brand, unit, product
partners/     customer (incl. walk-in), supplier, product–supplier link
inventory/    stock movement ledger, stock-in, adjustment, count, import,
              services.py (the only code that moves stock or cost)
sales/        quotation, invoice, void, customer payment, return,
              money.py (discount cap, settling tenders), services.py (balances)
reports/      NEXT — read-only endpoints
```

---

## Partners — as built

Customer and Supplier share an abstract `Partner` base (code, names, contact,
address, notes). Endpoints under `/api/partners/`: `customers/`,
`customers/lookup/` (till payload, active only, walk-in first), `suppliers/`,
`product-suppliers/`. Admin writes, Seller reads. No DELETE on customers or
suppliers.

**Walk-in.** Guarded three times over: `clean()` for the API and admin forms,
`save()`/`delete()` raising `DomainError` for commands, and database
constraints (one system row; system row is retail, cash, active). It cannot be
renamed or renumbered either. `Customer.walk_in()` returns it.

**Credit.** Credit off forces limit, terms and hold to zero/off on save. Credit
on needs a limit above zero. A check constraint holds both. `credit_status` is
`NO`, `YES` or `HOLD`.

**Balance** lives in `sales.services` (`customer_balance`, `open_invoices`),
worked out from invoices, payments and returns on every call.

**Model rules reach the API** through `ModelCleanMixin` in
`partners/serializers.py`, which runs the model's `clean()` on the instance as
it would be saved. Rules are written once, on the model.

---

## Inventory — as built

**Ledger.** `StockMovement` is append-only: one row per product per posted
line, with signed quantity, unit cost, `value` (six places, so an outbound at
the average is exact), and `qty_after`/`avg_cost_after`, which make it a stock
card. It points at its document by `doc_type`, `doc_id`, `doc_number`.

**One code path moves stock.** `inventory/services.py` → `_move()` →
`next_position()`, the only place the average is computed. `recompute_stock`
replays the ledger through the same function, so it reproduces the figures
exactly. Callers lock products first with `lock_products()` (id order, no
deadlocks) inside `transaction.atomic()`.

**For sales (slice 5):** call `lock_products(ids)`, then `issue(product, qty,
ref)` for each sale line — it refuses to go below zero and returns the
movement, whose `unit_cost` is the cost to stamp on the line. A return calls
`receive(product, qty, qty * stamped_cost, ref)`. Build `ref` with `Ref(...)`
using `DocumentType.INVOICE` / `RETURN`. A void reverses the invoice's
movements the way `reverse_document()` does.

**Documents.** Stock-in (GRN), adjustment (ADJ), count (CNT) share
`StockDocument`: number taken at draft creation (a deleted draft leaves a gap),
`DRAFT` → `POSTED`, then frozen by the model itself. `reverse_document()` posts
a document of the same type that replays each movement with its sign turned —
inbound goes back out taking its own value, outbound comes back at its stamped
cost. Refused once the stock has gone, for a reversal, or a second time.

- Stock-in: pack size defaults from the `ProductSupplier` link. The average
  takes `line_total`, the money paid, not the rounded unit cost.
- Adjustment: direction is fixed by reason. Supplier required for return and
  replacement, refused for the rest. Opening balance allowed while
  `opening_balance_allowed(product)` — never moved, or only by reversed
  opening balances.
- Count: lines for every stock-tracked product in the category and its
  children. `expected_qty` is read when each count is entered, so sales made
  while the count is open are not differences. A product is on one open count
  at a time.

**Import.** `POST .../{id}/import/` with a CSV or .xlsx file. `commit=false`
checks every row (Matched / No such code / Cost missing / …) and adds nothing;
`commit=true` adds the rows that passed. `replace=true` clears the draft first.

**Guard added to catalogue:** a product with stock history cannot be switched
to `track_stock=False`.

---

## Sales — as built

**Money rules** are plain functions in `sales/money.py`: `net_price()` (the
15% cap, fixed prices) and `settle()` (tenders against a total). Riel converts
at the invoice's stamped rate; change comes from cash only, split with
`split_change`; KHQR and credit can never exceed the total. A shortfall under
half a ៛100 note is not a shortfall. Every cent riel rounding creates is kept
in `Invoice.rounding`, so cash reconciles.

**Quotation.** Number at creation. Draft → Sent → Accepted, or Rejected (note
required); Draft may go straight to Accepted. Lines editable while Draft or
Sent, fixed from Accepted. Tier and prices stamped from the customer; no cost,
no rate. `is_expired` is derived and only warns. `qty_invoiced` per line;
Invoiced when every line is taken; a void puts quantity back and re-opens it.

**Invoice.** `HELD` has no number, no stock, no rate; any till may resume or
cancel it. A new customer on a held sale reprices its lines. `complete_invoice()`
in one transaction: locks invoice, customer and quote; checks credit
eligibility, then cover, then the limit; takes the number; stamps the rate;
`issue()`s each stock line and copies the movement's cost to `unit_cost`;
writes tenders. A quote invoice takes only the quote's lines at the quote's
price and discount. Sellers never see `unit_cost` or profit (`cost.view`
scope, Admin only).

**Void.** `CanVoidInvoice` decides who; `void_invoice()` reverses the
invoice's movements through `inventory.services.reverse_movements()` and keeps
the number, marked VOID.

**Customer payment.** Applied in full at save (oldest first when no
allocations are sent), never more than an invoice's balance. Riel stamps the
rate of the payment date. Frozen; an Admin may void it, which re-opens the
invoices.

**Return.** Draft → Posted against one invoice, capped at sold less already
returned. Fit-to-sell lines `receive()` at the sale line's stamped cost; faulty
lines move no stock. Refund value is the sale line's net price.

**Balance** = on credit − payments applied − returns credited, per invoice,
via subqueries in `invoices_with_balance()`. `GET /api/sales/customers/{id}/account/`
serves the till and the payment screen.

---

## Next slice — `reports`

Read-only endpoints: daily sales (by day, seller, tender; Seller sees only
their own), stock on hand, receivables aging. Profit reads the cost stamped on
each sale line. Agree the design before building.

---

## Known gaps

- No root view — `/` is a 404. Worth adding a version/health response.
- Shelf location is used by stock screens but absent from the Products form in
  the mockup.
- Warranty claims have no customer or phone, so nothing says who to call when
  an item is ready for collection.
- The import-list dialog exists in the mockup but nothing opens it. The
  backend import is built; the Angular screens need a button for it.
- The mockup's adjustment reasons lack "Supplier replacement", and its count
  screen should only show expected quantities once posted.
- No barcode entry on stock-in after the search row was removed from the
  mockup.
- The mockup has no screen for product–supplier links, and shows partner codes
  with four digits (`CUS-0001`); the backend uses six.
- The mockup's Returns & voids screen and Void dialog still speak of cash
  sessions. There are none: the rule is same seller, same day.
- The mockup's Exchange rate screen offers THB; `company/currency.py` knows
  only USD and KHR.
- `seed` creates a `WRC-` warranty counter that nothing uses — claims are
  identified by the number on the warranty card. Decide whether to drop it.
- Warranty claims are not assigned to a slice.
- The mockup's Sell screen and quotation still show an invoice-level / quote
  discount; both are gone. A line's $ discount is per unit, not per line.
- The mockup's return dialog offers "Settle by: credit to the account"; the
  backend decides credit vs refund itself and asks only for the refund method.
- A posted return cannot be reversed yet.
- A riel customer payment can leave a cent open on an invoice, because the
  riel amount converts to dollars at two places.

---

## Reference

The UI mockup (`pos-dashboard-mockup.html`, v1.21.1) is a static clickable
prototype of 21 screens with demo data. It is the agreed design and the
best reference for what each screen shows and which rules are visible to the
user. It is not code to port — Angular is a fresh build.
