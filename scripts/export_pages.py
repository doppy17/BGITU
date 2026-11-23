#!/usr/bin/env python3
"""Статический экспорт публичной части сайта БГИТУ.

Django-сервер на GitHub Pages работать не может, поэтому скрипт рендерит
публичные страницы через ``django.test.Client`` и складывает готовый HTML в
``dist/``. Все абсолютные внутренние ссылки и ссылки на статику переписываются
в относительные, чтобы сайт работал и из корня домена, и из подпапки
(``/BGITU/`` на GitHub Pages).

Запуск из корня проекта::

    DJANGO_SETTINGS_MODULE=config.settings.dev PYTHONPATH=. \
        python scripts/export_pages.py

Каталог ``dist/`` — генерируемый, в git не попадает (см. ``.gitignore``).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')

import django  # noqa: E402
from django.conf import settings  # noqa: E402

# Экспорт публичной части не должен зависеть от локальной БД и её данных:
# в CI файла db.sqlite3 нет. SQLite подменяем на in-memory — если какая-то
# страница неожиданно начнёт ходить в БД, сборка упадёт явно, а не молча
# возьмёт локальные данные.
if settings.DATABASES['default']['ENGINE'].endswith('sqlite3'):
    settings.DATABASES['default']['NAME'] = ':memory:'
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

django.setup()

from django.test import Client  # noqa: E402

from apps.site.pages import PAGES  # noqa: E402

DIST = ROOT / 'dist'
STATIC_SRC = Path(settings.STATICFILES_DIRS[0])
STATIC_URL = settings.STATIC_URL
MEDIA_URL = settings.MEDIA_URL

# Страницы, которые действительно попадают в экспорт: главная + реестр.
PAGE_PATHS = {'/'}
for page in PAGES:
    if not page.slug:
        continue
    path = f'/{page.slug}/'
    if path in PAGE_PATHS:
        raise SystemExit(f'Дублирующийся slug в реестре: {page.slug!r}')
    PAGE_PATHS.add(path)

# Разделы кабинета: в статический экспорт не входят. Ссылки на них ведут на
# страницу-пояснение, чтобы на GitHub Pages не было битых адресов.
UNEXPORTED_PREFIXES = (
    '/cabinet', '/login', '/register', '/logout', '/roles', '/role-requests',
    '/manage', '/courses', '/works', '/theme', '/admin', '/media',
)

# Служебный адрес страницы-пояснения (не страница реестра).
OFFLINE_DIR = 'offline'

# Куда вести служебные ссылки, у которых есть публичный аналог.
PATH_ALIASES = {
    '/schedule': 'timetable/',
    '/schedule/': 'timetable/',
}

URL_ATTR_RE = re.compile(
    r'(?P<prefix>\b(?:href|src|action|poster|data-href|data-src)\s*=\s*)'
    r'(?P<quote>["\'])(?P<url>.*?)(?P=quote)',
    re.IGNORECASE | re.DOTALL,
)
SRCSET_RE = re.compile(r'(\bsrcset\s*=\s*)(["\'])(.*?)\2', re.IGNORECASE | re.DOTALL)
CSS_URL_RE = re.compile(r'url\(\s*(["\']?)(/[^)"\']+)\1\s*\)')
JSON_INDEX_RE = re.compile(
    r'(<script[^>]*\bid=["\']bgitu-index["\'][^>]*>)(.*?)(</script>)',
    re.IGNORECASE | re.DOTALL,
)
SCHEME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*:')

# Сюда складываются внутренние адреса, которых нет в реестре и которые не
# являются известными разделами кабинета. Такие ссылки нельзя молча прятать:
# это либо опечатка в шаблоне, либо новый маршрут, который забыли экспортировать.
unknown_internal: set[str] = set()


def map_internal_path(path: str) -> str | None:
    """Абсолютный путь Django → путь относительно корня dist (или None).

    ``None`` означает «в экспорт не входит»: для HTML такая ссылка ведёт на
    страницу-пояснение, из поискового индекса запись выбрасывается.
    """
    if path == '/':
        return ''
    if path.startswith(STATIC_URL):
        return path.lstrip('/')
    if path in PATH_ALIASES:
        return PATH_ALIASES[path]
    if path in PAGE_PATHS:
        return path.lstrip('/')
    if path.rstrip('/') + '/' in PAGE_PATHS:
        return path.rstrip('/').lstrip('/') + '/'
    if path.startswith(UNEXPORTED_PREFIXES):
        return None
    if path.startswith('/search'):
        return None
    unknown_internal.add(path)
    return None


def relative_to_root(rel: str, depth: int) -> str:
    """Путь от корня dist → путь со страницы на глубине depth."""
    if depth == 0:
        return rel or './'
    return f'../{rel}' if rel else '../'


def normalize_url(value: str, depth: int) -> str:
    """Переписать один абсолютный внутренний URL в относительный."""
    value = value.strip()
    if not value or value.startswith(('#', '?', '//')):
        return value
    if SCHEME_RE.match(value):
        return value
    if not value.startswith('/'):
        return value

    parts = urlsplit(value)
    suffix = ''
    if parts.query:
        suffix += '?' + parts.query
    if parts.fragment:
        suffix += '#' + parts.fragment

    rel = map_internal_path(parts.path)
    if rel is None:
        rel = OFFLINE_DIR + '/'
    return relative_to_root(rel, depth) + suffix


def normalize_srcset(value: str, depth: int) -> str:
    """srcset — список «URL [дескриптор], URL [дескриптор]»."""
    items = []
    for chunk in value.split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        pieces = chunk.split()
        pieces[0] = normalize_url(pieces[0], depth)
        items.append(' '.join(pieces))
    return ', '.join(items)


def rewrite_search_index(html: str, depth: int) -> str:
    """Относительные URL в JSON-индексе поиска + только публичные страницы."""
    match = JSON_INDEX_RE.search(html)
    if not match:
        return html
    try:
        items = json.loads(match.group(2))
    except json.JSONDecodeError:
        return html

    public_items = []
    for item in items:
        if not isinstance(item, dict) or item.get('category') == 'Кабинет':
            continue
        url = item.get('url')
        if not isinstance(url, str) or not url.startswith('/'):
            continue
        path = urlsplit(url).path
        if map_internal_path(path) is None:
            continue
        item['url'] = normalize_url(url, depth)
        public_items.append(item)

    payload = json.dumps(public_items, ensure_ascii=False)
    payload = payload.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return html[:match.start(2)] + payload + html[match.end(2):]


def normalize_html(html: str, depth: int) -> str:
    """Все абсолютные внутренние ссылки и ассеты → относительные."""
    def attr_repl(match: re.Match) -> str:
        return f'{match.group("prefix")}{match.group("quote")}{normalize_url(match.group("url"), depth)}{match.group("quote")}'

    html = URL_ATTR_RE.sub(attr_repl, html)
    html = SRCSET_RE.sub(
        lambda m: f'{m.group(1)}{m.group(2)}{normalize_srcset(m.group(3), depth)}{m.group(2)}',
        html,
    )
    html = CSS_URL_RE.sub(
        lambda m: f'url({m.group(1)}{normalize_url(m.group(2), depth)}{m.group(1)})',
        html,
    )
    return rewrite_search_index(html, depth)


def render(path: str, client: Client) -> str:
    response = client.get(path)
    if response.status_code != 200:
        raise SystemExit(f'{path}: ожидался 200, получен {response.status_code}')
    return response.content.decode('utf-8')


def write_page(rel_path: str, html: str) -> Path:
    target = DIST / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding='utf-8')
    return target


NOTICE_MAIN = '''<main id="main">
  <section class="page-hero" aria-labelledby="offline-h">
    <div class="container">
      <p class="tag mono"><i></i>Статическая версия</p>
      <h1 id="offline-h">Раздел доступен в полной версии</h1>
      <p class="page-hero__lead">Личный кабинет и служебные разделы работают только на серверной версии сайта. Здесь опубликована публичная часть.</p>
      <p><a class="btn btn--accent" href="../">На главную</a></p>
    </div>
  </section>
</main>'''


def make_notice_page(index_html: str) -> str:
    """Страница-пояснение для ссылок на неэкспортируемые разделы кабинета."""
    html = re.sub(r'<title>.*?</title>', '<title>Раздел доступен в полной версии — БГИТУ</title>',
                  index_html, count=1, flags=re.DOTALL)
    html, count = re.subn(r'<main id="main">.*?</main>', NOTICE_MAIN, html, count=1, flags=re.DOTALL)
    if count != 1:
        raise SystemExit('Не удалось собрать страницу-пояснение: не найден <main id="main">')
    return html


def copy_static() -> None:
    """static/ → dist/static/ без мусора."""
    if not STATIC_SRC.is_dir():
        raise SystemExit(f'Не найдена папка статики: {STATIC_SRC}')
    shutil.copytree(
        STATIC_SRC,
        DIST / 'static',
        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', '.DS_Store'),
    )
    patch_manifest()


def patch_manifest() -> None:
    """Манифест PWA лежит в static/site/, поэтому его URL считаются от него.

    В архиве start_url/shortcuts указывали на *.html, которых в Django-версии
    нет. Переписываем на реальные адреса статического экспорта.
    """
    manifest = DIST / 'static' / 'site' / 'manifest.webmanifest'
    if not manifest.exists():
        return
    try:
        data = json.loads(manifest.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    data['start_url'] = '../../'
    data['scope'] = '../../'
    data['shortcuts'] = [
        {'name': 'Расписание', 'url': '../../timetable/'},
        {'name': 'Поступающим', 'url': '../../admission/'},
    ]
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


def extract_urls(html: str) -> list[str]:
    urls = [m.group('url') for m in URL_ATTR_RE.finditer(html)]
    for match in SRCSET_RE.finditer(html):
        urls.extend(chunk.strip().split()[0] for chunk in match.group(3).split(',') if chunk.strip())
    urls.extend(m.group(2) for m in CSS_URL_RE.finditer(html))
    return urls


def check_export() -> list[str]:
    """Самопроверка: все ссылки ведут на существующие файлы, нет абсолютных путей."""
    problems: list[str] = []

    expected = {DIST / 'index.html'}
    expected |= {DIST / page.slug / 'index.html' for page in PAGES if page.slug}
    for path in sorted(expected):
        if not path.is_file():
            problems.append(f'нет страницы: {path.relative_to(DIST)}')
    if not (DIST / '.nojekyll').is_file():
        problems.append('нет dist/.nojekyll')

    for html_path in sorted(DIST.rglob('*.html')):
        text = html_path.read_text(encoding='utf-8')
        if '127.0.0.1' in text:
            problems.append(f'{html_path.relative_to(DIST)}: найден 127.0.0.1')
        for absolute in ('href="/static/', 'src="/static/', 'url(/static/'):
            if absolute in text:
                problems.append(f'{html_path.relative_to(DIST)}: абсолютный {absolute}')
        if re.search(r'(?:href|src|action)="/(?!/)', text):
            problems.append(f'{html_path.relative_to(DIST)}: остался абсолютный внутренний URL')

        for url in extract_urls(text):
            if not url or url.startswith(('#', '?', '//')) or SCHEME_RE.match(url):
                continue
            if url.startswith('/'):
                problems.append(f'{html_path.relative_to(DIST)}: абсолютная ссылка {url}')
                continue
            clean = url.split('#', 1)[0].split('?', 1)[0]
            if not clean:
                continue
            target = html_path.parent / clean
            if target.is_dir():
                target = target / 'index.html'
            if not target.exists():
                problems.append(f'{html_path.relative_to(DIST)}: битая ссылка {url}')

    return problems


def human_size(size: int) -> str:
    value = float(size)
    for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
        if value < 1024 or unit == 'ГБ':
            return f'{value:.1f} {unit}'
        value /= 1024
    return f'{size} Б'


def main() -> int:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    client = Client()
    index_raw = render('/', client)
    write_page('index.html', normalize_html(index_raw, 0))

    for page in PAGES:
        if not page.slug:
            continue
        raw = render(f'/{page.slug}/', client)
        write_page(f'{page.slug}/index.html', normalize_html(raw, 1))
        print(f'  /{page.slug}/')

    notice_raw = make_notice_page(index_raw)
    write_page(f'{OFFLINE_DIR}/index.html', normalize_html(notice_raw, 1))

    copy_static()
    (DIST / '.nojekyll').write_text('', encoding='utf-8')

    if unknown_internal:
        raise SystemExit('Неизвестные внутренние маршруты (добавьте в экспорт или в UNEXPORTED_PREFIXES): '
                         + ', '.join(sorted(unknown_internal)))

    problems = check_export()
    if problems:
        print('\nОшибки экспорта:', file=sys.stderr)
        for problem in problems:
            print('  - ' + problem, file=sys.stderr)
        return 1

    html_count = len(list(DIST.rglob('*.html')))
    total_size = sum(path.stat().st_size for path in DIST.rglob('*') if path.is_file())
    print(f'\nГотово: {html_count} HTML-страниц, {human_size(total_size)}, {DIST}')
    print('Проверка ссылок и ассетов пройдена.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
