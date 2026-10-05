# v1.0.0 — /api/inventory/
from rest_framework.routers import DefaultRouter

from inventory.views import (
    AdjustmentViewSet,
    StockCountViewSet,
    StockInViewSet,
    StockMovementViewSet,
)

router = DefaultRouter()
router.register("stock-ins", StockInViewSet, basename="stock-in")
router.register("adjustments", AdjustmentViewSet, basename="adjustment")
router.register("counts", StockCountViewSet, basename="count")
router.register("movements", StockMovementViewSet, basename="movement")

urlpatterns = router.urls
