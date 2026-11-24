from django.urls import path

from . import views
from .dev_keys import access_key_view

app_name = 'accounts'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    # временная страница одноразовых ключей разработчика (флаг DEV_ACCESS_KEYS)
    path('access/', access_key_view, name='dev_key'),
    path('theme/', views.theme_set, name='theme_set'),
    path('register/', views.register_view, name='register'),
    path('cabinet/profile/edit/', views.profile_edit, name='profile_edit'),
    path('cabinet/profile/contacts/', views.profile_contacts, name='profile_contacts'),
    # Контакты пользователя: безлимитное добавление, удаление и «основной».
    path('cabinet/profile/contacts/add/', views.contact_add, name='contact_add'),
    path('cabinet/profile/contacts/<int:pk>/delete/', views.contact_delete, name='contact_delete'),
    path('cabinet/profile/contacts/<int:pk>/primary/', views.contact_primary, name='contact_primary'),
    # Поля студенческого билета, которые студент заполняет сам (необязательно).
    path('cabinet/profile/ticket/', views.ticket_save, name='ticket_save'),
    path('cabinet/profile/security/', views.ProfileSecurityView.as_view(), name='profile_security'),
    path('logout/', views.logout_view, name='logout'),
    path('roles/', views.roles_view, name='roles'),
    path('roles/new/', views.role_create, name='role_create'),
    path('roles/<int:role_id>/request/', views.role_request_create, name='role_request_create'),
    path('role-requests/', views.role_requests_view, name='role_requests'),
    path('role-requests/<int:request_id>/<str:action>/', views.role_request_review, name='role_request_review'),
    path('manage/students/', views.manage_students, name='students'),
    path('manage/students/<int:user_id>/', views.manage_student_update, name='student_update'),
]
