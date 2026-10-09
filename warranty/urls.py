# v1.0.0 — /api/warranty/
from rest_framework.routers import DefaultRouter

from warranty.views import WarrantyClaimViewSet

router = DefaultRouter()
router.register("claims", WarrantyClaimViewSet, basename="warranty-claim")

urlpatterns = router.urls
