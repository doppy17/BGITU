"""URL публичной части: строятся из реестра страниц."""
from django.urls import path

from .pages import PAGES
from .views import PageView

app_name = 'site'

urlpatterns = [
    path(page.slug + '/', PageView.as_view(), {'name': page.name}, name=page.name)
    if page.slug else path('', PageView.as_view(), {'name': page.name}, name=page.name)
    for page in PAGES
]
