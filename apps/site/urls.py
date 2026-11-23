"""URL публичной части: реестр страниц + поиск."""
from django.urls import path

from .pages import PAGES
from .suggest import suggest
from .views import PageView, SearchView

app_name = 'site'

urlpatterns = [
    path('search/', SearchView.as_view(), name='search'),
    path('api/search/suggest/', suggest, name='search_suggest'),
]

urlpatterns += [
    path(page.slug + '/', PageView.as_view(), {'name': page.name}, name=page.name)
    if page.slug else path('', PageView.as_view(), {'name': page.name}, name=page.name)
    for page in PAGES
]
