from django.urls import path

from . import ai_views, views

app_name = 'schedule'

urlpatterns = [
    path('schedule/', views.schedule_view, name='schedule'),
    path('schedule/fragment/', views.schedule_fragment, name='fragment'),
    path('schedule/import/', views.import_view, name='import'),
    path('schedule/import/ai-review/<int:pk>/', ai_views.ai_review_view, name='ai_review'),
    path('schedule/import/server/', views.import_from_server_file, name='import_server'),
    path('schedule/source/<int:pk>/delete/', views.delete_source, name='source_delete'),
    path('schedule/source/<int:pk>/file/', views.download_source, name='source_file'),
]
