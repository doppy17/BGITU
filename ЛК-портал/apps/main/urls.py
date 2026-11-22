from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = 'main'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('news/', views.NewsListView.as_view(), name='news_list'),
    path('news/<slug:slug>/', views.news_detail, name='news_detail'),
    path('institute/<slug:slug>/', views.institute_detail, name='institute_detail'),
    path('admission/', views.admission_view, name='admission'),
    # расписание живёт в кабинете — публичная ссылка ведёт туда
    path(
        'timetable/',
        RedirectView.as_view(pattern_name='schedule:schedule', permanent=False),
        name='schedule',
    ),
]
