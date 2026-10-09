# v1.1.0 — reading a product list from a CSV or Excel file onto a draft.
#
# Two calls, same file: the first checks every row and adds nothing, the second
# adds the rows that passed. A row with a problem is never imported — the file
# is fixed and loaded again, or the product is added by hand.
#
# Columns: code, quantity, unit cost, then optionally pack size and pack unit.
# With a header row they are found by name, in any order; without one they are
# read in that order. On a stock-in with a pack size, quantity is the number of
# packs and unit cost is the cost of one pack.
import csv
import io
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models.functions import Upper

from catalogue.models import Product, Unit
from core.exceptions import DomainError
from inventory.models import Adjustment, AdjustmentLine, AdjustmentReason, StockIn, StockInLine
from inventory.services import default_pack, opening_balance_allowed

MAX_ROWS = 5000
COLUMNS = ("code", "quantity", "unit_cost", "pack_size", "pack_unit")
ALIASES = {
    "code": "code", "product_code": "code", "product": "code", "sku": "code",
    "quantity": "quantity", "qty": "quantity",
    "unit_cost": "unit_cost", "cost": "unit_cost", "cost_each": "unit_cost",
    "pack_size": "pack_size", "per_pack": "pack_size",
    "pack_unit": "pack_unit", "bought_as": "pack_unit",
}
MATCHED = "Matched"


class ImportFileError(DomainError):
    default_detail = "That file could not be read."
    default_code = "import_file"


def read_table(upload):
    """Rows of raw cell values from a .csv or .xlsx upload."""
    name = (upload.name or "").lower()
    try:
        if name.endswith(".csv"):
            text = io.TextIOWrapper(upload.file, encoding="utf-8-sig", newline="")
            rows = list(csv.reader(text))
        elif name.endswith(".xlsx"):
            from openpyxl import load_workbook

            book = load_workbook(upload.file, read_only=True, data_only=True)
            rows = [list(r) for r in book.worksheets[0].iter_rows(values_only=True)]
            book.close()
        else:
            raise ImportFileError("Use a .csv or .xlsx file.")
    except ImportFileError:
        raise
    except Exception:
        raise ImportFileError("That file could not be read as CSV or Excel.")
    if len(rows) > MAX_ROWS + 1:
        raise ImportFileError(f"A file may hold at most {MAX_ROWS} rows.")
    return rows


def _key(header):
    return str(header or "").strip().lower().replace(" ", "_").replace("-", "_")


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # Excel turns a code like 1001 into 1001.0
    return str(value).strip()


def _number(value):
    """None when blank; raises ValueError when not a number."""
    text = _text(value).replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError(text)


def to_records(rows, has_header=True):
    """[(spreadsheet row number, {column: raw value})], blank rows dropped."""
    if has_header:
        if not rows:
            return []
        positions = {}
        for i, header in enumerate(rows[0]):
            column = ALIASES.get(_key(header))
            if column and column not in positions:
                positions[column] = i
        if "code" not in positions or "quantity" not in positions:
            raise ImportFileError("The header row needs at least a code and a quantity column.")
        body, first = rows[1:], 2
    else:
        positions = {column: i for i, column in enumerate(COLUMNS)}
        body, first = rows, 1

    records = []
    for number, row in enumerate(body, start=first):
        cells = {c: (row[i] if i < len(row) else None) for c, i in positions.items()}
        if all(_text(v) == "" for v in cells.values()):
            continue
        records.append((number, cells))
    return records


def check_records(records, *, needs_cost, stock_in=False, opening=False):
    """One result per row. Only rows whose result is MATCHED may be imported."""
    codes = {_text(cells.get("code")).upper() for _, cells in records}
    products = {
        p.code.upper(): p
        for p in Product.objects.annotate(code_upper=Upper("code")).filter(code_upper__in=codes)
    }
    units = {}
    for unit in Unit.objects.all():
        units[unit.code.upper()] = unit
        units[unit.name.upper()] = unit

    seen = set()
    results = []
    for number, cells in records:
        code = _text(cells.get("code"))
        product = products.get(code.upper())
        row = {
            "row": number, "code": code,
            "product": product.pk if product else None,
            "product_name": product.name if product else "",
            "quantity": None, "unit_cost": None, "pack_size": None, "pack_unit": None,
        }
        try:
            row["quantity"] = _number(cells.get("quantity"))
            row["unit_cost"] = _number(cells.get("unit_cost"))
            row["pack_size"] = _number(cells.get("pack_size"))
        except ValueError as exc:
            row["result"] = f"Not a number: {exc}"
            results.append(row)
            continue
        unit_text = _text(cells.get("pack_unit"))
        unit = units.get(unit_text.upper()) if unit_text else None
        row["pack_unit"] = unit.pk if unit else None

        if not code:
            result = "Code missing"
        elif product is None:
            result = "No such code"
        elif row["quantity"] is None or row["quantity"] <= 0:
            result = "Quantity missing"
        elif not product.track_stock:
            result = "Does not track stock"
        elif stock_in and not product.is_active:
            result = "Product is inactive"
        elif needs_cost and (row["unit_cost"] is None or row["unit_cost"] <= 0):
            result = "Cost missing"
        elif row["pack_size"] is not None and row["pack_size"] <= 0:
            result = "Pack size must be above zero"
        elif unit_text and unit is None:
            result = "No such unit"
        elif opening and product.pk in seen:
            result = "Listed twice"
        elif opening and not opening_balance_allowed(product):
            result = "Already has stock movements"
        else:
            result = MATCHED
            seen.add(product.pk)
        row["result"] = result
        results.append(row)
    return results


@transaction.atomic
def import_lines(doc, upload, *, has_header=True, replace=False, commit=False):
    """Check the file against `doc`, and with commit add the rows that passed."""
    if doc.was_posted():
        raise DomainError(f"{doc.number} is posted. Lines can only be added to a draft.")
    stock_in = isinstance(doc, StockIn)
    opening = isinstance(doc, Adjustment) and doc.reason == AdjustmentReason.OPENING_BALANCE

    records = to_records(read_table(upload), has_header)
    results = check_records(
        records, needs_cost=stock_in or opening, stock_in=stock_in, opening=opening
    )
    ready = [r for r in results if r["result"] == MATCHED]

    imported = 0
    if commit and ready:
        if replace:
            doc.lines.all().delete()
        products = Product.objects.in_bulk([r["product"] for r in ready])
        for r in ready:
            product = products[r["product"]]
            if stock_in:
                # A row without pack columns takes the supplier's usual pack;
                # a row that gives only one of them keeps it.
                link_unit, link_size = default_pack(product, doc.supplier)
                if r["pack_size"] is None and r["pack_unit"] is None:
                    pack_unit_id = link_unit.pk if link_unit else None
                else:
                    pack_unit_id = r["pack_unit"]
                StockInLine(
                    document=doc,
                    product=product,
                    pack_unit_id=pack_unit_id,
                    packs=r["quantity"],
                    pack_size=r["pack_size"] or link_size,
                    pack_cost=r["unit_cost"],
                ).save()
            else:
                AdjustmentLine(
                    document=doc,
                    product=product,
                    quantity=r["quantity"],
                    unit_cost=r["unit_cost"] if opening else None,
                ).save()
            imported += 1

    return {
        "rows": results,
        "ready": len(ready),
        "to_fix": len(results) - len(ready),
        "imported": imported,
    }
