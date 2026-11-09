from django.urls import path

from module.microsoft_compliance.interface.views import HistoryView, LatestView, ScanView

urlpatterns = [
    path('compliance/connectors/<uuid:connector_id>/latest/', LatestView.as_view()),
    path('compliance/connectors/<uuid:connector_id>/history/', HistoryView.as_view()),
    path('compliance/connectors/<uuid:connector_id>/scan/', ScanView.as_view()),
]
