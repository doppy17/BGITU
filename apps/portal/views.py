"""Личный кабинет: дашборд и зачётная книжка."""
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import redirect, render

from apps.accounts.forms import ProfileFactsForm
from apps.accounts.models import ProfileHistory, RoleRequest
from apps.accounts.performance import performance_by_semester, total_average
from apps.accounts.permissions import get_role, role_can
from apps.education.models import Assignment, Enrollment, Grade, Submission
from apps.schedule.import_service import WEEKDAYS
from apps.schedule.models import Bell, Lesson
from apps.schedule.services import parity_of_monday


def home(request):
    if request.user.is_authenticated:
        return redirect('portal:dashboard')
    return redirect('accounts:login')


def parity_for(day):
    """Чётность учебной недели — по номеру недели семестра, как в файле БГИТУ."""
    return parity_of_monday(day)


def parity():
    return parity_for(date.today())


def numeric_average(grades):
    values = [g.numeric for g in grades if g.numeric is not None]
    return round(sum(values) / len(values), 2) if values else None


@login_required
def stipendia_view(request):
    return render(request, 'portal/stipendia.html')


@login_required
def profile_view(request):
    """Профиль: данные студента, билет, контакты, успеваемость, история."""
    user = request.user
    profile = getattr(user, 'profile', None)
    facts_form = ProfileFactsForm(instance=profile, user=user) if profile else None
    return render(request, 'portal/profile.html', {
        'profile': profile,
        'facts_form': facts_form,
        'role': get_role(user),
        'performance': performance_by_semester(user),
        'total_average': total_average(user),
        'histories': ProfileHistory.objects.filter(user=user),
    })


@login_required
def dashboard(request):
    user = request.user
    profile = getattr(user, 'profile', None)
    group = profile.group if profile else None
    today = date.today()
    weekday = today.weekday() + 1
    week = parity()

    has_parity = bool(group) and Lesson.objects.filter(group=group).exclude(week='all').exists()
    week_filter = ['all', week] if has_parity else ['all']

    lessons = []
    if group:
        lessons = list(
            Lesson.objects.filter(group=group, weekday=weekday, week__in=week_filter).order_by('pair', 'subgroup')
        )
    bells = {b.pair: b for b in Bell.objects.filter(building='2')}
    today_lessons = [{'lesson': lesson, 'time': bells.get(lesson.pair)} for lesson in lessons]

    lessons_day = today
    lessons_week = week
    if not today_lessons and group:
        # Воскресенье и пустые дни: показываем ближайший учебный день, а не пустую карточку.
        for offset in range(1, 9):
            candidate = today + timedelta(days=offset)
            if candidate.weekday() > 5:
                continue
            candidate_week = parity_for(candidate)
            candidate_lessons = list(
                Lesson.objects.filter(
                    group=group, weekday=candidate.weekday() + 1,
                    week__in=['all', candidate_week] if has_parity else ['all'],
                ).order_by('pair', 'subgroup')
            )
            if candidate_lessons:
                lessons_day = candidate
                lessons_week = candidate_week
                today_lessons = [{'lesson': lesson, 'time': bells.get(lesson.pair)} for lesson in candidate_lessons]
                break

    grades = list(Grade.objects.filter(student=user).select_related('course', 'course__teacher'))
    average = numeric_average(grades)
    debts = [g for g in grades if g.is_fail]

    courses = (
        Enrollment.objects.filter(student=user).select_related('course', 'course__teacher', 'course__group')
        if not role_can(user, 'can_create_courses')
        else None
    )
    teaching = None
    if role_can(user, 'can_create_courses'):
        from apps.education.models import Course

        teaching = Course.objects.filter(teacher=user).annotate(students_count=Count('enrollments'))

    submitted_ids = Submission.objects.filter(student=user).values_list('assignment_id', flat=True)
    pending_assignments = (
        Assignment.objects.filter(course__enrollments__student=user)
        .exclude(id__in=submitted_ids)
        .select_related('course')
        .order_by('due_date')[:4]
    )
    recent_submissions = Submission.objects.filter(student=user).select_related('assignment', 'assignment__course')[:4]

    base = 2430
    academic = round(base * 1.8) if average and average >= 4.5 else base
    stipend = {'academic': academic, 'boosted': bool(average and average >= 4.5), 'total': academic}

    return render(request, 'portal/dashboard.html', {
        'profile': profile,
        'role': get_role(user),
        'group': group,
        'today': today,
        'weekday_name': WEEKDAYS.get(weekday, '').capitalize(),
        'has_parity': has_parity,
        'week_label': 'нечётная' if lessons_week == 'odd' else 'чётная',
        'today_lessons': today_lessons,
        'lessons_day': lessons_day,
        'lessons_is_today': lessons_day == today,
        'lessons_week_label': 'нечётная' if lessons_week == 'odd' else 'чётная',
        'average': average,
        'grades_count': len(grades),
        'debts': debts,
        'courses': courses,
        'teaching': teaching,
        'pending_assignments': pending_assignments,
        'recent_submissions': recent_submissions,
        'stipend': stipend,
        'pending_role_requests': RoleRequest.objects.filter(user=user, status=RoleRequest.STATUS_PENDING).count(),
    })


@login_required
def grades_view(request):
    user = request.user
    grades = list(
        Grade.objects.filter(student=user).select_related('course', 'course__teacher', 'course__group').order_by(
            'course__title', 'kind'
        )
    )
    by_course = {}
    for grade in grades:
        by_course.setdefault(grade.course, []).append(grade)
    rows = []
    for course, items in by_course.items():
        rows.append({'course': course, 'grades': items, 'average': numeric_average(items)})
    return render(request, 'portal/grades.html', {
        'rows': rows,
        'average': numeric_average(grades),
        'debts': [g for g in grades if g.is_fail],
        'grades_count': len(grades),
    })
