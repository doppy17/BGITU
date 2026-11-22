"""Поисковый индекс для диалога поиска (сайт + кабинет)."""
from django.urls import reverse

from .pages import PAGES
from .search import ALL_ENTRIES
from .search_cabinet import CABINET_ENTRIES

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

CABINET_NAMES = {entry.url_name for entry in CABINET_ENTRIES}


SECTION_LABELS = {
    'university': 'Университет',
    'admission': 'Поступающим',
    'student': 'Студенту',
    'science': 'Наука',
    'news': 'Новости',
    'contacts': 'Контакты',
}


def search_index(request):
    """Единый список для клиентского поиска: разделы сайта и кабинета."""
    items = []
    for page in PAGES:
        items.append({
            'title': page.title,
            'category': SECTION_LABELS.get(page.section, 'Раздел'),
            'url': reverse(f'site:{page.name}'),
            'keywords': f'{page.description} {page.section}',
        })
    for entry in CABINET_ENTRIES:
        items.append({
            'title': entry.title,
            'category': 'Кабинет',
            'url': reverse(CABINET_URLS[entry.url_name]),
            'keywords': f'{entry.keywords} {entry.snippet}',
        })
    return {'search_index': items}
