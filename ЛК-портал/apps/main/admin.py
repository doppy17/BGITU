from django.contrib import admin

from .models import Institute, News


@admin.register(Institute)
class InstituteAdmin(admin.ModelAdmin):
    list_display = ('short_name', 'name', 'dean')
    prepopulated_fields = {'slug': ('short_name',)}


@admin.register(News)
class NewsAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'is_pinned', 'is_published', 'published_at')
    list_filter = ('category', 'is_published', 'is_pinned')
    list_editable = ('is_pinned', 'is_published')
    prepopulated_fields = {'slug': ('title',)}
