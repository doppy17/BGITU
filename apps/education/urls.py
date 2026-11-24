from django.urls import path

from . import plan_views, views

app_name = 'education'

urlpatterns = [
    path('courses/', views.course_list, name='courses'),
    path('courses/new/', views.course_create, name='course_create'),
    path('courses/<int:course_id>/', views.course_detail, name='course_detail'),
    path('courses/<int:course_id>/journal/', views.course_journal, name='journal'),
    path('courses/<int:course_id>/grade/', views.set_grade, name='set_grade'),
    path('courses/<int:course_id>/assignment/', views.assignment_create, name='assignment_create'),
    path('courses/<int:course_id>/attendance/', views.attendance_view, name='attendance'),
    path('courses/<int:course_id>/points/', views.course_points, name='course_points'),
    path('courses/<int:course_id>/points/mark/', views.set_point_marks, name='set_point_marks'),
    path('courses/<int:course_id>/points/close/', views.close_point, name='close_point'),
    path('assignments/<int:assignment_id>/submit/', views.submit_work, name='submit_work'),
    path('submissions/<int:submission_id>/review/', views.review_submission, name='review_submission'),
    path('works/', views.my_works, name='works'),
    path('plan/import/', plan_views.plan_import, name='plan_import'),
    path('plan/source/<int:pk>/delete/', plan_views.plan_source_delete, name='plan_source_delete'),
    path('plan/source/<int:pk>/file/', plan_views.plan_source_download, name='plan_source_file'),
]
