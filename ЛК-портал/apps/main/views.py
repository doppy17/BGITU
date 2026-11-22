"""Публичные страницы БГИТУ: главная, новости, институты, приём."""
from django.shortcuts import get_object_or_404, render
from django.views.generic import ListView

from .models import Institute, News


class IndexView(ListView):
    model = News
    template_name = 'main/index.html'
    context_object_name = 'news_list'

    def get_queryset(self):
        return News.objects.filter(is_published=True)[:5]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['institutes'] = Institute.objects.all()[:3]
        return context


class NewsListView(ListView):
    model = News
    template_name = 'main/news_list.html'
    context_object_name = 'news_list'
    paginate_by = 12

    def get_queryset(self):
        return News.objects.filter(is_published=True)


def news_detail(request, slug):
    item = get_object_or_404(News, slug=slug, is_published=True)
    return render(request, 'main/news_detail.html', {'news': item})


def institute_detail(request, slug):
    institute = get_object_or_404(Institute, slug=slug)
    return render(request, 'main/institute_detail.html', {'institute': institute})


def admission_view(request):
    return render(request, 'main/admission.html')
