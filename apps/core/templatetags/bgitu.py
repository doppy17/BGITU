"""Шаблонные фильтры проекта."""
from urllib.parse import quote

from django import template

register = template.Library()


# Цвета favicon по темам — те же значения, что в :root[data-theme=…] tokens.css.
# Синхронность проверяет apps/site/tests.py::FaviconTests.
THEME_FAVICON = {
    'paper': ('#b8322a', '#ffffff'),
    'surik': ('#ff5a2e', '#0d0d0d'),
    'latun': ('#d6b072', '#0d0d0d'),
    'brass': ('#856632', '#ffffff'),
}
FAVICON_SVG = (
    "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 64 64\">"
    "<rect width=\"64\" height=\"64\" rx=\"14\" fill=\"{accent}\"/>"
    "<text x=\"32\" y=\"46\" font-family=\"Arial,sans-serif\" font-size=\"40\""
    " font-weight=\"bold\" text-anchor=\"middle\" fill=\"{ink}\">Б</text></svg>"
)


@register.filter
def favicon_uri(theme):
    """data-URI иконки вкладки в цветах темы (без сетевых запросов)."""
    accent, ink = THEME_FAVICON.get(str(theme), THEME_FAVICON['paper'])
    svg = FAVICON_SVG.format(accent=accent, ink=ink)
    return 'data:image/svg+xml,' + quote(svg, safe='')


@register.filter
def ru_num(value):
    if value is None or value == '':
        return '—'
    return str(value).replace('.', ',')


@register.filter
def get_item(mapping, key):
    if mapping is None:
        return None
    return mapping.get(key)


@register.filter
def percent(part, total):
    try:
        return round(int(part) / int(total) * 100)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0


@register.filter
def fio(user):
    """«Портнов Георгий» — фамилия, затем имя (в User поля наоборот)."""
    if not user:
        return ''
    name = f'{getattr(user, "last_name", "")} {getattr(user, "first_name", "")}'.strip()
    return name or getattr(user, 'username', '')


@register.filter
def money(value):
    try:
        return f'{int(value):,}'.replace(',', '\u00a0')
    except (TypeError, ValueError):
        return value
