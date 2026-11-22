"""Публичная часть сайта БГИТУ.

Одна вьюха обслуживает все страницы: контент лежит в шаблонах
(``site/pages/<name>.html``), метаданные — в реестре :mod:`apps.site.pages`.
"""
from django.http import Http404
from django.views.generic import TemplateView

from .pages import PAGES, PAGES_BY_NAME, Page


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
