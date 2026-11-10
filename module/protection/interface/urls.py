from django.urls import path

from module.protection.interface import views as v

urlpatterns = [
    path('protection/organizations/<uuid:org_id>/', v.PolicyView.as_view()),
    path('protection/organizations/<uuid:org_id>/evaluate/', v.EvaluateView.as_view()),
    path('protection/events/<uuid:event_id>/reenable/', v.ReenableView.as_view()),
]
