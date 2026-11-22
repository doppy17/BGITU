"""Публичная часть сайта БГИТУ.

Одна вьюха обслуживает все страницы: контент лежит в шаблонах
(``site/pages/<name>.html``), метаданные — в реестре :mod:`apps.site.pages`.
"""
from django.http import Http404
from django.urls import reverse
from django.views.generic import TemplateView

from .pages import PAGES, PAGES_BY_NAME, Page
from .search import search

# Имена кабинетных разделов → маршруты Django
CABINET_URLS = {
    'cabinet': 'portal:dashboard',
    'schedule': 'schedule:schedule',
    'plan': 'schedule:schedule',
    'grades': 'portal:grades',
    'stipendia': 'portal:stipendia',
    'courses': 'education:courses',
    'works': 'education:works',
    'roles': 'accounts:roles',
    'profile': 'portal:profile',
    'students': 'accounts:students',
}


class PageView(TemplateView):
    """Страница из реестра: шаблон, заголовок и раздел меню берутся из Page."""

    page: Page

    def dispatch(self, request, *args, **kwargs):
        name = kwargs.get('name')
        page = PAGES_BY_NAME.get(name)
        if page is None:
            raise Http404(f'Страница {name!r} не найдена')
        self.page = page
        return super().dispatch(request, *args, **kwargs)

    def get_template_names(self) -> list[str]:
        return [self.page.template]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page'] = self.page
        context['pages'] = PAGES
        return context


class SearchView(TemplateView):
    """Поиск по страницам сайта и разделам кабинета."""

    template_name = 'site/pages/search.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = (self.request.GET.get('q') or '').strip()
        results = []
        for entry, _score in search(query):
            url = (
                reverse(f'site:{entry.url_name}')
                if entry.url_name in PAGES_BY_NAME
                else reverse(CABINET_URLS[entry.url_name])
            )
            results.append({'entry': entry, 'url': url})
        context.update({'query': query, 'results': results, 'page': None})
        return context
