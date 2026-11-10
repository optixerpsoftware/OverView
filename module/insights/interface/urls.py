from django.urls import path

from module.insights.interface import views as v

urlpatterns = [
    path('insights/dashboard/', v.DashboardView.as_view()),
    path('insights/alerts/', v.AlertListView.as_view()),
    path('insights/alerts/counts/', v.AlertCountsView.as_view()),
    path('insights/alerts/status/', v.AlertStatusView.as_view()),
    path('insights/alerts/evaluate/', v.AlertEvaluateView.as_view()),
    path('insights/organizations/<uuid:org_id>/notifications/', v.NotificationSettingView.as_view()),
    path('insights/search/', v.SearchView.as_view()),
    path('insights/queue/', v.QueueView.as_view()),
    path('insights/queue/assign/', v.QueueAssignView.as_view()),
    path('insights/queue/snooze/', v.QueueSnoozeView.as_view()),
    path('insights/alerts/<uuid:alert_id>/note/', v.QueueNoteView.as_view()),
    path('insights/organizations/<uuid:org_id>/report/', v.OrganizationReportView.as_view()),
    path('insights/bulk/rules/delete/', v.BulkRulesDeleteView.as_view()),
    path('insights/bulk/sharing/revoke/', v.BulkSharingRevokeView.as_view()),
    path('insights/bulk/log/', v.BulkLogView.as_view()),
]
