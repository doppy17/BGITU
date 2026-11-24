from django.contrib import admin

from .models import Contact, Profile, Role, RoleRequest


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_developer', 'can_manage_roles', 'can_grade', 'is_system')
    search_fields = ('name', 'code')


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'group', 'record_book')
    list_filter = ('role', 'group', 'education_level', 'study_form')
    search_fields = ('user__username', 'user__email', 'user__last_name', 'record_book', 'ticket_number')
    fieldsets = (
        (None, {
            'fields': ('user', 'role', 'group', 'subgroup', 'theme'),
        }),
        ('Личные данные', {
            'fields': ('name_latin', 'citizenship', 'birth_date', 'phone', 'photo', 'scopus_id'),
        }),
        ('Учёба', {
            'fields': (
                'record_book', 'ticket_number', 'ticket_order', 'ticket_issue_date',
                'enrollment_year', 'education_level', 'study_form', 'department',
                'direction', 'plan_name', 'plan_code',
            ),
        }),
        ('Институт и курс', {
            'fields': ('institute_override', 'course_override'),
        }),
    )


@admin.register(RoleRequest)
class RoleRequestAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'status', 'created_at', 'reviewed_by')
    list_filter = ('status', 'role')


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('user', 'kind', 'value', 'label', 'is_primary', 'order', 'created_at')
    list_filter = ('kind', 'is_primary')
    search_fields = ('user__username', 'user__email', 'user__last_name', 'value', 'label')
    list_select_related = ('user',)
    ordering = ('user', 'order', 'id')
