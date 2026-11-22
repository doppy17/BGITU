"""Поиск по публичному сайту и разделам кабинета.

Индекс сайта генерируется из шаблонов (``search_site.py``), разделы кабинета
описаны вручную (``search_cabinet.py``). Поиск — подстрочный, без внешних служб.
"""
from __future__ import annotations

import re

from .search_cabinet import CABINET_ENTRIES

from .search_site import SITE_ENTRIES, SearchEntry

ALL_ENTRIES: tuple[SearchEntry, ...] = SITE_ENTRIES + CABINET_ENTRIES

_STOPWORDS = {'и', 'в', 'на', 'по', 'для', 'с', 'о', 'об', 'где', 'как', 'что', 'the', 'a'}


def normalize(value: str) -> str:
    value = value.lower().replace('ё', 'е')
    return re.sub(r'[^\wа-я0-9]+', ' ', value)


def search(query: str, limit: int = 20) -> list[tuple[SearchEntry, int]]:
    """Возвращает найденные разделы с оценкой релевантности."""
    words = [word for word in normalize(query).split() if word and word not in _STOPWORDS]
    if not words:
        return []
    results: list[tuple[SearchEntry, int]] = []
    for entry in ALL_ENTRIES:
        haystack_title = normalize(entry.title)
        haystack = ' '.join((haystack_title, normalize(entry.keywords), normalize(entry.snippet)))
        score = 0
        matched = 0
        for word in words:
            if word in haystack_title:
                score += 5
                matched += 1
            elif word in haystack:
                score += 2
                matched += 1
        if matched == len(words):
            results.append((entry, score))
        elif matched and len(words) > 1:
            results.append((entry, score - 2))
    results.sort(key=lambda item: (-item[1], item[0].title))
    return results[:limit]
