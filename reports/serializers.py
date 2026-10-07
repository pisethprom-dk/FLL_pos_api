# v1.3.0 — the shape of each report, so the schema the Angular client is
# generated from matches what is sent. Money is a decimal string.
from rest_framework import serializers

from core.schema import money
from reports.services import PAID_BY, RECEIVABLE_FILTERS, STOCK_FILTERS, StockStatus
from sales.money import TENDER_KINDS

NO_COST = "Null unless the user may see cost."


def percent(**kw):
    return serializers.DecimalField(max_digits=7, decimal_places=1, **kw)


class DailySalesQuerySerializer(serializers.Serializer):
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    seller = serializers.IntegerField(required=False, min_value=1)


class DailySalesSummarySerializer(serializers.Serializer):
    sales = money()
    sales_khr = serializers.DecimalField(
        max_digits=18, decimal_places=0, help_text="Each sale at its own stamped rate."
    )
    invoices = serializers.IntegerField()
    average = money()
    discount = money()
    cost = money(allow_null=True, help_text=NO_COST)
    profit = money(allow_null=True, help_text=NO_COST)
    margin = percent(allow_null=True, help_text="Profit as a share of sales. " + NO_COST)
    returns_total = money(help_text="Returns posted in the period, shown apart from the sales.")
    returns_count = serializers.IntegerField()


class SalesDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    invoices = serializers.IntegerField()
    cash = money(help_text="What the sales came to less KHQR and credit: kept after change.")
    khqr = money()
    credit = money()
    sales = money()
    cost = money(allow_null=True, help_text=NO_COST)
    profit = money(allow_null=True, help_text=NO_COST)


class SalesSellerSerializer(serializers.Serializer):
    seller = serializers.IntegerField(allow_null=True)
    seller_name = serializers.CharField(allow_null=True)
    invoices = serializers.IntegerField()
    sales = money()
    average = money()
    discount = money()


class SalesTenderSerializer(serializers.Serializer):
    tender = serializers.ChoiceField(choices=TENDER_KINDS)
    amount = money()
    share = percent(allow_null=True, help_text="Its share of the sales; null when there were none.")


class DailySalesSerializer(serializers.Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    seller = serializers.IntegerField(allow_null=True, help_text="Whose sales; null for everyone's.")
    summary = DailySalesSummarySerializer()
    by_day = SalesDaySerializer(many=True, help_text="Newest day first; days without sales left out.")
    by_seller = SalesSellerSerializer(many=True)
    by_tender = SalesTenderSerializer(many=True)


class StockQuerySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=STOCK_FILTERS, required=False)
    category = serializers.IntegerField(required=False, min_value=1)
    brand = serializers.IntegerField(required=False, min_value=1)
    search = serializers.CharField(required=False, allow_blank=True)


class LastCountSerializer(serializers.Serializer):
    number = serializers.CharField()
    date = serializers.DateField()
    category = serializers.CharField()
    differences = serializers.IntegerField(help_text="Lines whose count differed.")
    value = money(allow_null=True, help_text="What the differences came to. " + NO_COST)


class StockSummarySerializer(serializers.Serializer):
    products = serializers.IntegerField()
    value = money(allow_null=True, help_text="All stock at average cost. " + NO_COST)
    below_reorder = serializers.IntegerField(help_text="At or below the reorder level, out of stock included.")
    out_of_stock = serializers.IntegerField()
    no_movement = serializers.IntegerField(help_text="Holding stock, nothing in or out for 90 days.")
    no_movement_value = money(allow_null=True, help_text=NO_COST)
    last_count = LastCountSerializer(allow_null=True)


class StockRowSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    brand_name = serializers.CharField(allow_null=True)
    unit_name = serializers.CharField()
    shelf_location = serializers.CharField()
    qty_on_hand = serializers.DecimalField(max_digits=12, decimal_places=2)
    reorder_level = serializers.DecimalField(max_digits=12, decimal_places=2)
    avg_cost = serializers.DecimalField(max_digits=12, decimal_places=4, allow_null=True, help_text=NO_COST)
    value = money(allow_null=True, help_text=NO_COST)
    last_moved = serializers.DateField(allow_null=True)
    status = serializers.ChoiceField(choices=StockStatus.choices)


class StockOnHandSerializer(serializers.Serializer):
    as_at = serializers.DateField()
    summary = StockSummarySerializer(help_text="All stock, whatever the filters.")
    rows = StockRowSerializer(many=True)
    rows_value = money(allow_null=True, help_text="The rows listed, at average cost. " + NO_COST)


class ReceivablesQuerySerializer(serializers.Serializer):
    show = serializers.ChoiceField(choices=RECEIVABLE_FILTERS, required=False)


class AgedSerializer(serializers.Serializer):
    """The four columns a balance is aged into, by the days since its invoice."""

    d0_30 = money()
    d31_60 = money()
    d61_90 = money()
    over_90 = money()
    owed = money()
    credit_limit = money()
    room_left = money(help_text="The limit less what is owed; below zero when over it.")


class ReceivableRowSerializer(AgedSerializer):
    customer = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    overdue = serializers.BooleanField(help_text="An open invoice is past its due date.")
    over_limit = serializers.BooleanField()
    on_hold = serializers.BooleanField()


class ReceivablesSummarySerializer(serializers.Serializer):
    owed = money()
    owed_khr = serializers.DecimalField(
        max_digits=18, decimal_places=0, allow_null=True,
        help_text="At today's rate; null when there is none.",
    )
    rate = serializers.DecimalField(max_digits=12, decimal_places=6, allow_null=True)
    past_60 = money(help_text="Owed on invoices more than 60 days old.")
    past_60_share = percent(allow_null=True)
    collected_this_month = money(help_text="Posted payments dated this month.")
    flagged = serializers.ListField(
        child=serializers.CharField(), help_text="Customers over their limit or on credit hold."
    )


class ReceivablesSerializer(serializers.Serializer):
    as_at = serializers.DateField()
    summary = ReceivablesSummarySerializer(help_text="Everyone who owes, whatever the filter.")
    rows = ReceivableRowSerializer(many=True, help_text="Most owed first.")
    totals = AgedSerializer(help_text="The rows listed, added up.")


class DashboardTodaySerializer(serializers.Serializer):
    sales = money()
    sales_khr = serializers.DecimalField(
        max_digits=18, decimal_places=0, help_text="Each sale at its own stamped rate."
    )
    invoices = serializers.IntegerField()
    average = money()
    profit = money(allow_null=True, help_text=NO_COST)
    margin = percent(allow_null=True, help_text="Profit as a share of sales. " + NO_COST)


class DashboardDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    sales = money()
    share = percent(help_text="Its share of the week's best day, for the bar's height.")


class CategorySalesSerializer(serializers.Serializer):
    category = serializers.IntegerField(allow_null=True, help_text="A main category; null for Other.")
    name = serializers.CharField()
    sales = money()
    share = percent(allow_null=True, help_text="Its share of the day's sales.")


class RecentSaleSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    number = serializers.CharField()
    sale_date = serializers.DateTimeField()
    customer_name = serializers.CharField()
    walk_in_name = serializers.CharField()
    paid_by = serializers.ChoiceField(choices=PAID_BY, help_text="MIXED when settled more than one way.")
    total = money()


class DashboardSalesSerializer(serializers.Serializer):
    seller = serializers.IntegerField(allow_null=True, help_text="Whose sales; null for the whole shop.")
    today = DashboardTodaySerializer()
    last_7_days = DashboardDaySerializer(many=True, help_text="Oldest first; a day without sales is 0.00.")
    by_category = CategorySalesSerializer(many=True, help_text="Today; the biggest four, then Other.")
    recent = RecentSaleSerializer(many=True, help_text="The last five completed sales, newest first.")


class CashTodaySerializer(serializers.Serializer):
    sales = money(help_text="Cash kept from today's sales, after change.")
    payments = money(help_text="Cash customer payments posted today.")
    refunds = money(help_text="Cash refunds posted today.")
    total = money()


class LowStockSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    unit_name = serializers.CharField()
    qty_on_hand = serializers.DecimalField(max_digits=12, decimal_places=2)
    reorder_level = serializers.DecimalField(max_digits=12, decimal_places=2)
    status = serializers.ChoiceField(choices=StockStatus.choices)


class DashboardStockSerializer(serializers.Serializer):
    products = serializers.IntegerField()
    value = money(allow_null=True, help_text="All stock at average cost. " + NO_COST)
    below_reorder = serializers.IntegerField(help_text="At or below the reorder level, out of stock included.")
    out_of_stock = serializers.IntegerField()
    running_low = LowStockSerializer(many=True, help_text="Out of stock first, then by code.")
    more = serializers.IntegerField(help_text="Below reorder but not listed.")


class DashboardOwedSerializer(serializers.Serializer):
    owed = money()
    over_90 = money()
    rows = ReceivableRowSerializer(many=True, help_text="The customers who owe most.")
    totals = AgedSerializer(help_text="Everyone who owes.")
    more = serializers.IntegerField(help_text="Customers who owe but are not listed.")


class QuotationsDueSerializer(serializers.Serializer):
    sent = serializers.IntegerField(help_text="Sent and not yet answered.")
    expiring = serializers.IntegerField(help_text="Of those, valid for 7 days or less.")
    expired = serializers.IntegerField(help_text="Of those, past their valid-until date.")


class WarrantyOpenSerializer(serializers.Serializer):
    open = serializers.IntegerField(help_text="Received, sent for repair or ready for collection.")
    out_of_warranty = serializers.IntegerField(help_text="Of those, claimed after the warranty ended.")


class DashboardSerializer(serializers.Serializer):
    """Each part is null when the user may not see it."""

    as_at = serializers.DateField()
    sales = DashboardSalesSerializer(allow_null=True, help_text="report.sales.all, or .own for their own.")
    cash_today = CashTodaySerializer(allow_null=True, help_text="report.sales.all. Not a drawer count.")
    stock = DashboardStockSerializer(allow_null=True, help_text="report.stock.")
    owed = DashboardOwedSerializer(allow_null=True, help_text="report.receivables.")
    held_sales = serializers.IntegerField(allow_null=True, help_text="sell. Every till's.")
    quotations = QuotationsDueSerializer(allow_null=True, help_text="quotation.view.")
    warranty = WarrantyOpenSerializer(allow_null=True, help_text="warranty.view.")
