# v1.0.0 — /api/catalogue/
from rest_framework.routers import DefaultRouter

from catalogue.views import BrandViewSet, CategoryViewSet, ProductViewSet, UnitViewSet

router = DefaultRouter()
router.register("categories", CategoryViewSet, basename="category")
router.register("brands", BrandViewSet, basename="brand")
router.register("units", UnitViewSet, basename="unit")
router.register("products", ProductViewSet, basename="product")

urlpatterns = router.urls
