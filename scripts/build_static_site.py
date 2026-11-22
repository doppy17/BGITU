#!/usr/bin/env python3
"""Сборка публичной части сайта БГИТУ из статического архива в Django-приложение.

Источник: папка с готовыми .html из архива (по умолчанию ~/Downloads/bgitu-site-main).
Результат:
  apps/site/pages.py                     — реестр страниц (dataclass Page)
  apps/site/templates/site/base.html     — общая оболочка (шапка, подвал, оверлеи)
  apps/site/templates/site/pages/*.html  — содержимое страниц ({% block content %})
  static/site/css/site.css               — общий CSS
  static/site/js/*.js                    — разбитые по смыслу скрипты
  static/site/manifest.webmanifest, sw.js, icon-*.png

Запуск:  python scripts/build_static_site.py [путь-к-архиву]
Скрипт идемпотентен: пересобирает файлы целиком.
"""
from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Мобильная шапка — общая для сайта и кабинета: бургер вместо меню разделов.
# На сайте бургер открывает меню разделов, в кабинете — рельс (app.js).
MOBILE_HEAD_CSS = """
@media (max-width: 1180px) {
  .nav, .head-actions .btn { display: none; }
  .burger { display: block; }
  .head-actions { margin-left: auto; }
}
"""
SOURCE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / 'Downloads' / 'bgitu-site-main'

# slug в URL отличается от имени файла там, где путь занят ЛК
SLUGS = {
    'index': '',
    'schedule': 'timetable',   # /schedule/ занят кабинетом
    'lk': None,                # страница входа не нужна: ведём в наш ЛК
}

# Старые адреса bgitu.ru → наши страницы (сайт заменяет старый)
LINK_MAP = [
    (r'https://bgitu\.ru/studentu/raspisanie/[^"]*', 'CABINET_SCHEDULE'),
    (r'https://bgitu\.ru/studentu/stipendii[^"]*', 'scholarships'),
    (r'https://bgitu\.ru/studentu/eos[^"]*', 'eos'),
    (r'https://bgitu\.ru/studentu/obshchezhitie[^"]*', 'life'),
    (r'https://bgitu\.ru/studentu[^"]*', 'student'),
    (r'https://bgitu\.ru/obyavl[^"]*', 'news'),
    (r'https://bgitu\.ru/news[^"]*', 'news'),
    (r'https://bgitu\.ru/abitur[^"]*', 'admission'),
    (r'https://bgitu\.ru/universitet/kontakty/telefonnyy-spravochnik[^"]*', 'contacts'),
    (r'https://bgitu\.ru/universitet/kadrovyy-sostav[^"]*', 'staff'),
    (r'https://bgitu\.ru/universitet/voennyy-uchebnyy-tsentr[^"]*', 'inst-vuc'),
    (r'https://bgitu\.ru/sveden/document[^"]*', 'sveden'),
    (r'https://bgitu\.ru/sveden[^"]*', 'sveden'),
    (r'https://eos\.bgitu\.ru/Account/[^"]*', 'eos'),
    (r'https://eos\.bgitu\.ru[^"]*', 'eos'),
    (r'https://bgitu\.ru/', 'index'),
]

# Какая страница к какому пункту верхнего меню относится (для aria-current)
SECTIONS = {
    'university': 'university', 'institutes': 'university', 'inst-econ': 'university',
    'inst-fop': 'university', 'inst-les': 'university', 'inst-stroy': 'university',
    'inst-vuc': 'university', 'college': 'university', 'campus': 'university',
    'garden': 'university', 'sveden': 'university', 'docs': 'university',
    'press': 'university', 'staff': 'university',
    'admission': 'admission', 'programs': 'admission', 'program': 'admission',
    'target': 'admission', 'school': 'admission', 'transfer': 'admission',
    'paid': 'admission', 'international': 'admission',
    'student': 'student', 'schedule': 'student', 'eos': 'student',
    'scholarships': 'student', 'library': 'student', 'life': 'student',
    'career': 'student', 'dpo': 'student', 'help': 'student',
    'science': 'science', 'aspirantura': 'science',
    'news': 'news', 'article': 'news',
    'contacts': 'contacts', 'sitemap': 'contacts', 'privacy': 'contacts',
}

# Скрипты: имя файла → фрагмент из общего блока <script>
SCRIPTS = [
    ('mode.js', None),        # html.js — режим «js включён»
    ('theme.js', 'тема до первой отрисовки'),
    ('reveal.js', 'Плавное появление текста'),
    ('core.js', 'БГИТУ — лендинг. Чистый JS'),
    ('trail.js', 'Фон «След»'),
    ('secbar.js', 'Липкая плашка раздела'),
    ('week.js', 'Индикатор учебной недели'),
]


@dataclass(frozen=True)
class Page:
    """Страница публичной части: имя файла, slug, заголовок, раздел навигации."""

    name: str
    slug: str
    title: str
    section: str
    description: str = ''

    @property
    def template(self) -> str:
        return f'site/pages/{self.name}.html'


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='ignore')


def extract_style(html: str) -> str:
    found = re.search(r'<style[^>]*>(.*?)</style>', html, re.S)
    if not found:
        raise SystemExit('В странице нет общего <style>')
    return found.group(1).strip()


def extract_scripts(html: str) -> list[str]:
    return [m.strip() for m in re.findall(r'<script(?![^>]*src)[^>]*>(.*?)</script>', html, re.S)]


def split_tail_script(script: str) -> dict[str, str]:
    """Разбирает общий хвостовой <script> на смысловые блоки по комментариям."""
    blocks: dict[str, str] = {}
    markers = {
        'week': 'Индикатор учебной недели',
        'a11y': 'Версия для слабовидящих',
        'cookies': 'Cookies / ПДн',
        'pwa': 'PWA:',
    }
    positions = []
    for key, marker in markers.items():
        index = script.find(marker)
        if index != -1:
            positions.append((index, key))
    positions.sort()
    for order, (index, key) in enumerate(positions):
        end = positions[order + 1][0] if order + 1 < len(positions) else len(script)
        blocks[key] = script[index:end].strip()
    return blocks


def rewrite_links(fragment: str, pages: dict[str, Page]) -> str:
    """Внутренние ссылки и ассеты → теги Django (url/static)."""

    def href(match: re.Match) -> str:
        target = match.group(1)
        page_name, _, anchor = target.partition('#')
        if page_name == 'CABINET_SCHEDULE':
            return 'href="{% url \'schedule:schedule\' %}"'
        page = pages.get(page_name.removesuffix('.html'))
        if page is None:
            return match.group(0)
        suffix = f'#{anchor}' if anchor else ''
        return f'href="{{% url \'site:{page.name}\' %}}{suffix}"'

    fragment = fragment.replace('href="lk.html"', 'href="{% url \'portal:dashboard\' %}"')
    # старые адреса университета → наши страницы
    for pattern, page_name in LINK_MAP:
        target = (
            "{% url 'schedule:schedule' %}"
            if page_name == 'CABINET_SCHEDULE'
            else "{% url 'site:" + page_name + "' %}"
        )
        fragment = re.sub('href="' + pattern + '"', 'href="' + target + '"', fragment)
    # внутренние ссылки: без новой вкладки, без хвостов «bgitu.ru» и стрелок
    def clean_internal(match: re.Match) -> str:
        tag = match.group(0).replace(' target="_blank"', '').replace(' rel="noopener"', '')
        return tag

    fragment = re.sub(r'<a\b[^>]*href="\{% url[^"]*"[^>]*>', clean_internal, fragment)
    fragment = re.sub(
        r'(<a\b[^>]*href="\{% url[^"]*"[^>]*>.*?</a>)',
        lambda m: m.group(1)
            .replace('bgitu.ru ↗', '')
            .replace('bgitu.ru', '')
            .replace('eos.bgitu.ru', '')
            .replace(' ↗', ''),
        fragment,
        flags=re.S,
    )
    fragment = re.sub(r'href="([\w\-]+\.html(?:#[^"]*)?)"', href, fragment)

    for asset in ('icon-180.png', 'icon-192.png', 'icon-512.png', 'icon-maskable-512.png',
                  'manifest.webmanifest', 'sw.js'):
        tag = "{% static 'site/" + asset + "' %}"
        fragment = fragment.replace('href="' + asset + '"', 'href="' + tag + '"')
        fragment = fragment.replace('content="' + asset + '"', 'content="' + tag + '"')
    fragment = fragment.replace("register('sw.js')", "register('{% static \'site/sw.js\' %}')")
    return fragment


def extract_body(html: str) -> str:
    start = html.index('<body')
    return html[html.index('>', start) + 1:html.rindex('</body>')]


def split_body(body: str) -> tuple[str, str, str]:
    """(шапка-и-оверлеи, содержимое main, подвал-и-скрипты)."""
    main_start = body.index('<main')
    main_open_end = body.index('>', main_start) + 1
    main_end = body.index('</main>') + len('</main>')
    return body[:main_start], body[main_open_end:body.index('</main>')], body[main_end:]


def page_description(html: str) -> str:
    match = re.search(r'<meta name="description" content="([^"]*)"', html)
    return match.group(1).strip() if match else ''


def page_title(html: str) -> str:
    match = re.search(r'<title>(.*?)</title>', html, re.S)
    return match.group(1).strip() if match else 'БГИТУ'


def nav_section(html: str, name: str) -> str:
    match = re.search(r'<title>([^<]*)</title>', html)
    return name


def main() -> None:
    if not SOURCE.is_dir():
        raise SystemExit(f'Не найдена папка архива: {SOURCE}')

    html_files = sorted(p for p in SOURCE.glob('*.html') if p.name != '404.html')
    pages: dict[str, Page] = {}
    for path in html_files:
        name = path.stem
        if name in SLUGS and SLUGS[name] is None:
            continue
        slug = SLUGS.get(name, name)
        pages[name] = Page(name=name, slug=slug, title='', section=SECTIONS.get(name, ''))

    sample = read(SOURCE / 'index.html')
    css_dir = ROOT / 'static' / 'site' / 'css'
    js_dir = ROOT / 'static' / 'site' / 'js'
    tpl_dir = ROOT / 'apps' / 'site' / 'templates' / 'site'
    for directory in (css_dir, js_dir, tpl_dir / 'pages'):
        directory.mkdir(parents=True, exist_ok=True)

    style_lines = extract_style(sample).split('\n')
    # Правила шапки выносим отдельно: один и тот же header.css используют
    # и публичные страницы, и кабинет — навбар выглядит одинаково.
    header_ranges = ((170, 208), (507, 510), (548, 549))
    header_lines: list[str] = []
    for start, end in header_ranges:
        header_lines.extend(style_lines[start - 1:end])
    kept: list[str] = []
    drop = {index for start, end in header_ranges for index in range(start - 1, end)}
    for index, line in enumerate(style_lines):
        if index not in drop:
            kept.append(line)
    (css_dir / 'header.css').write_text(
        '/* Шапка сайта и кабинета: один компонент на обе части. */\n'
        '.site-head { padding: 0; }\n'
        + '\n'.join(header_lines) + '\n'
        + MOBILE_HEAD_CSS,
        encoding='utf-8',
    )
    (css_dir / 'site.css').write_text('\n'.join(kept) + '\n', encoding='utf-8')

    scripts = extract_scripts(sample)
    (js_dir / 'mode.js').write_text(scripts[0] + '\n', encoding='utf-8')
    (js_dir / 'reveal.js').write_text(scripts[2] + '\n', encoding='utf-8')
    core_js = scripts[3]
    start = core_js.find('/* ---------- Поиск: Ctrl/⌘ + K ---------- */')
    end = core_js.find('/* ---------- Фон «шёлк»')
    if start != -1 and end != -1:
        # поиск живёт в отдельном общем модуле static/js/search.js
        core_js = core_js[:start] + core_js[end:]
    # шапку и мобильное меню обслуживает app.js (общий для сайта и кабинета)
    start_m = core_js.find('/* ---------- Шапка и мобильное меню ---------- */')
    end_m = core_js.find('// Страницы, которых ещё нет в макете')
    if start_m != -1 and end_m != -1:
        core_js = core_js[:start_m] + core_js[end_m:]
    # подсказку клавиши ставит search.js — убираем дубль из макета
    core_js = re.sub(r"\$\('\[data-kbd\]'\)\.textContent\s*=\s*[^;]+;\n?", '', core_js)
    (js_dir / 'core.js').write_text(core_js + '\n', encoding='utf-8')
    (js_dir / 'trail.js').write_text(scripts[4] + '\n', encoding='utf-8')
    (js_dir / 'secbar.js').write_text(scripts[5] + '\n', encoding='utf-8')
    tail_blocks = split_tail_script(scripts[6])
    (js_dir / 'week.js').write_text(tail_blocks.get('week', '') + '\n', encoding='utf-8')
    extras = '\n\n'.join(
        tail_blocks[key] for key in ('a11y', 'cookies', 'pwa') if key in tail_blocks
    )
    (js_dir / 'extras.js').write_text(extras + '\n', encoding='utf-8')

    for asset in ('manifest.webmanifest', 'sw.js', 'icon-180.png', 'icon-192.png',
                  'icon-512.png', 'icon-maskable-512.png', 'icon.png'):
        source = SOURCE / asset
        if source.exists():
            shutil.copy2(source, ROOT / 'static' / 'site' / asset)

    head, content, tail = split_body(extract_body(sample))
    head = rewrite_links(head, pages)
    for section, page_file in (('university', 'university.html'), ('admission', 'admission.html'),
                               ('student', 'student.html'), ('science', 'science.html'),
                               ('news', 'news.html'), ('contacts', 'contacts.html')):
        head = head.replace(
            f'<a href="{{% url \'site:{page_file.removesuffix(".html")}\' %}}"',
            f'<a href="{{% url \'site:{page_file.removesuffix(".html")}\' %}}"'
            f'{{% if page.section == \'{section}\' %}} aria-current="page"{{% endif %}}',
        )
    tail = re.sub(r'<script(?![^>]*src)[^>]*>.*?</script>', '', tail, flags=re.S).strip()
    tail = rewrite_links(tail, pages)

    base = f"""{{% load static %}}
<!DOCTYPE html>
<html lang="ru" data-theme="{{{{ ui_theme|default:'paper' }}}}" data-auth="{{% if user.is_authenticated %}}1{{% else %}}0{{% endif %}}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{{% block title %}}{{{{ page.title }}}}{{% endblock %}} — БГИТУ</title>
  <meta name="description" content="{{{{ page.description }}}}">
  <meta name="theme-color" content="#f2eee6">
  <link rel="icon" href="{{% static 'site/icon-192.png' %}}">
  <link rel="manifest" href="{{% static 'site/manifest.webmanifest' %}}">
  <link rel="apple-touch-icon" href="{{% static 'site/icon-180.png' %}}">
  <script src="{{% static 'site/js/mode.js' %}}"></script>
  <script src="{{% static 'js/theme.js' %}}"></script>
  <link rel="stylesheet" href="{{% static 'site/css/site.css' %}}">
  <link rel="stylesheet" href="{{% static 'site/css/header.css' %}}">
  <link rel="stylesheet" href="{{% static 'site/css/search.css' %}}">
  <link rel="stylesheet" href="{{% static 'site/css/a11y.css' %}}">
</head>
<body id="top">
{{% include 'partials/site_head.html' with nav_section=page.section nav_mode='site' %}}

<main id="main">
  {{% block content %}}{{% endblock %}}
</main>

{tail}

<script src="{{% static 'site/js/reveal.js' %}}"></script>
<script src="{{% static 'site/js/core.js' %}}"></script>
<script src="{{% static 'site/js/fallback.js' %}}"></script>
<script src="{{% static 'site/js/trail.js' %}}"></script>
<script src="{{% static 'site/js/secbar.js' %}}"></script>
<script src="{{% static 'site/js/week.js' %}}"></script>
<script src="{{% static 'site/js/extras.js' %}}"></script>
<script src="{{% static 'js/app.js' %}}"></script>
<script src="{{% static 'js/search.js' %}}"></script>
</body>
</html>
"""
    (tpl_dir / 'base.html').write_text(base, encoding='utf-8')

    registry: list[Page] = []
    for name, page in pages.items():
        html = read(SOURCE / f'{name}.html')
        _, page_content, _ = split_body(extract_body(html))
        page_content = rewrite_links(page_content, pages)
        title = page_title(html).replace(' — БГИТУ', '')
        registry.append(Page(
            name=name,
            slug=page.slug,
            title=title,
            section=SECTIONS.get(name, ''),
            description=page_description(html),
        ))
        (tpl_dir / 'pages' / f'{name}.html').write_text(
            '{% extends "site/base.html" %}\n{% load static %}\n\n{% block content %}\n'
            + page_content.strip() + '\n{% endblock %}\n',
            encoding='utf-8',
        )

    # --- поисковый индекс: заголовки, подзаголовки и начало текста страниц
    def page_text(fragment: str) -> str:
        text = re.sub(r'<script.*?</script>', ' ', fragment, flags=re.S)
        text = re.sub(r'<style.*?</style>', ' ', text, flags=re.S)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = text.replace('&nbsp;', ' ').replace('&mdash;', '—').replace('&laquo;', '«')
        return re.sub(r'\s+', ' ', text).strip()

    search_lines = [
        '"""Поисковый индекс публичного сайта (генерируется build_static_site.py)."""',
        '',
        'from dataclasses import dataclass',
        '',
        '',
        '@dataclass(frozen=True)',
        'class SearchEntry:',
        '    title: str',
        '    url_name: str',
        '    section: str',
        '    snippet: str',
        '    keywords: str',
        '',
        '',
    ]
    entries: list[str] = []
    for page in registry:
        html_page = read(SOURCE / f'{page.name}.html')
        _, body, _ = split_body(extract_body(html_page))
        text = page_text(body)[:1200]
        snippet = text[:180].strip()
        heads = ' '.join(re.findall(r'<h[12][^>]*>(.*?)</h[12]>', body, re.S))
        keywords = page_text(heads)[:400]
        entries.append(
            '    SearchEntry('
            f'title={page.title!r}, url_name={page.name!r}, section={page.section!r}, '
            f'snippet={snippet!r}, keywords={keywords!r}),'
        )
    search_lines.append('SITE_ENTRIES: tuple[SearchEntry, ...] = (')
    search_lines.extend(entries)
    search_lines.append(')')
    (ROOT / 'apps' / 'site' / 'search_site.py').write_text('\n'.join(search_lines) + '\n', encoding='utf-8')

    lines = ['''"""Реестр страниц публичной части сайта.

Каждая страница — экземпляр Page: имя файла, slug в URL, заголовок.
Вьюха PageView берёт шаблон из Page.template, URL-ы строятся из реестра.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Page:
    name: str
    slug: str
    title: str
    section: str = ''
    description: str = ''

    @property
    def template(self) -> str:
        return f'site/pages/{self.name}.html'


PAGES: tuple[Page, ...] = (''']
    for page in registry:
        lines.append(
            f"    Page(name={page.name!r}, slug={page.slug!r}, title={page.title!r}, "
            f"section={page.section!r}, description={page.description!r}),"
        )
    lines.append(')\n\nPAGES_BY_NAME = {page.name: page for page in PAGES}')
    (ROOT / 'apps' / 'site' / 'pages.py').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'готово: {len(registry)} страниц, CSS {len((css_dir / "site.css").read_text())} байт')


if __name__ == '__main__':
    main()
