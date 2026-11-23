"""Подсказки поиска: мгновенный локальный prefilter + необязательное LLM-ранжирование.

Эндпоинт ``/api/search/suggest/?q=...`` отдаёт до пяти разделов сайта и кабинета.
Основу всегда даёт детерминированный подстрочный/fuzzy-поиск; LLM (если модель
загружена и успела ответить) лишь переупорядочивает 2–6 спорных кандидатов.
Любая проблема с моделью — молча возвращаем локальный результат.
"""
from __future__ import annotations

import difflib
import logging
import re
import time
from functools import lru_cache

from django.conf import settings
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.core import local_llm

from .context_processors import CABINET_URLS, SECTION_LABELS
from .pages import PAGES_BY_NAME
from .search import ALL_ENTRIES, normalize

logger = logging.getLogger(__name__)

MAX_RESULTS = 5
MAX_CANDIDATES = 6
MIN_QUERY = 2
_LLM_TIMEOUT = 1.4  # секунд: дольше ждать модель в поиске нельзя
_STOPWORDS = {'и', 'в', 'на', 'по', 'для', 'с', 'о', 'об', 'где', 'как', 'что', 'мне', 'нужно', 'the', 'a'}

# Простые синонимы студенческого сленга — расширяют локальный prefilter.
_SYNONYMS = {
    'пересдача': ('задолженности', 'оценки', 'экзамен'),
    'пересдать': ('задолженности', 'оценки', 'экзамен'),
    'зачетка': ('оценки',),
    'общага': ('общежитие', 'кампус'),
    'общагу': ('общежитие', 'кампус'),
    'степа': ('стипендия',),
    'препод': ('преподаватель',),
    'пары': ('расписание',),
    'сессия': ('экзамен', 'оценки'),
}



@lru_cache(maxsize=4)
def _build_index(urlconf: str) -> tuple[dict, ...]:
    """Тот же набор, что в ``#bgitu-index``: страницы сайта + разделы кабинета."""
    items: list[dict] = []
    for entry in ALL_ENTRIES:
        page = PAGES_BY_NAME.get(entry.url_name)
        if page is not None:
            url = reverse(f'site:{entry.url_name}')
            category = SECTION_LABELS.get(page.section, 'Раздел')
        else:
            route = CABINET_URLS.get(entry.url_name)
            if route is None:
                continue
            url = reverse(route)
            category = 'Кабинет'
        title_n = normalize(entry.title)
        hay_n = ' '.join((title_n, normalize(entry.keywords), normalize(entry.snippet)))
        items.append({
            'title': entry.title,
            'url': url,
            'cat': category,
            'why': '',
            'keys': entry.keywords,
            'title_n': title_n,
            'title_words': tuple(word for word in title_n.split() if word),
            'title_stems': tuple(_stem(word) for word in title_n.split() if word),
            'hay_n': hay_n,
            'hay_stems': tuple(_stem(word) for word in hay_n.split() if word),
        })
    return tuple(items)


def index() -> tuple[dict, ...]:
    return _build_index(settings.ROOT_URLCONF)


def _tokens(query: str) -> list[str]:
    words = [word for word in normalize(query).split() if len(word) > 1 and word not in _STOPWORDS]
    expanded = list(words)
    for word in words:
        for synonym in _SYNONYMS.get(word, ()):
            if synonym not in expanded:
                expanded.append(synonym)
    return expanded


def _stem(word: str) -> str:
    """Грубая основа для русских словоформ: «стипендия/стипендии» → «стипе»."""
    return word[:5] if len(word) >= 5 else word


def _stem_in(stem: str, words: tuple[str, ...]) -> bool:
    return any(other == stem or other.startswith(stem) for other in words)


def _ratio(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, left, right).ratio()


def _match_level(word: str, item: dict) -> int:
    """0 — нет совпадения, 10 — точное в названии, 7 — словоформа в названии, 3/2 — ключевые слова."""
    if word in item['title_n']:
        return 10
    stem = _stem(word)
    if _stem_in(stem, item['title_stems']):
        return 7
    if word in item['hay_n']:
        return 3
    if _stem_in(stem, item['hay_stems']):
        return 2
    best = max((_ratio(word, title_word) for title_word in item['title_words']), default=0.0)
    if best >= 0.8:
        return 3
    return 0


def _score(item: dict, words: list[str], query_n: str) -> int:
    levels = [_match_level(word, item) for word in words]
    matched = sum(1 for level in levels if level)
    if not matched:
        return 0
    strong = sum(1 for level in levels if level >= 7)
    phrase = bool(query_n) and query_n in item['hay_n']
    # Многословный запрос должен опираться на название или точную фразу в ключевых
    # словах: иначе «военный учебный центр» цепляется за любое слово «центр».
    if len(words) > 1 and not strong and not phrase:
        return 0
    score = sum(levels) + 8 * strong
    if phrase and len(words) > 1:
        score += 12
    if len(words) > 1:
        score -= 3 * (len(words) - matched)
    if query_n and query_n in item['title_n']:
        score += 10
    if query_n:
        ratio = _ratio(query_n, item['title_n'])
        if ratio >= 0.7:
            score += int(ratio * 6)
    # Короткие названия-разделы чуть важнее длинных заголовков статей.
    score += max(0, 3 - len(item['title']) // 30)
    return score


def _why(item: dict, words: list[str]) -> str:
    if any(_match_level(word, item) >= 7 for word in words):
        return 'в названии раздела'
    hits = [word for word in words if _match_level(word, item) >= 2]
    if hits:
        return 'по теме: ' + ', '.join(hits[:2])
    return 'похоже по названию'


def prefilter(query: str) -> list[dict]:
    """Быстрый локальный отбор: подстрока + лёгкий fuzzy, без LLM."""
    words = _tokens(query)
    if not words:
        return []
    query_n = ' '.join(words)
    scored = []
    for item in index():
        score = _score(item, words, query_n)
        if not score:
            continue
        scored.append((score, item, _why(item, words)))
    scored.sort(key=lambda pair: (-pair[0], len(pair[1]['title']), pair[1]['title']))
    if scored:
        cutoff = max(4, scored[0][0] * 0.4)
        scored = [pair for pair in scored if pair[0] >= cutoff]
    return [
        {'title': item['title'], 'url': item['url'], 'cat': item['cat'], 'why': why, 'score': score, 'keys': item['keys']}
        for score, item, why in scored[:MAX_CANDIDATES]
    ]


def _short_keys(keys: str, limit: int = 90) -> str:
    keys = re.sub(r'\s+', ' ', keys or '').strip()
    if len(keys) <= limit:
        return keys
    cut = keys[:limit].rsplit(' ', 1)[0]
    return cut + '…'


def _prompt(query: str, candidates: list[dict]) -> str:
    lines = [f'- {c["title"]}: {_short_keys(c["keys"], 70)}' for c in candidates]
    return (
        f'Запрос студента: «{query}»\n'
        'Разделы сайта:\n'
        + '\n'.join(lines)
        + '\n\nКакой раздел лучше всего отвечает на запрос? '
        'Напиши только название раздела из списка, без пояснений.'
    )


def _pick_best(answer: str, candidates: list[dict]) -> int | None:
    """Находит в ответе модели название одного из кандидатов (номер в списке)."""
    answer_n = normalize(answer or '')
    if not answer_n:
        return None
    best: tuple[int, int] | None = None
    for position, candidate in enumerate(candidates):
        found = answer_n.find(normalize(candidate['title']))
        if found >= 0 and (best is None or found < best[0]):
            best = (found, position)
    if best is not None:
        return best[1]
    # Модель могла склонять название — сравниваем основы.
    answer_stems = {_stem(word) for word in answer_n.split()}
    for position, candidate in enumerate(candidates):
        if _stem(candidate['title']) in answer_stems:
            return position
    return None


def _llm_ranked(query: str, candidates: list[dict]) -> list[dict]:
    answer = local_llm.ask(_prompt(query, candidates), max_tokens=20, timeout=_LLM_TIMEOUT)
    if not answer:
        return []
    picked = _pick_best(answer, candidates)
    if picked is None:
        logger.info('AI-поиск: «%s» — ответ модели не распознан: %r', query, answer[:80])
        return []
    ordered = [candidates[picked]] + [c for i, c in enumerate(candidates) if i != picked]
    logger.info('AI-поиск: «%s» — LLM выбрала «%s» (ответ %r)', query, candidates[picked]['title'], answer[:40])
    return [
        {'title': c['title'], 'url': c['url'], 'why': c['why']}
        for c in ordered[:MAX_RESULTS]
    ]


@require_GET
def suggest(request):
    """JSON-подсказки для диалога поиска. Никогда не возвращает 500."""
    started = time.perf_counter()
    try:
        query = (request.GET.get('q') or '').strip()[:120]
        if len(query) < MIN_QUERY:
            return JsonResponse({'items': []})

        candidates = prefilter(query)
        items: list[dict] = []
        if 2 <= len(candidates) <= MAX_CANDIDATES:
            items = _llm_ranked(query, candidates)
        if not items:
            items = [
                {'title': c['title'], 'url': c['url'], 'why': c['why']}
                for c in candidates[:MAX_RESULTS]
            ]
        logger.info(
            'AI-поиск: «%s» — %d подсказок за %.0f мс',
            query, len(items), (time.perf_counter() - started) * 1000,
        )
        return JsonResponse({'items': items})
    except Exception as exc:  # noqa: BLE001 — подсказки не должны ломать поиск
        logger.warning('AI-поиск: сбой, отдаю пустой список: %s', exc)
        return JsonResponse({'items': []})
