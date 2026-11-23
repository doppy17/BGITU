"""Учебный план: выборка дисциплин с приоритетом источников.

Файлы планов разных направлений хранятся в БД одновременно. Если две записи
описывают одну дисциплину в одном семестре, видна запись из самого свежего
файла (uploaded_at), а старая остаётся в БД и вернётся, если свежий файл
удалить. Записи без источника (демо-план из миграции) считаются самыми
старыми и показываются только пока у направления нет ни одного файла.
"""
from __future__ import annotations

from .models import CurriculumEntry
from .plan_import import direction_code_for_program, subject_key


def run_segments(weeks):
    """[1, 2, 4] → [(1, 2), (4, 4)]."""
    weeks = sorted(set(weeks))
    if not weeks:
        return []
    segments = []
    start = prev = weeks[0]
    for week in weeks[1:]:
        if week == prev + 1:
            prev = week
            continue
        segments.append((start, prev))
        start = prev = week
    segments.append((start, prev))
    return segments


def source_rank(entry):
    """Свежесть записи: файл важнее демо, затем дата загрузки, затем id."""
    if not entry.source_id:
        return (0, None, 0, entry.pk)
    return (1, entry.source.uploaded_at, entry.source_id, entry.pk)


def plan_code_for_group(group):
    """Код плана для группы: шифр из программы, иначе префикс группы."""
    if group is None:
        return ''
    code = direction_code_for_program(group.program or '')
    if code:
        return code
    prefix = (group.code or '').split('-')[0].strip().upper()
    return {'ИСТ': 'ist', 'ИВТ': 'ivt', 'ПИ': 'pi', 'ПРИ': 'pri'}.get(prefix, '')


def direction_entries(code):
    """Записи направления; демо-план уступает место загруженным файлам."""
    entries = list(
        CurriculumEntry.objects.filter(code=code).select_related('source')
    )
    sourced = [entry for entry in entries if entry.source_id]
    return sourced or entries


def semester_winners(code):
    """{(ключ дисциплины, семестр): запись-победитель} — приоритет источников."""
    winners = {}
    for entry in direction_entries(code):
        key = subject_key(entry.subject)
        if not key:
            continue
        start = max(1, entry.semester_from)
        end = max(start, entry.semester_to)
        for semester in range(start, end + 1):
            slot = (key, semester)
            if slot not in winners or source_rank(entry) > source_rank(winners[slot]):
                winners[slot] = entry
    return winners


def resolve_curriculum(code):
    """Дисциплины направления → отрезки семестров с учётом приоритета файлов.

    Для каждого семестра каждой дисциплины выбирается запись с самым свежим
    источником. Дисциплина, которая в старом файле шла 1–4, а в новом 1–2,
    покажет 1–2 из нового файла; семестры, которых в новом файле нет, останутся
    из старого (без потерь).
    """
    by_subject = {}
    for (key, semester), entry in semester_winners(code).items():
        by_subject.setdefault(key, []).append((semester, entry))

    rows = []
    for entries in by_subject.values():
        # Название — из самой свежей записи дисциплины.
        latest = max((entry for _, entry in entries), key=source_rank)
        segments = run_segments([semester for semester, _ in entries])
        rows.append({
            'subject': latest.subject,
            'segments': segments,
            'first': segments[0][0],
        })

    rows.sort(key=lambda item: (item['first'], item['subject'].lower()))
    colors = {}
    result = []
    for row in rows:
        key = subject_key(row['subject'])
        if key not in colors:
            colors[key] = len(colors) % 6
        result.append({
            'subject': row['subject'],
            'segments': row['segments'],
            'color': colors[key],
        })
    return result
