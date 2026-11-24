"""Успеваемость студента по семестрам — данные для раздела «Профиль».

Семестр берётся из ``education.Course.semester``. В зачётку попадают только
курсы, по которым уже есть оценки; средний балл считается по числовым
оценкам 2–5, зачёты и долги учитываются отдельно.
"""
from apps.education.models import Grade


def _teacher_name(teacher):
    if teacher is None:
        return ''
    name = f'{teacher.last_name} {teacher.first_name}'.strip()
    return name or teacher.username


def _average(grades):
    values = [grade.numeric for grade in grades if grade.numeric is not None]
    return round(sum(values) / len(values), 2) if values else None


def performance_by_semester(user):
    """Список семестров студента по возрастанию номера.

    Каждый элемент::

        {
            'semester': 1,
            'courses': [{'title', 'teacher', 'kind', 'value', 'date'}, ...],
            'average': 4.5,       # только оценки 2–5, None если числовых нет
            'credits': 1,         # количество «зачтено»
            'exams': 2,           # количество экзаменов
            'has_debt': False,    # есть «2» или «не зачтено»
        }
    """
    grades = list(
        Grade.objects.filter(student=user)
        .select_related('course', 'course__teacher')
        .order_by('course__semester', 'course__title', 'kind')
    )

    by_semester = {}
    for grade in grades:
        by_semester.setdefault(grade.course.semester, []).append(grade)

    semesters = []
    for semester in sorted(by_semester):
        items = by_semester[semester]
        semesters.append({
            'semester': semester,
            'courses': [
                {
                    'title': grade.course.title,
                    'teacher': _teacher_name(grade.course.teacher),
                    'kind': grade.kind,
                    'value': grade.value,
                    'date': grade.date,
                }
                for grade in items
            ],
            'average': _average(items),
            'credits': sum(1 for grade in items if grade.value == 'зачтено'),
            'exams': sum(1 for grade in items if grade.kind == Grade.KIND_EXAM),
            'has_debt': any(grade.value in ('2', 'не зачтено') for grade in items),
        })
    return semesters


def total_average(user):
    """Средний балл по всем числовым оценкам 2–5 за всё время обучения."""
    return _average(Grade.objects.filter(student=user))


def grade_statistics(user):
    """Сводка по числовым оценкам: средний балл и доли «5»/«4» в процентах.

    Зачёты и «не зачтено» в расчёт не входят: без числовых оценок
    ``average`` равен ``None``, доли — 0, исключений нет.
    """
    values = list(Grade.objects.filter(student=user).values_list('value', flat=True))
    numeric = [int(value) for value in values if value in ('2', '3', '4', '5')]
    total = len(numeric)
    if not total:
        return {'average': None, 'excellent_share': 0, 'good_share': 0, 'numeric_count': 0}
    return {
        'average': round(sum(numeric) / total, 2),
        'excellent_share': round(numeric.count(5) * 100 / total, 1),
        'good_share': round(numeric.count(4) * 100 / total, 1),
        'numeric_count': total,
    }
