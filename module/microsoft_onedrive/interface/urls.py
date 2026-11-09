from django.urls import path

from module.microsoft_onedrive.interface import views

_c = 'onedrive/connectors/<uuid:connector_id>'

urlpatterns = [
    path(f'{_c}/drives/', views.DrivesView.as_view()),
    path(f'{_c}/drives/<str:drive_id>/items/', views.ItemsView.as_view()),
    path(f'{_c}/drives/<str:drive_id>/items/<str:item_id>/permissions/', views.PermissionsView.as_view()),
    path(f'{_c}/drives/<str:drive_id>/items/<str:item_id>/permissions/<str:permission_id>/',
         views.RevokeView.as_view()),
    path(f'{_c}/sharing/latest/', views.LatestScanView.as_view()),
    path(f'{_c}/sharing/scan/', views.StartScanView.as_view()),
    path(f'{_c}/sharing/history/', views.ScanHistoryView.as_view()),
]
