"""Просмотр расписания и импорт файла с сайта БГИТУ."""
import os
from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files import File
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import role_can, role_required
from apps.education.curriculum import plan_code_for_group, resolve_curriculum
from apps.education.models import Group, PlanSource

from .models import Bell, Lesson, ScheduleImport
from .services import (
    SEMESTER_WEEKS,
    detect_institute,
    nearest_lesson,
    parity_of_monday,
    resolve_lessons,
    run_import,
    semester_monday,
    semester_week_of,
    visible_lessons,
)

BGITU_SOURCE = 'https://bgitu.ru/studentu/raspisanie/ochnoe-obuchenie/'
DAY_SHORT = ['', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб']
DAY_FULL = ['', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']

PROGRAM_SEMESTERS = 8  # семестров в программе бакалавриата


def now_local():
    """Текущие дата и время в часовом поясе проекта (МСК).

    Отдельная функция — чтобы «сейчас» можно было подменить в тестах и чтобы
    все решения «какая пара идёт» считались по одному моменту времени.
    """
    return timezone.localtime(timezone.now())


def current_program_semester(course, today=None):
    """Номер семестра программы: курс 2, осень → 3-й семестр."""
    if not course:
        return None
    today = today or date.today()
    autumn = today.month in (9, 10, 11, 12, 1)
    offset = 1 if autumn else 2
    return min(PROGRAM_SEMESTERS, (course - 1) * 2 + offset)


SUBGROUP_ALIASES = {'1': 'а', '2': 'б'}


def subgroup_code(value):
    """Подгруппа в одном виде: «1»/«2» из файла = «а»/«б» в интерфейсе."""
    value = (value or '').strip().lower()
    return SUBGROUP_ALIASES.get(value, value)


def teacher_sort_key(name):
    """Ключ сортировки ФИО по фамилии.

    В файлах БГИТУ ФИО идут «Фамилия И.О.», поэтому фамилия — первое слово.
    Регистр и «ё» не влияют на порядок: «Ёлкина» стоит рядом с «Елкиной».
    """
    parts = name.split()
    surname = parts[0] if parts else name
    return (surname.casefold().replace('ё', 'е'), name.casefold().replace('ё', 'е'))


def teacher_name_key(value):
    """ФИО для сравнения: ё→е, без регистра, точек и лишних пробелов."""
    value = (value or '').replace('ё', 'е').replace('Ё', 'Е').casefold()
    return ' '.join(value.replace('.', ' ').split())


def same_teacher(profile_name, lesson_name):
    """Похоже ли ФИО из профиля на ФИО в занятии.

    В файлах БГИТУ ФИО сокращённые — «Иванов И.О.», а в профиле полные —
    «Иванов Иван» (отчество в User не хранится). Поэтому сравниваем фамилию и
    инициалы: точное совпадение нормализованных строк, иначе — фамилия плюс
    инициалы, где сокращённая запись является префиксом полной.
    """
    left = teacher_name_key(profile_name).split()
    right = teacher_name_key(lesson_name).split()
    if not left or not right or left[0] != right[0]:
        return False
    left_initials = ''.join(part[:1] for part in left[1:])
    right_initials = ''.join(part[:1] for part in right[1:])
    if not left_initials or not right_initials:
        return False
    if left_initials == right_initials:
        return True
    # «Иванов Иван» (и) ↔ «Иванов И.О.» (ио): сравниваем по имени.
    if len(left_initials) == 1 or len(right_initials) == 1:
        return left_initials.startswith(right_initials) or right_initials.startswith(left_initials)
    return False


def division_subgroups(lessons):
    """Подгруппы группы, если в данных есть обе метки «а» и «б».

    Метки проставляет только парсер и только для групп, у которых в шапке
    файла есть подстолбцы «а»/«б». Поэтому наличие обеих меток — достаточный
    признак деления, даже если в конкретном слоте подгруппы учатся в разные
    недели.
    """
    present = sorted({
        subgroup_code(lesson.subgroup) for lesson in lessons if subgroup_code(lesson.subgroup)
    })
    return present if len(present) >= 2 else []


def schedule_context(request):
    """Контекст страницы расписания — общий для страницы и AJAX-фрагмента.

    Выбор группы запоминается в сессии, выбор преподавателя — отдельно для
    каждой группы (schedule_teacher_by_group): GET-параметр сильнее
    сохранённого значения, сохранённое — сильнее «умного» дефолта. Поэтому
    возврат на группу восстанавливает её преподавателя, а переход на другую
    группу показывает преподавателя, который ведёт у неё пару сейчас.
    """
    profile = getattr(request.user, 'profile', None)
    role = getattr(profile, 'role', None) if profile else None
    # Роль «преподаватель»: код роли teacher либо право создавать курсы без
    # права менять расписание (деканат/разработчик остаются по ту сторону).
    is_teacher_role = bool(role) and (
        role.code == 'teacher'
        or (role_can(request.user, 'can_create_courses')
            and not role_can(request.user, 'can_manage_schedule'))
    )
    can_choose = role_can(request.user, 'can_manage_schedule') or role_can(request.user, 'can_view_all')
    # Преподавателю выбор группы нужен и без права менять расписание: блок 03
    # показывает расписание выбранной группы.
    if is_teacher_role:
        can_choose = True
    # Один момент «сейчас» на весь запрос: какая неделя текущая, какая пара
    # идёт (для преподавателя по умолчанию) и где сегодняшняя колонка.
    now = now_local()
    today = now.date()
    groups = Group.objects.all()
    group = profile.group if profile else None
    # Порядок выбора группы: ?group= → сохранённый в сессии → группа профиля →
    # первая в списке. Сохранённый выбор не сильнее явного GET: ссылку с
    # параметрами можно переслать, и она откроет именно эту группу.
    saved_group = (request.session.get('schedule_group') or '').strip()
    if can_choose and request.GET.get('group'):
        group = groups.filter(code=request.GET['group']).first() or group
    elif can_choose and saved_group:
        group = groups.filter(code=saved_group).first() or group
    if group is None and can_choose:
        group = groups.first()
    if can_choose and group:
        request.session['schedule_group'] = group.code

    # Номер семестровой недели: ?week=N, зажатый в границы семестра.
    # ?week=odd|even — явная чётность (кнопки/скролл без пересчёта номера):
    # берём ближайшую неделю семестра нужной чётности относительно текущей.
    current_week = semester_week_of(today)
    raw_week = (request.GET.get('week') or '').strip().lower()
    explicit_parity = raw_week if raw_week in (Lesson.WEEK_ODD, Lesson.WEEK_EVEN) else ''
    # ?parity= — то же самое для ссылок, которые сохраняют номер недели.
    if not explicit_parity:
        wanted = (request.GET.get('parity') or '').strip().lower()
        explicit_parity = wanted if wanted in (Lesson.WEEK_ODD, Lesson.WEEK_EVEN) else ''
    if explicit_parity:
        base = max(1, min(SEMESTER_WEEKS, current_week))
        week = base if parity_of_monday(semester_monday(base)) == explicit_parity else base + 1
        week = max(1, min(SEMESTER_WEEKS, week))
    else:
        try:
            week = int(raw_week or current_week)
        except (TypeError, ValueError):
            week = current_week
        week = max(1, min(SEMESTER_WEEKS, week))
    direction = request.GET.get('dir')
    if direction not in ('next', 'prev'):
        direction = ''

    monday = semester_monday(week)
    sunday = monday + timedelta(days=6)
    # Чётность определяется по КАЛЕНДАРЮ выбранной недели: листание недель
    # само решает, показывать расписание нечётной или чётной недели.
    parity = parity_of_monday(monday)
    week_label = 'нечётная' if parity == 'odd' else 'чётная'
    is_current = week == current_week

    # Подгруппа: ?sub= → сохранённый выбор в профиле → «без подгруппы».
    lessons = []
    subgroups = []
    subgroups_by_day = {}
    # ?sub= — подгруппа; ?sub=odd|even сюда не попадает (это чётность недели).
    sub = subgroup_code(request.GET.get('sub'))
    if sub in (Lesson.WEEK_ODD, Lesson.WEEK_EVEN):
        sub = ''
    saved_sub = subgroup_code(profile.subgroup) if profile else ''
    has_parity = False
    has_subgroups = False
    if group:
        # Выборка с приоритетом: если несколько файлов дают занятие в одном
        # слоте (группа/день/пара/подгруппа), видно занятие самого свежего файла.
        all_lessons = visible_lessons(group)
        has_parity = any(item.week != Lesson.WEEK_ALL for item in all_lessons)
        # Пилюли выбора и метки подгрупп — только если в данных ЭТОЙ группы
        # есть обе метки «а»/«б» (парсер ставит их лишь для групп с
        # подстолбцами а/б в шапке файла).
        subgroups = division_subgroups(all_lessons)
        has_subgroups = bool(subgroups)
        # Если у группы нет деления (или выбранной подгруппы в ней нет),
        # сохранённый выбор профиля игнорируется — показываем всю группу.
        if sub not in subgroups:
            sub = saved_sub if saved_sub in subgroups else ''
        # В таблице — только занятия выбранной недели: общие (week=all) и её
        # чётности. Пары другой недели не показываются и не занимают место.
        lessons = [item for item in all_lessons if item.week in (Lesson.WEEK_ALL, parity)]
        if sub:
            lessons = [item for item in lessons if subgroup_code(item.subgroup) in ('', sub)]
        if has_subgroups:
            for item in lessons:
                code = subgroup_code(item.subgroup)
                if code:
                    subgroups_by_day.setdefault(item.weekday, set()).add(code)
            subgroups_by_day = {day: sorted(values) for day, values in subgroups_by_day.items()}
    # Звонки по корпусам: время пары зависит от корпуса занятия (1-й/2-й).
    bells = {(bell.building, bell.pair): bell for bell in Bell.objects.all()}

    # Блок 03: расписание преподавателя — те же загруженные занятия, но фильтр
    # по ФИО. Список ФИО — из данных (distinct, сортировка по фамилии);
    # неизвестное ?teacher= не ошибка — работает выбор по умолчанию.
    teacher_names = sorted(
        {
            name.strip()
            for name in Lesson.objects.exclude(teacher_name='').values_list('teacher_name', flat=True)
            if name.strip()
        },
        key=teacher_sort_key,
    )
    requested_teacher = (request.GET.get('teacher') or '').strip()
    # Преподавателя помним ОТДЕЛЬНО ДЛЯ КАЖДОЙ ГРУППЫ: при переходе на другую
    # группу показывается её «умный» дефолт (кто ведёт пару сейчас), а возврат
    # на прежнюю восстанавливает выбранного ранее преподавателя.
    saved_by_group = request.session.get('schedule_teacher_by_group')
    if not isinstance(saved_by_group, dict):
        saved_by_group = {}
    legacy_teacher = (request.session.get('schedule_teacher') or '').strip()
    if legacy_teacher:
        # Старый формат — одно ФИО на всю сессию: привязываем его к группе,
        # которая была выбрана, чтобы выбор не потерялся при обновлении кода.
        request.session.pop('schedule_teacher', None)
        if group and legacy_teacher in teacher_names:
            saved_by_group[group.code] = legacy_teacher
        request.session['schedule_teacher_by_group'] = saved_by_group
    saved_teacher = saved_by_group.get(group.code, '') if group else ''
    # Преподаватель по умолчанию — тот, кто ведёт у выбранной группы пару
    # сейчас (или ближайшую следующую на этой неделе). Порядок: явный ?teacher=
    # → сохранённый для этой группы → дефолт; первый по алфавиту — только если
    # у группы на неделе занятий нет.
    default_lesson, default_reason = nearest_lesson(lessons, now, bells)
    default_teacher = (default_lesson.teacher_name or '').strip() if default_lesson else ''
    # Свой преподаватель: ФИО из профиля сопоставляем с ФИО в занятиях —
    # в файлах «Фамилия И.О.», в профиле полное имя без отчества.
    self_teacher = ''
    if is_teacher_role:
        candidates = []
        if profile:
            candidates.append(profile.full_name)
        candidates.append(f'{request.user.last_name} {request.user.first_name}'.strip())
        for name in teacher_names:
            if any(same_teacher(candidate, name) for candidate in candidates if candidate):
                self_teacher = name
                break
    if requested_teacher in teacher_names:
        teacher, teacher_source = requested_teacher, 'query'
        if group:
            saved_by_group[group.code] = teacher
            request.session['schedule_teacher_by_group'] = saved_by_group
    elif saved_teacher in teacher_names:
        teacher, teacher_source = saved_teacher, 'group'
    elif self_teacher:
        teacher, teacher_source = self_teacher, 'self'
    elif default_teacher in teacher_names:
        teacher, teacher_source = default_teacher, default_reason
    else:
        teacher, teacher_source = (teacher_names[0] if teacher_names else ''), 'first'
    # Откуда взят преподаватель по умолчанию — для подписи в блоке 03 и отладки.
    teacher_default = {
        'reason': teacher_source,
        'teacher': teacher,
        'now': now,
        'is_current_week': week == current_week,
        'weekday_name': DAY_FULL[default_lesson.weekday].lower() if default_lesson else '',
        'pair': default_lesson.pair if default_lesson else None,
        'subject': default_lesson.subject if default_lesson else '',
        'group_code': group.code if group and default_lesson else '',
        'is_self': teacher_source == 'self',
    }
    teacher_grid = []

    # Таблица: строки — пары, колонки — дни недели. В ячейке — занятия только
    # выбранной недели: одна карточка, а если у «а» и «б» РАЗНЫЕ пары —
    # две карточки рядом: слева «а», справа «б».
    by_cell = {}
    for lesson in lessons:
        by_cell.setdefault((lesson.weekday, lesson.pair), []).append(lesson)

    def row_lessons(items):
        """Карточки ячейки: общая — одна, иначе по подгруппам."""
        common = [item for item in items if not subgroup_code(item.subgroup)]
        if common:
            exact = next((item for item in common if item.week == parity), None)
            return [exact or common[0]]
        by_subgroup = {}
        for item in items:
            code = subgroup_code(item.subgroup)
            current = by_subgroup.get(code)
            # Точная неделя важнее «каждой недели» для той же подгруппы.
            if current is None or (current.week == Lesson.WEEK_ALL and item.week != Lesson.WEEK_ALL):
                by_subgroup[code] = item
        return [by_subgroup[code] for code in sorted(by_subgroup)]

    def build_cell(items):
        if not items:
            return {'lessons': [], 'split': False}
        cards = row_lessons(items)
        return {'lessons': cards, 'split': len(cards) > 1}

    # Число строк таблицы — из данных выбранной недели: последняя пара, на
    # которой есть занятие у любой группы в эту неделю. Пустых строк нет.
    # Разрешаем конфликты источников по всей базе (без фильтра недели):
    # в слоте побеждает самый свежий файл, и этот же набор использует блок 03.
    resolved_pool = resolve_lessons(Lesson.objects.select_related('source', 'group'))
    week_pool = [item for item in resolved_pool if item.week in (Lesson.WEEK_ALL, parity)]
    week_max = max((item.pair for item in week_pool), default=0)
    group_max = max((item.pair for item in lessons), default=0)
    max_pair = max(week_max, group_max)
    pairs = range(1, max_pair + 1) if max_pair else range(0)

    # Блок 03: занятия выбранного преподавателя на этой неделе — из того же
    # разрешённого набора, поэтому приоритет источников общий: если слот группы
    # занял более свежий файл с другим преподавателем, старая пара в расписании
    # преподавателя не показывается.
    if teacher:
        teacher_lessons = [
            item for item in week_pool if item.teacher_name == teacher
        ]

        def teacher_cards(items):
            """Карточки ячейки преподавателя: по одной на группу (и подгруппу).

            У преподавателя в одном слоте могут стоять пары разных групп —
            они показываются стопкой, поэтому группа указана в каждой карточке.
            """
            picked = {}
            for item in items:
                key = (item.group.code, subgroup_code(item.subgroup), item.subject)
                current = picked.get(key)
                # Точная неделя важнее «каждой недели» для той же пары.
                if current is None or (current.week == Lesson.WEEK_ALL and item.week != Lesson.WEEK_ALL):
                    picked[key] = item
            return [picked[key] for key in sorted(picked)]

        teacher_by_cell = {}
        for lesson in teacher_lessons:
            teacher_by_cell.setdefault((lesson.weekday, lesson.pair), []).append(lesson)
        # Строки — только пары самого преподавателя: пустые строки не занимают место.
        teacher_max = max((item.pair for item in teacher_lessons), default=0)
        for pair in range(1, teacher_max + 1):
            days = []
            for weekday in range(1, 7):
                cards = teacher_cards(teacher_by_cell.get((weekday, pair), []))
                for item in cards:
                    bell = bells.get((item.building or '2', pair)) or bells.get(('2', pair))
                    item.time_label = f'{bell.start}–{bell.end}' if bell else ''
                days.append({
                    'lessons': cards,
                    'split': len(cards) > 1,
                    'is_today': monday + timedelta(days=weekday - 1) == today,
                })
            teacher_grid.append({'pair': pair, 'days': days})

    grid = []
    for pair in pairs:
        days = []
        for weekday in range(1, 7):
            cell = build_cell(by_cell.get((weekday, pair), []))
            for item in cell['lessons']:
                bell = bells.get((item.building or '2', pair)) or bells.get(('2', pair))
                item.time_label = f'{bell.start}–{bell.end}' if bell else ''
            cell['is_today'] = monday + timedelta(days=weekday - 1) == today
            days.append(cell)
        grid.append({'pair': pair, 'days': days})

    days_header = []
    for weekday in range(1, 7):
        day = monday + timedelta(days=weekday - 1)
        days_header.append({
            'short': DAY_SHORT[weekday],
            'full': DAY_FULL[weekday],
            'date': day,
            'is_today': day == today,
            'subgroups': subgroups_by_day.get(weekday, []),
        })

    # Ссылки недель и подгрупп несут группу — переключение не сбрасывает выбор.
    group_qs = f'&group={group.code}' if can_choose and group else ''
    week_links = {
        'prev': f'?week={week - 1}{group_qs}&dir=prev' if week > 1 else '',
        'next': f'?week={week + 1}{group_qs}&dir=next' if week < SEMESTER_WEEKS else '',
    }
    # Явный выбор недели в выпадашке: ?parity=odd|even сохраняет группу и подгруппу.
    parity_query = f'{group_qs}&sub={sub}' if sub else group_qs
    parity_links = {}
    for value, monday_of in (
        (Lesson.WEEK_ODD, next((semester_monday(n) for n in range(1, SEMESTER_WEEKS + 1)
                                if parity_of_monday(semester_monday(n)) == Lesson.WEEK_ODD), None)),
        (Lesson.WEEK_EVEN, next((semester_monday(n) for n in range(1, SEMESTER_WEEKS + 1)
                                 if parity_of_monday(semester_monday(n)) == Lesson.WEEK_EVEN), None)),
    ):
        number = semester_week_of(monday_of) if monday_of else 1
        parity_links[value] = f'?week={number}{parity_query}'
    sub_links = {'all': f'?week={week}{group_qs}'}
    for item in subgroups:
        sub_links[item] = f'?week={week}{group_qs}&sub={item}'
    no_lessons = bool(group) and not lessons

    # Учебный план: все дисциплины программы и семестры, где они читаются.
    # Направление — из Group.program (09.03.02 → ist), иначе префикс группы.
    direction_code = plan_code_for_group(group) if group else ''
    curriculum = resolve_curriculum(direction_code) if direction_code else []
    program_semester = current_program_semester(group.course if group else None) if direction_code else None

    sources = list(ScheduleImport.objects.select_related('imported_by').order_by('-imported_at'))
    latest = sources[0] if sources else None
    plan_sources = list(PlanSource.objects.select_related('uploaded_by').order_by('-uploaded_at'))
    latest_plan = plan_sources[0] if plan_sources else None
    # Канонический адрес состояния: его кладёт в history.pushState AJAX-слой,
    # поэтому скопированная ссылка открывает тот же выбор группы и преподавателя.
    query = [('group', group.code)] if can_choose and group else []
    if teacher and teacher_source in ('query', 'group'):
        query.append(('teacher', teacher))
    query.append(('week', week))
    if sub:
        query.append(('sub', sub))
    page_url = f"{reverse('schedule:schedule')}?{urlencode(query)}"
    return {
        'page_url': page_url,
        'is_teacher_role': is_teacher_role,
        'teacher_first': is_teacher_role,
        # Номера блоков: у преподавателя первым идёт его расписание (01), блок
        # группы — третьим (03); у студентов и сотрудников — наоборот.
        'group_block_num': '03' if is_teacher_role else '01',
        'teacher_block_num': '01' if is_teacher_role else '03',
        'group': group,
        'groups': groups,
        'can_choose_group': can_choose,
        'week': week,
        'week_label': week_label,
        # «на нечётной неделе» — предложный падеж для подписей пустых состояний.
        'week_label_prep': 'нечётной' if parity == 'odd' else 'чётной',
        'parity': parity,
        'has_parity': has_parity,
        'week_links': week_links,
        'parity_links': parity_links,
        'parity_odd': Lesson.WEEK_ODD,
        'parity_even': Lesson.WEEK_EVEN,
        'monday': monday,
        'sunday': sunday,
        'is_current': is_current,
        'direction': direction,
        'program_semesters': range(1, PROGRAM_SEMESTERS + 1),
        'program_semester': program_semester,
        'current_week': current_week,
        'days_header': days_header,
        'grid': grid,
        'pairs': pairs,
        'max_pair': max_pair,
        'teachers': teacher_names,
        'teacher': teacher,
        'teacher_grid': teacher_grid,
        'teacher_default': teacher_default,
        'curriculum': curriculum,
        'latest_import': latest,
        'sources': sources,
        'plan_sources': plan_sources,
        'latest_plan': latest_plan,
        'subgroups': subgroups,
        'has_subgroups': has_subgroups,
        'sub': sub,
        'sub_links': sub_links,
        'no_lessons': no_lessons,
        'can_import': role_can(request.user, 'can_manage_schedule') or role_can(request.user, 'can_view_all'),
    }


@login_required
def schedule_view(request):
    """Полная страница расписания (без JS работает как обычная форма)."""
    return render(request, 'schedule/schedule.html', schedule_context(request))


@login_required
def schedule_fragment(request):
    """Только блоки 01/03 и навигация недели — для подмены без перезагрузки.

    Ответ — фрагмент без <html>/<head>/<footer>: AJAX-слой заменяет им
    содержимое блоков на странице. Канонический адрес состояния (page_url уже
    percent-encoded: в группе и ФИО кириллица) уезжает в заголовке
    X-Schedule-Url — его и ставит history.pushState.
    """
    context = schedule_context(request)
    response = render(request, 'schedule/_fragment.html', context)
    response['X-Schedule-Url'] = context['page_url']
    response['Cache-Control'] = 'no-store'
    return response


MAX_UPLOAD_SIZE = 15 * 1024 * 1024


def schedule_redirect(group_code=''):
    url = reverse('schedule:schedule')
    return f'{url}?group={group_code}' if group_code else url


@login_required
@role_required('can_manage_schedule')
def import_view(request):
    """Загрузка нескольких файлов расписания сразу.

    Каждый файл — отдельный источник занятий. Файлы разных институтов
    сохраняются рядом; повторная загрузка файла того же охвата заменяет
    занятия предыдущего (см. services.run_import).
    """
    if request.method == 'POST':
        uploads = request.FILES.getlist('file')
        if not uploads:
            messages.error(request, 'Выберите файлы расписания (.xls или .xlsx).')
            return redirect('schedule:import')

        group_code = request.POST.get('group', '')
        source_url = request.POST.get('source_url', BGITU_SOURCE).strip() or BGITU_SOURCE
        imported, errors, total_lessons = [], [], 0

        for upload in uploads:
            extension = os.path.splitext(upload.name)[1].lower()
            if extension not in ('.xls', '.xlsx'):
                errors.append(f'{upload.name}: поддерживаются только .xls и .xlsx.')
                continue
            if upload.size > MAX_UPLOAD_SIZE:
                errors.append(f'{upload.name}: файл больше 15 МБ.')
                continue
            record = ScheduleImport.objects.create(
                file=upload,
                original_name=upload.name[:255],
                institute=detect_institute(upload.name),
                size=upload.size,
                source_url=source_url,
                imported_by=request.user,
            )
            try:
                run_import(record, source_url=source_url)
            except Exception as exc:  # один битый файл не отменяет остальные
                record.note = f'ошибка импорта: {exc}'[:255]
                record.save(update_fields=['note'])
                errors.append(f'{upload.name}: не удалось разобрать файл — {exc}')
                continue
            imported.append(record)
            total_lessons += record.lessons_count

        if imported:
            names = ', '.join(item.institute or item.original_name for item in imported)
            messages.success(
                request,
                f'Загружено файлов: {len(imported)} · занятий: {total_lessons} ({names}).',
            )
        for error in errors:
            messages.error(request, error)
        return redirect(schedule_redirect(group_code))

    return render(request, 'schedule/import.html', {
        'imports': ScheduleImport.objects.select_related('imported_by')[:20],
        'source_url': BGITU_SOURCE,
    })


@login_required
@require_POST
@role_required('can_manage_schedule')
def delete_source(request, pk):
    """Удалить загруженный файл вместе со всеми его занятиями."""
    record = get_object_or_404(ScheduleImport, pk=pk)
    name = record.original_name
    with transaction.atomic():
        Lesson.objects.filter(source=record).delete()
        record.delete()
    messages.success(request, f'Файл «{name}» удалён вместе с занятиями.')
    return redirect(schedule_redirect(request.POST.get('group', '')))


@login_required
@require_POST
@role_required('can_manage_schedule')
def import_from_server_file(request):
    """Импорт приложенного в проект файла БГИТУ (для демо/первичной заливки)."""
    from django.conf import settings

    path = request.POST.get('path', '')
    allowed_root = (settings.BASE_DIR / 'fixtures' / 'schedule').resolve()
    full_path = (settings.BASE_DIR / path).resolve()
    # Только файлы расписания внутри fixtures/schedule — без выхода за пределы папки.
    if not path or os.path.isabs(path) or not full_path.is_relative_to(allowed_root):
        messages.error(request, 'Некорректный путь к файлу.')
        return redirect('schedule:import')
    if full_path.suffix.lower() not in ('.xls', '.xlsx'):
        messages.error(request, 'Поддерживаются только .xls и .xlsx.')
        return redirect('schedule:import')
    if not full_path.is_file():
        messages.error(request, 'Файл не найден.')
        return redirect('schedule:import')
    with full_path.open('rb') as handle:
        record = ScheduleImport(
            original_name=full_path.name,
            source_url=BGITU_SOURCE,
            imported_by=request.user,
        )
        record.file.save(full_path.name, File(handle), save=True)
    try:
        run_import(record, source_url=BGITU_SOURCE)
    except ValueError as exc:
        record.note = f'ошибка импорта: {exc}'[:255]
        record.save(update_fields=['note'])
        messages.error(request, f'Не удалось разобрать файл: {exc}')
        return redirect('schedule:import')
    messages.success(request, f'Импортировано занятий: {record.lessons_count}.')
    return redirect('schedule:schedule')


def can_download_source(user, record):
    """Скачивание источника: нужно право импорта и авторство файла (staff — любой).

    Файл расписания — исходник импорта, поэтому отдаём его только тем, кто
    вправе менять расписание, и только авторам загрузки; staff и superuser
    видят все источники.
    """
    if not role_can(user, 'can_manage_schedule'):
        return False
    if user.is_superuser or user.is_staff:
        return True
    return record.imported_by_id is not None and record.imported_by_id == user.pk


@login_required
def download_source(request, pk):
    """Отдать загруженный файл расписания как вложение с исходным именем."""
    record = get_object_or_404(ScheduleImport, pk=pk)
    if not can_download_source(request.user, record):
        raise PermissionDenied
    if not record.file or not record.file.storage.exists(record.file.name):
        raise Http404('Файл расписания не найден на диске.')
    filename = record.original_name or os.path.basename(record.file.name)
    return FileResponse(record.file.open('rb'), as_attachment=True, filename=filename)
