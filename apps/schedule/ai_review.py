"""ИИ-разбор файлов расписания и учебного плана.

Все числа и проверки считает Python: сколько занятий, групп, преподавателей,
где пересекаются аудитории и преподаватели, какие строки подозрительны, какие
дисциплины плана остались без семестра. Локальная LLM получает уже готовые
факты и пишет только короткое человеческое резюме (≤ 5 пунктов, ≤ 500 знаков),
поэтому выдумать цифры она не может — их просто нет в её ответе.

Модель — из общего рантайма проекта (`apps.core.local_llm.ask`). Импорт
ленивый: если рантайма ещё нет, `ask_local` тихо возвращает None, и разбор
показывает факты без резюме. Своего HTTP-клиента здесь нет.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from django.utils import timezone

from apps.education.plan_import import parse_plan, subject_key

MAX_SUMMARY_POINTS = 5
MAX_SUMMARY_CHARS = 500
MAX_PAIR = 8
MAX_WEEKDAY = 6
CONFLICT_LIMIT = 8
SUSPECT_LIMIT = 8

WEEKDAY_NAMES = ['', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
WEEK_LABELS = {'all': '', 'odd': '1-я неделя', 'even': '2-я неделя'}
SERVICE_WORDS = ('итого', 'всего', 'блок ', 'название дисциплин', 'семестр', 'зачетн')
GROUP_RE = re.compile(r'^[А-ЯЁA-Z]{2,6}\s*-\s*\d{2,3}$')


def _ask_stub(prompt, max_tokens=120, timeout=None):
    """Заглушка на случай, если рантайм локальной модели ещё не подключён."""
    return None


def ask_local(prompt, max_tokens=120, timeout=30):
    """Короткий запрос к локальной модели; None, если её нет или она упала.

    Импорт намеренно внутри функции: `apps/core/local_llm.py` может появиться
    позже, и тогда он подхватится сам, без правок этого модуля. Таймаут не
    даёт запросу из ЛК висеть, если модель загружена и занята.
    """
    try:
        from apps.core.local_llm import ask
    except ImportError:
        ask = _ask_stub
    except Exception:
        return None
    try:
        return ask(prompt, max_tokens=max_tokens, timeout=timeout)
    except TypeError:
        # Рантайм со старой сигнатурой ask(prompt, max_tokens).
        try:
            return ask(prompt, max_tokens=max_tokens)
        except Exception:
            return None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Общие помощники
# --------------------------------------------------------------------------- #

def clean_summary(text, max_points=MAX_SUMMARY_POINTS, max_chars=MAX_SUMMARY_CHARS):
    """Ответ модели → список пунктов, гарантированно ≤ 5 и ≤ 500 знаков."""
    text = (text or '').strip()
    if not text:
        return []
    lines = []
    for raw in text.splitlines():
        line = re.sub(r'[*_`#]+', '', raw).strip()
        line = re.sub(r'^\s*(?:[-*•–—]|\d+[.)])\s*', '', line).strip()
        if len(line) < 3:
            continue
        if line.lower().rstrip(':') in ('итог', 'итоги', 'вывод', 'выводы', 'резюме'):
            continue
        lines.append(line)
    if len(lines) == 1:
        sentences = [part.strip() for part in re.split(r'(?<=[.!?])\s+', lines[0]) if part.strip()]
        if len(sentences) >= 2:
            lines = sentences
    points, used = [], 0
    for line in lines[:max_points]:
        remaining = max_chars - used - (2 if points else 0)
        if remaining < 20:
            break
        if len(line) > remaining:
            line = line[:remaining - 1].rsplit(' ', 1)[0].rstrip(' ,;:') + '…'
        points.append(line)
        used += len(line) + 2
    return points


def _problem_lines(review):
    """Факты для промпта: только то, что уже посчитано кодом."""
    lines = [f"{item['label']}: {item['value']}." for item in review['highlights']]
    issues = [item for item in review['issues'] if item['count']]
    if issues:
        lines.append('')
        lines.append('ТРЕБУЕТ ВНИМАНИЯ:')
        for issue in issues[:6]:
            lines.append(f"- {issue['title']}: {issue['count']}.")
    else:
        lines.append('')
        lines.append('ТРЕБУЕТ ВНИМАНИЯ: ничего не найдено.')
    return '\n'.join(lines)


def build_prompt(review):
    kind = 'расписания' if review.get('kind') == 'schedule' else 'учебного плана'
    return (
        f'Ты — методист деканата. Программа уже проверила файл {kind}, факты ниже верны.\n'
        'Напиши 2–3 коротких пункта для сотрудника деканата: что проверить в первую очередь '
        'и почему. Каждый пункт — одна строка не длиннее 90 символов, без вступления. '
        'Опирайся только на раздел «ТРЕБУЕТ ВНИМАНИЯ»; если он пуст, ответь одной строкой: '
        '«Существенных проблем не найдено». Не перечисляй общие цифры и не добавляй числа, '
        'названия и единицы измерения, которых нет в фактах.\n'
        + _problem_lines(review)
    )


def _numbers_allowed(points, source_text):
    """Отбросить пункты с числами, которых нет в фактах (защита от выдумок)."""
    allowed = set(re.findall(r'\d+', source_text))
    result = []
    for point in points:
        if set(re.findall(r'\d+', point)) <= allowed:
            result.append(point)
    return result


def _finalize(review, use_llm=True):
    """Дописать резюме модели, не теряя факты при её недоступности."""
    review['summary'] = None
    review['summary_points'] = []
    review['llm'] = {'available': False, 'note': ''}
    review['has_issues'] = any(item['count'] for item in review['issues'])
    if not use_llm:
        review['llm']['note'] = 'резюме отключено'
        return review
    prompt = build_prompt(review)
    raw = ask_local(prompt, max_tokens=110)
    points = _numbers_allowed(clean_summary(raw), prompt)
    if not points:
        review['llm']['note'] = (
            'резюме отклонено: модель добавила числа, которых нет в фактах'
            if raw else 'локальная модель недоступна'
        )
        return review
    review['summary_points'] = points
    review['summary'] = '\n'.join(f'• {point}' for point in points)[:MAX_SUMMARY_CHARS]
    review['llm'] = {'available': True, 'note': ''}
    return review


def _issue(code, title, count, examples=None, note=''):
    return {
        'code': code,
        'title': title,
        'count': int(count),
        'examples': list(examples or [])[:SUSPECT_LIMIT],
        'note': note,
    }


def _highlight(label, value):
    return {'label': label, 'value': str(value)}


# --------------------------------------------------------------------------- #
# Расписание
# --------------------------------------------------------------------------- #

def _normalize_parsed(item):
    return {
        'group': str(item.get('group') or '').strip().upper(),
        'weekday': int(item.get('weekday') or 0),
        'pair': int(item.get('pair') or 0),
        'week': str(item.get('week') or 'all'),
        'subject': str(item.get('subject') or '').strip(),
        'kind': str(item.get('kind') or '').strip(),
        'teacher_name': str(item.get('teacher_name') or '').strip(),
        'room': str(item.get('room') or '').strip(),
        'building': str(item.get('building') or '').strip(),
        'subgroup': str(item.get('subgroup') or '').strip(),
    }


def _normalize_lesson(lesson):
    return {
        'group': str(getattr(lesson.group, 'code', '') or '').strip().upper(),
        'weekday': int(lesson.weekday or 0),
        'pair': int(lesson.pair or 0),
        'week': str(lesson.week or 'all'),
        'subject': str(lesson.subject or '').strip(),
        'kind': str(lesson.kind or '').strip(),
        'teacher_name': str(lesson.teacher_name or '').strip(),
        'room': str(lesson.room or '').strip(),
        'building': str(lesson.building or '').strip(),
        'subgroup': str(lesson.subgroup or '').strip(),
    }


def _weeks_overlap(first, second):
    return first == 'all' or second == 'all' or first == second


def _subgroups_exclusive(first, second):
    return bool(first and second and first != second)


def _slot_text(item):
    day = WEEKDAY_NAMES[item['weekday']] if 0 < item['weekday'] < len(WEEKDAY_NAMES) else '—'
    return f"{day}, {item['pair']} пара"


def _lesson_label(item):
    subject = f"«{item['subject']}»" if item['subject'] else 'без дисциплины'
    return f"{item['group'] or 'без группы'} — {subject}"


def _conflicts(lessons, mode):
    """Пересечения одного слота: аудитория, преподаватель или группа."""
    buckets = defaultdict(list)
    for item in lessons:
        if mode == 'room':
            if not item['room']:
                continue
            key = (item['weekday'], item['pair'], item['room'], item['building'])
        elif mode == 'teacher':
            if not item['teacher_name']:
                continue
            key = (item['weekday'], item['pair'], item['teacher_name'])
        else:
            if not item['group']:
                continue
            key = (item['weekday'], item['pair'], item['group'])
        buckets[key].append(item)

    examples = []
    for key in sorted(buckets):
        items = buckets[key]
        if len(items) < 2:
            continue
        clash = None
        for index, first in enumerate(items):
            for second in items[index + 1:]:
                if mode != 'group' and first['group'] == second['group']:
                    continue
                if mode == 'group' and first['subject'] == second['subject']:
                    continue
                if not _weeks_overlap(first['week'], second['week']):
                    continue
                if _subgroups_exclusive(first['subgroup'], second['subgroup']):
                    continue
                # Потоковая лекция: одна дисциплина, один преподаватель, одна
                # аудитория у нескольких групп — это норма, а не конфликт.
                same_subject = subject_key(first['subject']) == subject_key(second['subject'])
                same_room = (first['room'], first['building']) == (second['room'], second['building'])
                if same_subject and same_room and first['teacher_name'] == second['teacher_name']:
                    continue
                if mode == 'teacher' and same_subject and same_room:
                    continue
                clash = (first, second)
                break
            if clash:
                break
        if not clash:
            continue
        first, second = clash
        where = _slot_text(first)
        week = WEEK_LABELS.get(first['week']) or WEEK_LABELS.get(second['week'])
        if week:
            where = f'{where} ({week})'
        if mode == 'room':
            building = f" ({first['building']} корп.)" if first['building'] else ''
            examples.append(f'{where}: ауд. {first["room"]}{building} — {_lesson_label(first)} / {_lesson_label(second)}')
        elif mode == 'teacher':
            examples.append(f"{where}: {first['teacher_name']} — {first['group']} и {second['group']}")
        else:
            examples.append(f'{where}: {_lesson_label(first)} / {_lesson_label(second)}')
    return examples


def _suspects(lessons):
    found = []
    for item in lessons:
        reasons = []
        subject = item['subject']
        lowered = subject.lower()
        if len(subject) < 3:
            reasons.append('слишком короткое название дисциплины')
        if subject and re.fullmatch(r'[\d\s.,/–—-]+', subject):
            reasons.append('название состоит из цифр')
        if any(lowered.startswith(word) for word in SERVICE_WORDS):
            reasons.append('строка похожа на служебную')
        if not item['group']:
            reasons.append('не указана группа')
        elif not GROUP_RE.match(item['group']):
            reasons.append(f'необычный код группы «{item["group"]}»')
        if item['week'] not in ('all', 'odd', 'even'):
            reasons.append('непонятная неделя')
        if item['teacher_name'] and re.search(r'\d', item['teacher_name']):
            reasons.append('цифры в ФИО преподавателя')
        if item['room'] and len(item['room']) > 12:
            reasons.append('слишком длинная аудитория')
        if reasons:
            found.append(f'{_lesson_label(item)} · {_slot_text(item)}: {", ".join(reasons)}')
    return found


def build_schedule_facts(lessons, file_name='', header_groups=None, bell_pairs=None, source_note=''):
    """Посчитать факты расписания по списку занятий (dict или Lesson)."""
    items = [_normalize_lesson(item) if not isinstance(item, dict) else _normalize_parsed(item)
             for item in lessons]
    groups = sorted({item['group'] for item in items if item['group']})
    teachers = sorted({item['teacher_name'] for item in items if item['teacher_name']})
    rooms = sorted({item['room'] for item in items if item['room']})
    subjects = sorted({subject_key(item['subject']) for item in items if item['subject']})
    by_week = Counter(item['week'] for item in items)
    by_kind = Counter(item['kind'] for item in items if item['kind'])
    no_teacher = sum(1 for item in items if not item['teacher_name'])
    no_room = sum(1 for item in items if not item['room'])
    pairs = sorted({item['pair'] for item in items if item['pair']})
    days = sorted({item['weekday'] for item in items if item['weekday']})

    highlights = [
        _highlight('Занятий', len(items)),
        _highlight('Групп', len(groups)),
        _highlight('Преподавателей', len(teachers)),
        _highlight('Аудиторий', len(rooms)),
        _highlight('Дисциплин', len(subjects)),
        _highlight('Дней', len(days)),
        _highlight('Пар', f'1–{max(pairs)}' if pairs else '—'),
    ]
    if by_kind:
        kinds = ', '.join(f'{name} — {count}' for name, count in by_kind.most_common(4))
        highlights.append(_highlight('Виды занятий', kinds))
    if by_week.get('odd') or by_week.get('even'):
        highlights.append(_highlight(
            'По неделям',
            f"каждую — {by_week.get('all', 0)}, 1-я — {by_week.get('odd', 0)}, 2-я — {by_week.get('even', 0)}",
        ))

    issues = []
    if not items:
        issues.append(_issue('empty', 'Файл не дал ни одного занятия', 1,
                             note='Проверьте шапку с группами и строки дней недели.'))
    header = sorted({str(code).strip().upper() for code in (header_groups or []) if str(code).strip()})
    if header:
        without = [code for code in header if code not in groups]
        extra = [code for code in groups if code not in header]
        if without:
            issues.append(_issue('groups_empty', 'Группы из шапки без единой пары', len(without), without))
        if extra:
            issues.append(_issue('groups_extra', 'Группы в занятиях, которых нет в шапке', len(extra), extra))

    room_conflicts = _conflicts(items, 'room')
    teacher_conflicts = _conflicts(items, 'teacher')
    group_conflicts = _conflicts(items, 'group')
    if room_conflicts:
        issues.append(_issue('room_conflict', 'Одна аудитория в одно время у разных групп',
                             len(room_conflicts), room_conflicts[:CONFLICT_LIMIT]))
    if teacher_conflicts:
        issues.append(_issue('teacher_conflict', 'Преподаватель в одно время в разных группах',
                             len(teacher_conflicts), teacher_conflicts[:CONFLICT_LIMIT]))
    if group_conflicts:
        issues.append(_issue('group_conflict', 'У группы два занятия в один слот',
                             len(group_conflicts), group_conflicts[:CONFLICT_LIMIT]))

    out_of_grid = [item for item in items
                   if not 1 <= item['pair'] <= MAX_PAIR or not 1 <= item['weekday'] <= MAX_WEEKDAY]
    if out_of_grid:
        issues.append(_issue(
            'out_of_grid', 'Пары вне сетки (дни 1–6, пары 1–8)', len(out_of_grid),
            [f'{_lesson_label(item)} · {_slot_text(item)}' for item in out_of_grid[:CONFLICT_LIMIT]],
        ))

    if bell_pairs:
        no_bell = [item for item in items
                   if item['pair'] and item['building'] and (item['building'], item['pair']) not in bell_pairs]
        if no_bell:
            issues.append(_issue(
                'no_bell', 'Нет звонка для пары в корпусе', len(no_bell),
                [f'{_lesson_label(item)} · {_slot_text(item)} · {item["building"]} корп.' for item in no_bell[:CONFLICT_LIMIT]],
                note='Пара есть в расписании, но время звонка для неё не найдено.',
            ))

    suspects = _suspects(items)
    if suspects:
        issues.append(_issue('suspects', 'Подозрительные строки', len(suspects), suspects[:CONFLICT_LIMIT]))

    if no_teacher:
        issues.append(_issue(
            'no_teacher', 'Занятия без преподавателя', no_teacher,
            [f'{_lesson_label(item)} · {_slot_text(item)}' for item in items if not item['teacher_name']][:3],
            note='Часто норма для физкультуры и дистанционных занятий.',
        ))
    if no_room:
        issues.append(_issue(
            'no_room', 'Занятия без аудитории', no_room,
            [f'{_lesson_label(item)} · {_slot_text(item)}' for item in items if not item['room']][:3],
            note='Проверьте, не потерялась ли аудитория при разборе ячейки.',
        ))

    return {
        'kind': 'schedule',
        'file': file_name,
        'source_note': source_note,
        'generated_at': timezone.now().isoformat(timespec='seconds'),
        'stats': {
            'lessons': len(items),
            'groups': len(groups),
            'teachers': len(teachers),
            'rooms': len(rooms),
            'subjects': len(subjects),
            'days': len(days),
            'max_pair': max(pairs) if pairs else 0,
            'no_teacher': no_teacher,
            'no_room': no_room,
            'by_week': dict(by_week),
            'by_kind': dict(by_kind),
        },
        'highlights': highlights,
        'issues': issues,
    }


def review_lessons(lessons, file_name='', header_groups=None, bell_pairs=None, use_llm=True, source_note=''):
    facts = build_schedule_facts(lessons, file_name, header_groups, bell_pairs, source_note)
    return _finalize(facts, use_llm=use_llm)


def _header_groups(path):
    """Коды групп из шапки файла — чтобы найти группы без пар."""
    try:
        from .import_service import group_columns, read_rows

        rows, merged = read_rows(path)
        return [group['code'] for group in group_columns(rows, merged)]
    except Exception:
        return []


def review_schedule_file(path, use_llm=True):
    """Разбор файла расписания без записи в базу."""
    from .import_service import parse_schedule

    path = Path(path)
    lessons, bells = parse_schedule(str(path))
    bell_pairs = {(building, pair) for building, pair in bells}
    return review_lessons(
        lessons,
        file_name=path.name,
        header_groups=_header_groups(str(path)),
        bell_pairs=bell_pairs,
        use_llm=use_llm,
    )


def review_import(record, use_llm=True):
    """Разбор уже импортированного файла: цифры — из занятий этого источника."""
    from .models import Bell

    lessons = list(record.lessons.select_related('group').all())
    bell_pairs = set(Bell.objects.values_list('building', 'pair'))
    header = []
    if record.file:
        try:
            header = _header_groups(record.file.path)
        except Exception:
            header = []
    note = f'импорт №{record.pk} от {record.imported_at:%d.%m.%Y %H:%M}'
    return review_lessons(
        lessons,
        file_name=record.original_name,
        header_groups=header,
        bell_pairs=bell_pairs,
        use_llm=use_llm,
        source_note=note,
    )


# --------------------------------------------------------------------------- #
# Учебный план
# --------------------------------------------------------------------------- #

def _plan_entries_from_json(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    direction = ''
    if isinstance(data, dict):
        direction = str((data.get('meta') or {}).get('direction') or '')
        raw = data.get('disciplines') or []
    else:
        raw = data
    entries = []
    for item in raw:
        if isinstance(item, dict):
            semesters = []
            for value in item.get('semesters') or []:
                try:
                    semesters.append(int(value))
                except (TypeError, ValueError):
                    continue
            entries.append({
                'subject': str(item.get('subject') or item.get('name') or '').strip(),
                'semesters': sorted(set(semesters)),
            })
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            entries.append({'subject': str(item[0]).strip(), 'semesters': sorted(int(v) for v in item[1])})
    return entries, direction


def _plan_entries_from_table(path):
    return [{'subject': name, 'semesters': sorted(int(value) for value in semesters)}
            for name, semesters in parse_plan(path)]


def build_plan_facts(entries, file_name='', direction=''):
    """Посчитать факты плана: дисциплины, семестры, дубли, пустые семестры."""
    entries = [item for item in entries if str(item.get('subject') or '').strip()]
    by_semester = Counter()
    invalid = []
    missing = []
    seen = defaultdict(list)
    for item in entries:
        subject = str(item['subject']).strip()
        semesters = [int(value) for value in item.get('semesters') or [] if str(value).strip().lstrip('-').isdigit()]
        bad = [value for value in semesters if not 1 <= value <= 8]
        if bad:
            invalid.append(f'«{subject}»: семестры {", ".join(map(str, bad))}')
        good = [value for value in semesters if 1 <= value <= 8]
        if not good:
            missing.append(subject)
        for value in good:
            by_semester[value] += 1
        seen[subject_key(subject)].append((subject, sorted(set(good))))

    duplicates = []
    for key, rows in seen.items():
        if len(rows) < 2:
            continue
        semesters = sorted({value for _name, values in rows for value in values})
        duplicates.append(f'«{rows[0][0]}» — строк {len(rows)}, семестры {", ".join(map(str, semesters)) or "—"}')

    max_semester = max(by_semester) if by_semester else 0
    gaps = [value for value in range(1, max_semester + 1) if not by_semester.get(value)]
    suspects = []
    for item in entries:
        subject = str(item['subject']).strip()
        lowered = subject.lower()
        if len(subject) < 3:
            suspects.append(f'«{subject}» — слишком короткое название')
        elif any(lowered.startswith(word) for word in SERVICE_WORDS):
            suspects.append(f'«{subject}» — строка похожа на служебную')
        elif subject.lower() == subject and re.search(r'[а-яё]', subject) is None and len(subject) > 4:
            suspects.append(f'«{subject}» — название без кириллицы')

    highlights = [
        _highlight('Строк плана', len(entries)),
        _highlight('Дисциплин', len(seen)),
        _highlight('Семестров', f'1–{max_semester}' if max_semester else '—'),
    ]
    for value in sorted(by_semester):
        highlights.append(_highlight(f'{value} семестр', f'{by_semester[value]} дисциплин'))
    if direction:
        highlights.insert(0, _highlight('Направление', direction))

    issues = []
    if not entries:
        issues.append(_issue('empty', 'В плане не найдено ни одной дисциплины', 1,
                             note='Проверьте формат таблицы: нужны колонки семестров.'))
    if missing:
        issues.append(_issue('no_semester', 'Дисциплины без семестра', len(missing), missing[:CONFLICT_LIMIT]))
    if invalid:
        issues.append(_issue('bad_semester', 'Семестры вне диапазона 1–8', len(invalid), invalid[:CONFLICT_LIMIT]))
    if duplicates:
        issues.append(_issue('duplicates', 'Дисциплина встречается несколько раз',
                             len(duplicates), duplicates[:CONFLICT_LIMIT]))
    if gaps:
        issues.append(_issue(
            'gaps', 'Семестры без дисциплин', len(gaps),
            [f'{value} семестр' for value in gaps],
            note='Для сквозного плана бакалавриата это повод проверить разбор.',
        ))
    if suspects:
        issues.append(_issue('suspects', 'Подозрительные названия', len(suspects), suspects[:CONFLICT_LIMIT]))

    return {
        'kind': 'plan',
        'file': file_name,
        'source_note': '',
        'direction': direction,
        'generated_at': timezone.now().isoformat(timespec='seconds'),
        'stats': {
            'entries': len(entries),
            'disciplines': len(seen),
            'max_semester': max_semester,
            'by_semester': {str(key): value for key, value in sorted(by_semester.items())},
            'missing_semester': len(missing),
            'invalid_semester': len(invalid),
            'duplicates': len(duplicates),
        },
        'highlights': highlights,
        'issues': issues,
    }


def review_plan_file(path, use_llm=True):
    path = Path(path)
    if path.suffix.lower() == '.json':
        entries, direction = _plan_entries_from_json(path)
    else:
        entries = _plan_entries_from_table(path)
        try:
            from apps.education.plan_import import detect_direction

            direction = detect_direction(path.name)['direction']
        except (ValueError, KeyError):
            direction = ''
    review = _finalize(build_plan_facts(entries, path.name, direction), use_llm=use_llm)
    return review


def review_plan_source(source, use_llm=True):
    """Разбор уже импортированного плана: строки — из CurriculumEntry."""
    entries = [
        {'subject': item.subject, 'semesters': list(range(item.semester_from, item.semester_to + 1))}
        for item in source.entries.all()
    ]
    review = _finalize(build_plan_facts(entries, source.original_name, source.direction), use_llm=use_llm)
    review['source_note'] = f'план №{source.pk} от {source.uploaded_at:%d.%m.%Y %H:%M}'
    if source.entries_count and source.entries_count != len(entries):
        review['issues'].append(_issue(
            'entries_mismatch', 'Число строк плана в базе расходится с записью источника',
            abs(source.entries_count - len(entries)),
            [f'в источнике {source.entries_count}, в базе {len(entries)}'],
        ))
    return review


# --------------------------------------------------------------------------- #
# Текст для консоли и Markdown
# --------------------------------------------------------------------------- #

def review_to_text(review):
    """Человекочитаемый разбор для консоли."""
    lines = [f"ИИ-разбор: {review['file']} ({'расписание' if review['kind'] == 'schedule' else 'учебный план'})"]
    if review.get('source_note'):
        lines.append(f"Источник: {review['source_note']}")
    lines.append('')
    lines.append('Резюме:')
    if review.get('summary_points'):
        lines.extend(f'  • {point}' for point in review['summary_points'])
    else:
        note = review.get('llm', {}).get('note')
        lines.append(f'  — {note}' if note else '  — нет')
    lines.append('')
    lines.append('Факты:')
    lines.extend(f"  {item['label']}: {item['value']}" for item in review['highlights'])
    issues = [item for item in review['issues'] if item['count']]
    if issues:
        lines.append('')
        lines.append('Требует внимания:')
        for issue in issues:
            lines.append(f"  ⚠ {issue['title']}: {issue['count']}")
            lines.extend(f"      {example}" for example in issue['examples'])
    else:
        lines.append('')
        lines.append('Проблем не найдено.')
    return '\n'.join(lines)


def review_to_markdown(review):
    """Markdown-версия разбора для docs/ai-reviews."""
    kind = 'Расписание' if review['kind'] == 'schedule' else 'Учебный план'
    lines = [f"# ИИ-разбор · {kind} · {review['file']}", '']
    meta = [f"Сформирован: {review['generated_at'][:16].replace('T', ' ')}"]
    if review.get('source_note'):
        meta.append(review['source_note'])
    if review.get('direction'):
        meta.append(f"Направление: {review['direction']}")
    lines.append(' · '.join(meta))
    lines.append('')
    lines.append('## Резюме')
    if review.get('summary_points'):
        lines.extend(f'- {point}' for point in review['summary_points'])
    elif review.get('llm', {}).get('note') == 'резюме отключено':
        lines.append('_Резюме отключено при запуске._')
    else:
        lines.append('_Локальная модель недоступна — показаны только факты, посчитанные кодом._')
    lines.append('')
    lines.append('## Факты')
    lines.extend(f"- **{item['label']}:** {item['value']}" for item in review['highlights'])
    issues = [item for item in review['issues'] if item['count']]
    lines.append('')
    lines.append('## Требует внимания')
    if issues:
        for issue in issues:
            lines.append(f"### {issue['title']} — {issue['count']}")
            if issue.get('note'):
                lines.append(f"_{issue['note']}_")
            lines.extend(f'- {example}' for example in issue['examples'])
            lines.append('')
    else:
        lines.append('Проблем не найдено.')
    return '\n'.join(lines).rstrip() + '\n'


def review_filename(review, today=None):
    """Имя файла отчёта: <дата>-<файл>.md."""
    today = today or timezone.localdate()
    stem = os.path.splitext(review.get('file') or 'review')[0]
    stem = re.sub(r'\s+', '-', stem.strip())
    stem = re.sub(r'[^\w.\-]+', '', stem, flags=re.UNICODE).strip('.-') or 'review'
    return f'{today:%Y-%m-%d}-{stem[:80]}.md'
