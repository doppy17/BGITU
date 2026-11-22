"""Просмотр расписания и импорт файла с сайта БГИТУ."""
import os
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files import File
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.permissions import role_can, role_required
from apps.education.models import CurriculumEntry, Group

from .models import Bell, Lesson, ScheduleImport
from .services import run_import

BGITU_SOURCE = 'https://bgitu.ru/studentu/raspisanie/ochnoe-obuchenie/'
DAY_SHORT = ['', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб']
DAY_FULL = ['', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']

# Понедельник первой недели осеннего семестра 2026/27.
# Позже начало семестра стоит брать из импорта/настроек, а не константой.
SEMESTER_START = date(2026, 8, 31)
SEMESTER_WEEKS = 18
PROGRAM_SEMESTERS = 8  # семестров в программе бакалавриата


def semester_week_of(day):
    return (day - SEMESTER_START).days // 7 + 1


def semester_monday(week):
    return SEMESTER_START + timedelta(weeks=week - 1)


def parity_of_monday(monday):
    """Календарная чётность недели (по ISO-номеру) — как в файле БГИТУ."""
    return 'odd' if monday.isocalendar()[1] % 2 else 'even'


def current_program_semester(course, today=None):
    """Номер семестра программы: курс 2, осень → 3-й семестр."""
    if not course:
        return None
    today = today or date.today()
    autumn = today.month in (9, 10, 11, 12, 1)
    offset = 1 if autumn else 2
    return min(PROGRAM_SEMESTERS, (course - 1) * 2 + offset)


def run_segments(weeks):
    """Сливает набор номеров недель в непрерывные отрезки [(start, end), ...]."""
    weeks = sorted(set(weeks))
    if not weeks:
        return []
    segments = []
    start = prev = weeks[0]
    for w in weeks[1:]:
        if w == prev + 1:
            prev = w
            continue
        segments.append((start, prev))
        start = prev = w
    segments.append((start, prev))
    return segments


@login_required
def schedule_view(request):
    profile = getattr(request.user, 'profile', None)
    can_choose = role_can(request.user, 'can_manage_schedule') or role_can(request.user, 'can_view_all')
    groups = Group.objects.all()
    group = profile.group if profile else None
    if can_choose and request.GET.get('group'):
        group = groups.filter(code=request.GET['group']).first() or group
    if group is None and can_choose:
        group = groups.first()

    # Номер семестровой недели: ?week=N, зажатый в границы семестра.
    current_week = semester_week_of(date.today())
    try:
        week = int(request.GET.get('week', current_week))
    except (TypeError, ValueError):
        week = current_week
    week = max(1, min(SEMESTER_WEEKS, week))
    direction = request.GET.get('dir')
    if direction not in ('next', 'prev'):
        direction = ''

    monday = semester_monday(week)
    sunday = monday + timedelta(days=6)
    parity = parity_of_monday(monday)
    week_label = 'нечётная' if parity == 'odd' else 'чётная'
    is_current = week == current_week

    lessons = []
    subgroups = []
    sub = (request.GET.get('sub') or '').strip()
    if group:
        all_lessons = Lesson.objects.filter(group=group)
        subgroups = sorted({item.subgroup for item in all_lessons if item.subgroup})
        if sub not in subgroups:
            sub = ''
        lessons = list(
            all_lessons.filter(week__in=['all', parity])
            .order_by('weekday', 'pair', 'subgroup')
        )
        if sub:
            lessons = [item for item in lessons if item.subgroup in ('', sub)]
    bells = {b.pair: b for b in Bell.objects.filter(building='2')}

    # Таблица: строки — пары, колонки — дни недели. В одной паре — один предмет:
    # параллельные занятия подгрупп (П/Г 1/2/3) схлопываем до подгруппы студента.
    user_subgroup = str(profile.subgroup) if profile and profile.subgroup is not None else None
    by_cell = {}
    for lesson in lessons:
        key = (lesson.weekday, lesson.pair)
        current = by_cell.get(key)
        if current is None:
            by_cell[key] = lesson
            continue
        # Приоритет: подгруппа пользователя → занятие без подгруппы → первое
        if user_subgroup and lesson.subgroup == user_subgroup and current.subgroup != user_subgroup:
            by_cell[key] = lesson
        elif not current.subgroup and lesson.subgroup:
            continue

    grid = []
    for pair in range(1, 7):
        bell = bells.get(pair)
        grid.append({
            'pair': pair,
            'time': f'{bell.start}–{bell.end}' if bell else '',
            'days': [
                [by_cell[(weekday, pair)]] if (weekday, pair) in by_cell else []
                for weekday in range(1, 7)
            ],
        })

    days_header = []
    for weekday in range(1, 7):
        day = monday + timedelta(days=weekday - 1)
        days_header.append({
            'short': DAY_SHORT[weekday],
            'full': DAY_FULL[weekday],
            'date': day,
            'is_today': day == date.today(),
        })

    # Учебный план: все дисциплины программы и семестры, где они читаются.
    direction_code = 'ist' if group and group.code.upper().startswith('ИСТ') else None
    curriculum = []
    if direction_code:
        entries = CurriculumEntry.objects.filter(code=direction_code)
        for index, entry in enumerate(entries):
            curriculum.append({
                'subject': entry.subject,
                'segments': run_segments(range(entry.semester_from, entry.semester_to + 1)),
                'color': index % 6,
            })
    program_semester = current_program_semester(group.course if group else None) if direction_code else None

    latest = ScheduleImport.objects.order_by('-imported_at').first()
    return render(request, 'schedule/schedule.html', {
        'group': group,
        'groups': groups,
        'can_choose_group': can_choose,
        'week': week,
        'week_label': week_label,
        'monday': monday,
        'sunday': sunday,
        'is_current': is_current,
        'direction': direction,
        'program_semesters': range(1, PROGRAM_SEMESTERS + 1),
        'program_semester': program_semester,
        'current_week': current_week,
        'days_header': days_header,
        'grid': grid,
        'curriculum': curriculum,
        'latest_import': latest,
        'subgroups': subgroups,
        'sub': sub,
        'can_import': role_can(request.user, 'can_manage_schedule') or role_can(request.user, 'can_view_all'),
    })


@login_required
@role_required('can_manage_schedule')
def import_view(request):
    if request.method == 'POST':
        upload = request.FILES.get('file')
        if upload is None:
            messages.error(request, 'Выберите файл расписания (.xls или .xlsx).')
            return redirect('schedule:import')
        ext = os.path.splitext(upload.name)[1].lower()
        if ext not in ('.xls', '.xlsx'):
            messages.error(request, 'Поддерживаются только .xls и .xlsx.')
            return redirect('schedule:import')
        if upload.size > 15 * 1024 * 1024:
            messages.error(request, 'Файл больше 15 МБ.')
            return redirect('schedule:import')
        record = ScheduleImport.objects.create(
            file=upload,
            original_name=upload.name,
            source_url=request.POST.get('source_url', BGITU_SOURCE).strip() or BGITU_SOURCE,
            imported_by=request.user,
        )
        try:
            run_import(record, source_url=record.source_url)
        except ValueError as exc:
            record.note = f'ошибка импорта: {exc}'[:255]
            record.save(update_fields=['note'])
            messages.error(request, f'Не удалось разобрать файл: {exc}')
            return redirect('schedule:import')
        messages.success(
            request,
            f'Расписание обновлено: занятий — {record.lessons_count}, групп — {record.groups_count}.',
        )
        return redirect(f"{reverse('schedule:schedule')}?group={request.POST.get('group', '')}")

    return render(request, 'schedule/import.html', {
        'imports': ScheduleImport.objects.select_related('imported_by')[:20],
        'source_url': BGITU_SOURCE,
    })


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
