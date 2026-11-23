"""Сохранение импортированного расписания и выборка занятий.

Источников может быть несколько: каждый загруженный файл — отдельный
ScheduleImport со своим охватом (институт/направления). Занятия ссылаются
на источник, поэтому файл можно удалить вместе с его занятиями, а при
конфликте слотов выигрывает самый свежий файл.
"""
import os
import re
from datetime import date, timedelta

from django.db import transaction

from apps.education.models import Group

from .import_service import parse_schedule
from .models import Bell, Lesson, ScheduleImport

# Учебный календарь: понедельник 1-й недели осеннего семестра 2026/27.
SEMESTER_START = date(2026, 8, 31)
SEMESTER_WEEKS = 18


def semester_week_of(day):
    return (day - SEMESTER_START).days // 7 + 1


def semester_monday(week):
    return SEMESTER_START + timedelta(weeks=week - 1)


def parity_of_monday(monday):
    """Чётность учебной недели по её номеру в семестре.

    Верхняя половина ячейки файла — 1-я неделя (нечётная), нижняя — 2-я.
    Считать по ISO-номеру нельзя: 1-я неделя семестра может попасть на
    чётный ISO-номер, и половины поменяются местами.
    """
    return 'odd' if semester_week_of(monday) % 2 else 'even'

# Аббревиатуры охвата, которые встречаются в именах файлов БГИТУ.
COVERAGE_LABELS = {
    'IVT': 'ИВТ',
    'IST': 'ИСТ',
    'PI': 'ПИ',
    'PRI': 'ПРИ',
    'IEI': 'ИЭИ',
    'ИВТ': 'ИВТ',
    'ИСТ': 'ИСТ',
    'ПИ': 'ПИ',
    'ПРИ': 'ПРИ',
    'ИЭИ': 'ИЭИ',
}


def group_course(code):
    digits = ''.join(ch for ch in code if ch.isdigit())
    return int(digits[0]) if digits else 1


def coverage_from_lessons(lessons):
    """Охват по кодам групп файла: ИСТ-201, ИВТ-101 → «ИВТ · ИСТ»."""
    labels = []
    for item in lessons:
        prefix = str(item.get('group', '')).split('-')[0].strip().upper()
        if prefix and prefix not in labels:
            labels.append(prefix)
    return ' · '.join(sorted(labels))[:160]


def detect_institute(filename, lessons=None):
    """Институт/охват файла: сначала по имени, затем по группам внутри."""
    stem = os.path.splitext(os.path.basename(filename or ''))[0]
    labels = []
    for token in re.findall(r'[A-Za-zА-Яа-яЁё]{2,6}', stem):
        label = COVERAGE_LABELS.get(token.upper())
        if label and label not in labels:
            labels.append(label)
    if labels:
        return ' · '.join(labels)[:160]
    if lessons:
        from_groups = coverage_from_lessons(lessons)
        if from_groups:
            return from_groups
    return ''


def run_import(record: ScheduleImport, source_url='', replace_all=False):
    """Разобрать файл источника и сохранить его занятия.

    replace_all=True — старое поведение «файл заменяет всё расписание»
    (полная перезаливка каталога из management-команд). По умолчанию
    заменяются только занятия источников того же охвата: файлы разных
    институтов живут одновременно.
    """
    path = record.file.path
    lessons, bells = parse_schedule(path)
    codes = sorted({item['group'] for item in lessons})
    institute = (record.institute or '').strip() or detect_institute(record.original_name, lessons)
    if not record.size:
        try:
            record.size = os.path.getsize(path)
        except OSError:
            record.size = 0

    with transaction.atomic():
        if replace_all:
            Lesson.objects.all().delete()
            ScheduleImport.objects.exclude(pk=record.pk).delete()
            replaced = 0
        else:
            # Повторный импорт того же файла не должен плодить дубли.
            Lesson.objects.filter(source=record).delete()
            stale = ScheduleImport.objects.exclude(pk=record.pk)
            if institute:
                stale = stale.filter(institute__iexact=institute)
            else:
                # Охват не определён — заменяем только одноимённый файл.
                stale = stale.filter(institute='', original_name=record.original_name)
            stale_ids = list(stale.values_list('id', flat=True))
            if stale_ids:
                Lesson.objects.filter(source_id__in=stale_ids).delete()
                ScheduleImport.objects.filter(id__in=stale_ids).delete()
            replaced = len(stale_ids)

        groups = {}
        for code in codes:
            groups[code], _ = Group.objects.get_or_create(
                code=code,
                defaults={'course': group_course(code), 'program': '09.03.02 «Информационные системы и технологии»'},
            )
        Lesson.objects.bulk_create([
            Lesson(
                group=groups[item['group']],
                weekday=item['weekday'],
                pair=item['pair'],
                week=item['week'],
                subject=item['subject'][:180],
                kind=item['kind'][:40],
                teacher_name=item['teacher_name'][:120],
                room=item['room'][:40],
                building=item.get('building', '')[:1],
                subgroup=item['subgroup'][:8],
                source=record,
            )
            for item in lessons
        ])
        for (building, pair), (start, end) in bells.items():
            Bell.objects.update_or_create(
                building=building, pair=pair, defaults={'start': start, 'end': end}
            )
        record.lessons_count = len(lessons)
        record.groups_count = len(codes)
        record.institute = institute[:160]
        record.note = 'импорт занятий из файла БГИТУ'
        if replaced:
            record.note = f'{record.note} · заменено файлов: {replaced}'[:255]
        if source_url:
            record.source_url = source_url
        record.save(update_fields=[
            'lessons_count', 'groups_count', 'institute', 'note', 'source_url', 'size',
        ])
    return record


def _subgroup_key(value):
    """«1»/«2» из файла и «а»/«б» в интерфейсе — одна и та же подгруппа."""
    value = (value or '').strip().lower()
    return {'1': 'а', '2': 'б'}.get(value, value)


def lesson_slot(lesson):
    """Ключ конфликта: группа + день + пара + подгруппа (без недели).

    Чётность недели не входит: свежий файл владеет слотом целиком, иначе
    старое занятие другой чётности «просвечивало» бы в том же слоте.
    """
    return (lesson.group_id, lesson.weekday, lesson.pair, _subgroup_key(lesson.subgroup))


def source_rank(lesson):
    """Свежесть источника: (есть ли файл, uploaded_at, id).

    Занятия без источника (ручные/старые) считаются самыми старыми, но
    между собой не конфликтуют — у них одинаковый ранг.
    """
    if not lesson.source_id:
        return (0, None, 0)
    return (1, lesson.source.imported_at, lesson.source_id)


def resolve_lessons(lessons):
    """Оставить только занятия из самого свежего источника в каждом слоте.

    Более старые занятия в этом слоте не показываются, но остаются в БД:
    удаление источника — отдельное действие пользователя.
    """
    lessons = list(lessons)
    winners = {}
    for lesson in lessons:
        key = lesson_slot(lesson)
        rank = source_rank(lesson)
        if key not in winners or rank > winners[key]:
            winners[key] = rank
    return [lesson for lesson in lessons if source_rank(lesson) == winners[lesson_slot(lesson)]]


def visible_lessons(group, parity=None):
    """Занятия группы с учётом приоритета источников и чётности недели."""
    queryset = Lesson.objects.filter(group=group).select_related('source')
    lessons = resolve_lessons(queryset)
    if parity:
        lessons = [item for item in lessons if item.week in (Lesson.WEEK_ALL, parity)]
    return lessons
