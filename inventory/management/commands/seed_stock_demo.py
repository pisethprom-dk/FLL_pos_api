# v1.0.0 — a sample stock history, as a working tool shop would have it:
# opening balances at go-live, stock-ins twice a week, adjustments of every
# kind, a count at each month end, a mistake reversed, drafts waiting. For
# development and demos, like seed_demo — never the live shop.
#
# Every document goes through inventory/services.py, the code the screens
# post through, in date order, so stock, average cost and the ledger are what
# real use would make them. Each document's stamps are then set to its own
# date in shop hours, as if it had been entered that day. A fixed random seed
# gives the same history every time.
#
#   manage.py seed_stock_demo --user Piseth [--reset] [--since 2026-08-01]
#
# Without --reset it refuses while any stock record exists, so it never mixes
# with a real history. --reset clears the stock area first — every document
# and movement, each product's stock and average cost, the GRN/ADJ/CNT
# numbers — and refuses once there are sales, whose costs come from the ledger.
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from catalogue.models import Category, Product, Unit
from company.models import DocumentCounter, DocumentType
from inventory.models import (
    Adjustment,
    AdjustmentLine,
    AdjustmentReason,
    DocStatus,
    StockCount,
    StockCountLine,
    StockIn,
    StockInLine,
    StockMovement,
)
from inventory.services import (
    post_adjustment,
    post_count,
    post_stock_in,
    record_counts,
    reverse_document,
    start_count,
)
from partners.models import ProductSupplier, Supplier, SupplierType
from sales.models import Invoice, InvoiceStatus, SalesReturn
from users.models import Role, User

CENT = Decimal("0.01")
GO_LIVE = date(2026, 8, 1)

# name, type, contact, phone, invoice prefix, share of the price list they
# charge, and the brands they carry. The last carries everything else.
SUPPLIERS = [
    ("Total Cambodia", SupplierType.WHOLESALER, "Sophal Keo", "023 880 412", "TT",
     "0.74", {"Total", "Ingco"}),
    ("Makita Cambodia", SupplierType.DISTRIBUTOR, "Vanna Chea", "023 216 905", "MKT",
     "0.82", {"Makita"}),
    ("Bosch Power Tools Cambodia", SupplierType.DISTRIBUTOR, "Dara Sok", "023 999 314", "BSH",
     "0.82", {"Bosch"}),
    ("Safe Work Trading", SupplierType.IMPORTER, "Malis Chan", "012 778 204", "SWT",
     "0.70", {"3M"}),
    ("Lim Heng Hardware Wholesale", SupplierType.WHOLESALER, "Lim Heng", "012 455 610", "LH",
     "0.68", None),
]
SAFETY_WEAR = "CAT-04"  # unbranded safety wear comes from the safety importer
# Who delivers on each stock-in day, in turn: Total most often.
ROUND = ["Total Cambodia", "Lim Heng Hardware Wholesale", "Makita Cambodia", "Total Cambodia",
         "Bosch Power Tools Cambodia", "Lim Heng Hardware Wholesale", "Total Cambodia",
         "Safe Work Trading"]

R = AdjustmentReason
# days after go-live, reason, supplier, [(product code, quantity)], note
ADJUSTMENTS = [
    (9, R.DAMAGE, None, [("AC-0511", 6)], "A carton fell from the top shelf; six discs cracked."),
    (17, R.SHOP_USE, None, [("SF-0402", 6)], "Gloves for the staff who unload deliveries."),
    (24, R.RETURN_TO_SUPPLIER, "Makita Cambodia", [("TL-0118", 1)],
     "Faulty on arrival: the motor does not start."),
    (31, R.LOSS, None, [("TL-0280", 1)], "Missing from the display rack."),
    (38, R.SUPPLIER_REPLACEMENT, "Makita Cambodia", [("TL-0118", 1)],
     "Replacement for the grinder sent back faulty on arrival."),
    (45, R.WARRANTY_REPLACEMENT, None, [("TL-0101", 1)],
     "A new drill for a customer under warranty; theirs went to Bosch for repair."),
    (52, R.DAMAGE, None, [("AC-0520", 2)], "Teeth chipped in storage."),
    (58, R.RETURN_TO_SUPPLIER, "Lim Heng Hardware Wholesale", [("FX-0303", 5)],
     "Wrong size delivered; five boxes sent back."),
    (61, R.SHOP_USE, None, [("EL-0702", 3)], "Used repairing the shop's display lighting."),
]
DRAFT_ADJUSTMENT = (R.DAMAGE, [("TL-0262", 1)], "Handle cracked; waiting for the owner to look at it.")
# The category counted at each month end, in turn; Power tools is counted now.
MONTH_END_COUNTS = ["CAT-03", "CAT-02", "CAT-04", "CAT-07", "CAT-08", "CAT-06"]
COUNTING_NOW = "CAT-01"


def month_ends(after, before):
    """Each last day of a month strictly between the two dates."""
    day = after
    while True:
        first_next = date(day.year + day.month // 12, day.month % 12 + 1, 1)
        end = first_next - timedelta(days=1)
        if end >= before:
            return
        if end > after:
            yield end
        day = first_next


class Command(BaseCommand):
    help = "Load a sample stock history (development only). See the top of the file."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="The Admin who enters the documents.")
        parser.add_argument("--reset", action="store_true",
                            help="Clear every stock record first (refused once there are sales).")
        parser.add_argument("--since", type=date.fromisoformat, default=GO_LIVE,
                            help="Go-live: the day of the opening balances (default 2026-08-01).")

    def handle(self, *args, **options):
        self.user = User.objects.filter(username=options["user"], role=Role.ADMIN).first()
        if self.user is None:
            raise CommandError(f"No Admin named {options['user']!r}: stock is entered by an Admin.")
        self.today = timezone.localdate()
        self.since = options["since"]
        if self.since > self.today - timedelta(days=14):
            raise CommandError("--since must be at least two weeks ago.")
        self.rng = random.Random(2026)
        self.clock = {}
        self.mistake_made = False

        with transaction.atomic():
            if options["reset"]:
                self.reset()
            elif self.has_stock_records():
                raise CommandError(
                    "There are stock records already. Use --reset to clear them first "
                    "(development only)."
                )
            self.build()
        self.report()

    # --- clearing ---------------------------------------------------------

    def has_stock_records(self):
        return any(m.objects.exists() for m in (StockIn, Adjustment, StockCount, StockMovement))

    def reset(self):
        if Invoice.objects.exclude(status=InvoiceStatus.HELD).exists() or SalesReturn.objects.exists():
            raise CommandError(
                "There are sales or returns. Their costs come from the stock ledger, so it "
                "cannot be cleared."
            )
        StockMovement.objects.all().delete()
        for line in (StockCountLine, AdjustmentLine, StockInLine):
            line.objects.all().delete()
        for doc in (StockCount, Adjustment, StockIn):
            doc.objects.exclude(reverses=None).delete()  # a reversal points at its original
            doc.objects.all().delete()
        Product.objects.update(qty_on_hand=0, avg_cost=0)
        DocumentCounter.objects.filter(
            doc_type__in=[DocumentType.STOCK_IN, DocumentType.ADJUSTMENT, DocumentType.COUNT]
        ).update(next_number=1)

    # --- the history ------------------------------------------------------

    def build(self):
        self.products = list(
            Product.objects.filter(track_stock=True, is_active=True)
            .select_related("unit", "brand", "category__parent").order_by("code")
        )
        self.by_code = {p.code: p for p in self.products}
        self.units = {u.code: u for u in Unit.objects.all()}
        self.suppliers()

        events = [(self.since, 0, self.opening)]
        for n, day in enumerate(self.delivery_days()):
            events.append((day, 1, lambda day=day, n=n: self.delivery(day, n)))
        for offset, reason, supplier, lines, note in ADJUSTMENTS:
            day = self.since + timedelta(days=offset)
            if day < self.today:
                events.append((day, 2, lambda day=day, a=(reason, supplier, lines, note): self.adjust(day, *a)))
        for n, day in enumerate(month_ends(self.since, self.today)):
            code = MONTH_END_COUNTS[n % len(MONTH_END_COUNTS)]
            events.append((day, 3, lambda day=day, code=code: self.month_end_count(day, code)))
        events.append((self.today, 9, self.waiting))
        for _day, _order, run in sorted(events, key=lambda e: (e[0], e[1])):
            run()

    def suppliers(self):
        """The suppliers, and each stock product's usual supplier and pack."""
        self.supplier_of = {}
        self.terms = {}
        made = []
        for name, kind, contact, phone, prefix, share, brands in SUPPLIERS:
            supplier = Supplier.objects.filter(name=name).first()
            if supplier is None:
                supplier = Supplier.objects.create(
                    name=name, supplier_type=kind, contact_person=contact, phone=phone,
                    country="Cambodia", created_by=self.user,
                )
                made.append(supplier)
            self.terms[supplier.pk] = (prefix, Decimal(share))
            self.supplier_of[name] = supplier
        self.suppliers_added = len(made)

        self.links = {}
        self.cost = {}
        for product in self.products:
            supplier = self.supplier_for(product)
            unit, size = self.pack_for(product)
            link = ProductSupplier.objects.filter(product=product, supplier=supplier).first()
            if link is None:
                link = ProductSupplier.objects.create(
                    product=product, supplier=supplier, pack_unit=unit, pack_size=size,
                    supplier_sku=f"{self.terms[supplier.pk][0]}-{product.code}",
                    is_preferred=not product.supplier_links.filter(is_preferred=True).exists(),
                )
            self.links[product.pk] = link
            share = self.terms[supplier.pk][1]
            self.cost[product.pk] = max((product.wholesale_price * share).quantize(CENT), CENT)

    def supplier_for(self, product):
        brand = product.brand.name if product.brand else None
        for name, *_rest, brands in SUPPLIERS:
            if brands and brand in brands:
                return self.supplier_of[name]
        top = product.category.parent or product.category
        if top.code == SAFETY_WEAR:
            return self.supplier_of["Safe Work Trading"]
        return self.supplier_of["Lim Heng Hardware Wholesale"]

    def pack_for(self, product):
        """Cartons of the cheap things, rolls of cable, one at a time for the rest."""
        price = product.wholesale_price
        if product.unit.code == "M":
            return self.units.get("ROLL"), Decimal("100")
        if product.unit.code == "KG" or price >= 10 or "CTN" not in self.units:
            return None, Decimal("1")
        if price < Decimal("0.50"):
            return self.units["CTN"], Decimal("100")
        if price < 2:
            return self.units["CTN"], Decimal("50")
        return self.units["CTN"], Decimal("12")

    def opening(self):
        """Go-live: what was on the shelf, one document per category."""
        tops = {}
        for product in self.products:
            top = product.category.parent or product.category
            tops.setdefault(top, []).append(product)
        for top in sorted(tops, key=lambda c: (c.display_order, c.code)):
            doc = Adjustment.objects.create(
                reason=R.OPENING_BALANCE, doc_date=self.since, created_by=self.user,
                note=f"Opening balance at go-live: {top.name}, counted on the shelf.",
            )
            for product in tops[top]:
                AdjustmentLine.objects.create(
                    document=doc, product=product, quantity=self.opening_qty(product),
                    unit_cost=self.cost[product.pk],
                )
            self.post(post_adjustment, doc, self.since)

    def opening_qty(self, product):
        price, unit, rng = product.wholesale_price, product.unit.code, self.rng
        if unit == "M":
            return Decimal(rng.randint(3, 6) * 100)
        if unit == "KG":
            return Decimal(rng.randint(4, 10) * 10)
        if price >= 100:
            return Decimal(rng.randint(2, 4))
        if price >= 40:
            return Decimal(rng.randint(3, 8))
        if price >= 10:
            return Decimal(rng.randint(6, 15))
        if price >= 2:
            return Decimal(rng.randint(15, 40))
        return Decimal(rng.randint(6, 20) * 10)

    def delivery_days(self):
        """Mondays and Thursdays after go-live, up to yesterday."""
        day = self.since + timedelta(days=1)
        while day < self.today:
            if day.weekday() in (0, 3):
                yield day
            day += timedelta(days=1)

    def delivery(self, day, n):
        supplier = self.supplier_of[ROUND[n % len(ROUND)]]
        lines = self.order(supplier, day)
        if not lines:
            return
        prefix = self.terms[supplier.pk][0]
        ref = f"{prefix}-{self.rng.randint(10000, 99999)}"
        cartons = [i for i, (product, *_rest) in enumerate(lines) if self.links[product.pk].pack_size > 1]
        if not self.mistake_made and n >= 5 and cartons:  # once, a few weeks in
            self.mistake_made = True
            self.mistake(day, supplier, ref, lines, cartons[0])
            return
        self.post(post_stock_in, self.stock_in(day, supplier, ref, lines), day)

    def order(self, supplier, day):
        """2–8 of this supplier's products, in packs, at this month's price."""
        theirs = [p for p in self.products if self.links[p.pk].supplier_id == supplier.pk]
        if not theirs:
            return []
        picked = self.rng.sample(theirs, min(len(theirs), self.rng.randint(2, 8)))
        # Prices creep up about 1.5% a month, give or take a little.
        months = (day.year - self.since.year) * 12 + day.month - self.since.month
        creep = Decimal("1") + Decimal("0.015") * months + Decimal(self.rng.choice([-1, 0, 0, 1])) / 100
        lines = []
        for product in sorted(picked, key=lambda p: p.code):
            link = self.links[product.pk]
            price = product.wholesale_price
            if link.pack_size > 1:
                packs = self.rng.randint(1, 3)
            elif product.unit.code == "KG":
                packs = self.rng.randint(2, 5) * 10
            elif price >= 100:
                packs = self.rng.randint(1, 2)
            elif price >= 40:
                packs = self.rng.randint(2, 4)
            else:
                packs = self.rng.randint(3, 8)
            pack_cost = (self.cost[product.pk] * link.pack_size * creep).quantize(CENT)
            lines.append((product, Decimal(packs), pack_cost))
        return lines

    def stock_in(self, day, supplier, ref, lines, note=""):
        doc = StockIn.objects.create(
            supplier=supplier, doc_date=day, supplier_ref=ref, note=note, created_by=self.user,
        )
        for product, packs, pack_cost in lines:
            link = self.links[product.pk]
            StockInLine.objects.create(
                document=doc, product=product, pack_unit=link.pack_unit, packs=packs,
                pack_size=link.pack_size, pack_cost=pack_cost,
            )
        return doc

    def mistake(self, day, supplier, ref, lines, typo):
        """A carton's cost typed as a piece's: posted, reversed the next day, entered again."""
        wrong = [
            (p, packs, max((cost / self.links[p.pk].pack_size).quantize(CENT), CENT) if i == typo else cost)
            for i, (p, packs, cost) in enumerate(lines)
        ]
        original = self.post(post_stock_in, self.stock_in(day, supplier, ref, wrong), day)
        next_day = min(day + timedelta(days=1), self.today)
        reversal = reverse_document(original, self.user, "Cost typed per piece, not per carton.")
        StockIn.objects.filter(pk=reversal.pk).update(doc_date=next_day)
        StockMovement.objects.filter(doc_type=DocumentType.STOCK_IN, doc_id=reversal.pk).update(
            movement_date=next_day
        )
        reversal.doc_date = next_day
        self.stamp(reversal, next_day)
        again = self.stock_in(
            next_day, supplier, ref, lines,
            note=f"Entered again: {original.number} had a carton's cost typed per piece.",
        )
        self.post(post_stock_in, again, next_day)

    def adjust(self, day, reason, supplier_name, lines, note):
        doc = Adjustment(
            reason=reason, doc_date=day, note=note, created_by=self.user,
            supplier=self.supplier_of[supplier_name] if supplier_name else None,
        )
        rows = []
        for code, qty in lines:
            product = self.by_code.get(code)
            if product is None:
                continue
            product.refresh_from_db(fields=["qty_on_hand"])
            if not doc.is_inbound:
                qty = min(Decimal(qty), product.qty_on_hand)
            if qty > 0:
                rows.append((product, Decimal(qty)))
        if not rows:
            return
        doc.save()
        for product, qty in rows:
            AdjustmentLine.objects.create(document=doc, product=product, quantity=qty)
        self.post(post_adjustment, doc, day)

    def month_end_count(self, day, code):
        category = Category.objects.filter(code=code).first()
        if category is None:
            return
        count = start_count(category, self.user, doc_date=day, note=f"Month-end count: {category.name}.")
        entries = []
        for line in count.lines.select_related("product"):
            on_hand = line.product.qty_on_hand
            roll = self.rng.random()
            if roll < 0.10:
                delta = -2 if on_hand >= 20 else -1
            elif roll < 0.14:
                delta = 1
            else:
                delta = 0
            entries.append({"line": line.pk, "counted_qty": max(on_hand + delta, Decimal("0"))})
        record_counts(count, entries)
        self.post(post_count, count, day)

    def waiting(self):
        """Today: a delivery whose invoice is not in yet, a damage to look at, a count under way."""
        makita = self.supplier_of["Makita Cambodia"]
        lines = self.order(makita, self.today)[:2]
        if lines:
            doc = self.stock_in(
                self.today, makita, "", lines,
                note="Goods arrived; their invoice is not in yet, so the cost is last time's.",
            )
            self.stamp(doc, self.today)
        reason, rows, note = DRAFT_ADJUSTMENT
        rows = [(self.by_code[c], q) for c, q in rows if c in self.by_code]
        if rows:
            doc = Adjustment.objects.create(reason=reason, note=note, created_by=self.user)
            for product, qty in rows:
                AdjustmentLine.objects.create(document=doc, product=product, quantity=Decimal(qty))
            self.stamp(doc, self.today)
        category = Category.objects.filter(code=COUNTING_NOW).first()
        if category is not None:
            count = start_count(category, self.user, note="Cycle count; the back store room is still to do.")
            lines = list(count.lines.select_related("product"))[::2]
            entries = [{"line": l.pk, "counted_qty": l.product.qty_on_hand} for l in lines]
            if entries:
                entries[-1]["counted_qty"] = max(entries[-1]["counted_qty"] - 1, Decimal("0"))
                record_counts(count, entries)
            self.stamp(count, self.today)

    # --- posting and dating -------------------------------------------------

    def post(self, service, doc, day):
        posted = service(doc, self.user)
        self.stamp(posted, day)
        return posted

    def stamp(self, doc, day):
        """Dates the document's records to its own day, a little after the last one."""
        self.clock[day] = self.clock.get(day, 0) + 1
        at = timezone.make_aware(datetime.combine(day, time(8, 0)) + timedelta(minutes=35 * self.clock[day]))
        model = type(doc)
        fields = {"created_at": at, "updated_at": at}
        if doc.status == DocStatus.POSTED:
            fields["posted_at"] = at
        model.objects.filter(pk=doc.pk).update(**fields)
        StockMovement.objects.filter(doc_type=doc.DOC_TYPE, doc_id=doc.pk).update(posted_at=at)
        if model is StockCount:
            StockCountLine.objects.filter(document=doc, counted_at__isnull=False).update(counted_at=at)

    def report(self):
        def tally(model):
            posted = model.objects.filter(status=DocStatus.POSTED)
            return (f"{posted.filter(reverses=None).count()} posted, "
                    f"{posted.exclude(reverses=None).count()} reversal(s), "
                    f"{model.objects.filter(status=DocStatus.DRAFT).count()} draft")
        self.stdout.write(f"Suppliers added: {self.suppliers_added}; supplier links: {len(self.links)}")
        self.stdout.write(f"Stock in:    {tally(StockIn)}")
        self.stdout.write(f"Adjustments: {tally(Adjustment)}")
        self.stdout.write(f"Counts:      {tally(StockCount)}")
        self.stdout.write(self.style.SUCCESS(
            f"Stock history from {self.since:%d %b %Y} to {self.today:%d %b %Y} loaded."
        ))
