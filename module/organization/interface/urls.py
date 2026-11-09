from rest_framework.routers import DefaultRouter

from module.organization.interface.views import OrganizationViewSet

router = DefaultRouter()
router.register('organizations', OrganizationViewSet, basename='organization')
urlpatterns = router.urls
