from django.contrib import admin

from .models import Bell, Lesson, ScheduleImport


@admin.register(Bell)
class BellAdmin(admin.ModelAdmin):
    list_display = ('building', 'pair', 'start', 'end')


@admin.register(ScheduleImport)
class ScheduleImportAdmin(admin.ModelAdmin):
    list_display = ('original_name', 'institute', 'imported_at', 'size', 'lessons_count', 'groups_count', 'imported_by')
    readonly_fields = ('lessons_count', 'groups_count')


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ('group', 'weekday', 'pair', 'subject', 'room', 'building', 'teacher_name', 'subgroup', 'week')
    list_filter = ('group', 'weekday', 'week', 'building')
    search_fields = ('subject', 'teacher_name', 'room')
