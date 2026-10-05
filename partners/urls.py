# v1.0.0 — /api/partners/
from rest_framework.routers import DefaultRouter

from partners.views import CustomerViewSet, ProductSupplierViewSet, SupplierViewSet

router = DefaultRouter()
router.register("customers", CustomerViewSet, basename="customer")
router.register("suppliers", SupplierViewSet, basename="supplier")
router.register("product-suppliers", ProductSupplierViewSet, basename="product-supplier")

urlpatterns = router.urls
