"""Учебный процесс: курсы, задания, работы, посещаемость, ведомости."""
import datetime
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Profile
from apps.accounts.permissions import role_can, role_required

from .models import Assignment, Attendance, Course, Enrollment, Grade, Submission, Group
from .models import ControlPoint, PointMark, PointWeight, WORK_CHOICES
from .rating import VERDICT_NO, VERDICT_WAIT, VERDICT_YES, course_rating, point_weights, point_result

ALLOWED_WORK_EXT = {'.pdf', '.doc', '.docx', '.zip', '.rar', '.txt', '.py', '.xlsx', '.png', '.jpg', '.jpeg'}
MAX_WORK_SIZE = 20 * 1024 * 1024


def can_access_course(user, course):
    if role_can(user, 'can_view_all'):
        return True
    if course.teacher_id == user.id:
        return True
    return Enrollment.objects.filter(course=course, student=user).exists()


def is_course_teacher(user, course):
    return course.teacher_id == user.id or role_can(user, 'can_view_all')


def parse_date(value, default=None):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return default


@login_required
def course_list(request):
    if role_can(request.user, 'can_view_all'):
        courses = Course.objects.select_related('group', 'teacher')
        mode = 'all'
    elif role_can(request.user, 'can_create_courses'):
        courses = Course.objects.filter(teacher=request.user).select_related('group', 'teacher')
        mode = 'teaching'
    else:
        courses = Course.objects.filter(enrollments__student=request.user).select_related('group', 'teacher')
        mode = 'student'
    courses = list(courses.annotate(
        students_count=Count('enrollments', distinct=True),
        assignments_count=Count('assignments', distinct=True),
        sessions_count=Count('attendance__date', distinct=True),
    ))
    if mode == 'student':
        summary = {}
        for row in Attendance.objects.filter(course__in=courses, student=request.user).values('course_id', 'status').annotate(n=Count('id')):
            summary.setdefault(row['course_id'], {})[row['status']] = row['n']
        for course in courses:
            own = summary.get(course.id, {})
            total = sum(own.values())
            attended = own.get(Attendance.STATUS_PRESENT, 0) + own.get(Attendance.STATUS_LATE, 0)
            course.my_attendance_percent = round(attended / total * 100) if total else None
            course.my_absent = own.get(Attendance.STATUS_ABSENT, 0)
            course.my_sessions = total
    return render(request, 'education/courses.html', {
        'courses': courses,
        'mode': mode,
        'can_create': role_can(request.user, 'can_create_courses'),
        'coursework_count': sum(1 for c in courses if c.has_coursework),
    })


@login_required
@role_required('can_create_courses')
def course_create(request):
    groups = Group.objects.all()
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        code = request.POST.get('code', '').strip().upper()
        group_id = request.POST.get('group')
        semester = request.POST.get('semester', '1')
        description = request.POST.get('description', '').strip()
        group = Group.objects.filter(pk=group_id).first()
        errors = []
        if len(title) < 3:
            errors.append('Название курса — минимум 3 символа.')
        if not code:
            errors.append('Укажите код курса.')
        if group is None:
            errors.append('Выберите группу.')
        if not semester.isdigit() or not 1 <= int(semester) <= 12:
            errors.append('Семестр — число от 1 до 12.')
        attestation_count = request.POST.get('attestation_count', '3')
        if not attestation_count.isdigit() or not 1 <= int(attestation_count) <= 6:
            errors.append('Аттестаций за семестр — число от 1 до 6.')
        admission_score = request.POST.get('admission_score', '50')
        if not admission_score.isdigit() or not 0 <= int(admission_score) <= 100:
            errors.append('Порог допуска — число от 0 до 100.')
        control_form = request.POST.get('control_form') or Course.FORM_EXAM
        if control_form not in dict(Course.FORM_CHOICES):
            errors.append('Неизвестная форма аттестации.')
        if group and Course.objects.filter(code=code, group=group).exists():
            errors.append('Курс с таким кодом у группы уже есть.')
        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, 'education/course_form.html', {
                'groups': groups,
                'form_data': request.POST,
                'control_forms': Course.FORM_CHOICES,
            })
        course = Course.objects.create(
            title=title, code=code, group=group, teacher=request.user,
            semester=int(semester), description=description,
            control_form=control_form,
            attestation_count=int(attestation_count),
            admission_score=int(admission_score),
            has_coursework=bool(request.POST.get('has_coursework')),
        )
        students = Profile.objects.filter(
            group=group, user__is_active=True, role__code__in=['student', 'monitor']
        ).values_list('user_id', flat=True)
        Enrollment.objects.bulk_create(
            [Enrollment(course=course, student_id=sid) for sid in students], ignore_conflicts=True
        )
        messages.success(request, f'Курс «{title}» создан, зачислено студентов: {len(students)}.')
        return redirect('education:course_detail', course_id=course.id)
    return render(request, 'education/course_form.html', {
        'groups': groups,
        'control_forms': Course.FORM_CHOICES,
    })


@login_required
def course_detail(request, course_id):
    course = get_object_or_404(Course.objects.select_related('group', 'teacher'), pk=course_id)
    if not can_access_course(request.user, course):
        raise PermissionDenied
    assignments = course.assignments.annotate(submissions_count=Count('submissions')).order_by('-created_at')
    my_submissions = {
        s.assignment_id: s
        for s in Submission.objects.filter(assignment__course=course, student=request.user)
    }
    submissions_to_review = Submission.objects.filter(
        assignment__course=course, status=Submission.STATUS_SUBMITTED
    ).select_related('student', 'assignment').order_by('-submitted_at') if is_course_teacher(request.user, course) else []
    my_grades = {g.kind: g for g in Grade.objects.filter(course=course, student=request.user)}
    my_attendance = {}
    if Enrollment.objects.filter(course=course, student=request.user).exists():
        for row in Attendance.objects.filter(course=course, student=request.user).values('status').annotate(n=Count('id')):
            my_attendance[row['status']] = row['n']
    sessions = Attendance.objects.filter(course=course).values('date').distinct().count()
    is_enrolled = Enrollment.objects.filter(course=course, student=request.user).exists()
    my_rating = course_rating(course, request.user) if is_enrolled else None
    return render(request, 'education/course_detail.html', {
        'course': course,
        'assignments': assignments,
        'my_submissions': my_submissions,
        'submissions_to_review': submissions_to_review,
        'is_teacher': is_course_teacher(request.user, course),
        'is_enrolled': is_enrolled,
        'my_grades': my_grades,
        'my_attendance': my_attendance,
        'attestation_kind': _attestation_kind(course),
        'sessions': sessions,
        'my_rating': my_rating,
    })


@login_required
@require_POST
def submit_work(request, assignment_id):
    assignment = get_object_or_404(Assignment.objects.select_related('course'), pk=assignment_id)
    course = assignment.course
    if not Enrollment.objects.filter(course=course, student=request.user).exists():
        messages.error(request, 'Вы не записаны на этот курс.')
        return redirect('education:courses')

    upload = request.FILES.get('file')
    if upload is None:
        messages.error(request, 'Приложите файл работы.')
        return redirect('education:course_detail', course_id=course.id)
    ext = os.path.splitext(upload.name)[1].lower()
    if ext not in ALLOWED_WORK_EXT:
        messages.error(request, 'Формат файла не поддерживается: ' + ', '.join(sorted(ALLOWED_WORK_EXT)))
        return redirect('education:course_detail', course_id=course.id)
    if upload.size > MAX_WORK_SIZE:
        messages.error(request, 'Файл больше 20 МБ.')
        return redirect('education:course_detail', course_id=course.id)

    existing = Submission.objects.filter(assignment=assignment, student=request.user).first()
    if existing and existing.status != Submission.STATUS_RETURNED:
        messages.error(request, 'Работа уже загружена — дождитесь проверки.')
        return redirect('education:course_detail', course_id=course.id)
    if existing:
        existing.file = upload
        existing.comment = request.POST.get('comment', '').strip()[:2000]
        existing.status = Submission.STATUS_SUBMITTED
        existing.grade = ''
        existing.teacher_comment = ''
        existing.reviewed_at = None
        existing.reviewed_by = None
        existing.save()
    else:
        Submission.objects.create(
            assignment=assignment,
            student=request.user,
            file=upload,
            comment=request.POST.get('comment', '').strip()[:2000],
        )
    messages.success(request, 'Работа загружена и отправлена на проверку.')
    return redirect('education:course_detail', course_id=course.id)


@login_required
def my_works(request):
    submissions = Submission.objects.filter(student=request.user).select_related('assignment', 'assignment__course')
    return render(request, 'education/works.html', {'submissions': submissions})


@login_required
def course_journal(request, course_id):
    course = get_object_or_404(Course.objects.select_related('group', 'teacher'), pk=course_id)
    if not role_can(request.user, 'can_grade') or not is_course_teacher(request.user, course):
        raise PermissionDenied
    enrollments = list(course.enrollments.select_related('student', 'student__profile').order_by(
        'student__last_name', 'student__first_name'
    ))

    dates = list(
        Attendance.objects.filter(course=course).values_list('date', flat=True).distinct().order_by('date')
    )
    marks = {}
    for row in Attendance.objects.filter(course=course).values('student_id', 'date', 'status'):
        marks.setdefault(row['student_id'], {})[row['date']] = row['status']
    code_for = {
        Attendance.STATUS_PRESENT: 'б',
        Attendance.STATUS_ABSENT: 'н',
        Attendance.STATUS_LATE: 'о',
        Attendance.STATUS_EXCUSED: 'у',
    }

    grades_by_kind = {}
    for grade in Grade.objects.filter(course=course):
        grades_by_kind.setdefault(grade.kind, {})[grade.student_id] = grade

    points = list(course.control_points.prefetch_related('weights'))
    marks_by_student = {}
    for mark in PointMark.objects.filter(point__course=course):
        marks_by_student.setdefault(mark.student_id, {}).setdefault(mark.point_id, {})[mark.kind] = mark.score
    point_kinds = []
    for point in points:
        weights = point_weights(point)
        labels = dict(WORK_CHOICES)
        point_kinds.append({'point': point,
                            'kinds': [(k, labels.get(k, k), w) for k, w in weights.items()]})

    kinds_shown = [
        k for k in Grade.KIND_CHOICES
        if k[0] in grades_by_kind
        or k[0] == _attestation_kind(course)
        or (k[0] == Grade.KIND_COURSEWORK and course.has_coursework)
    ]

    rows = []
    for enrollment in enrollments:
        student = enrollment.student
        student_marks = marks.get(student.id, {})
        cells = [
            {'date': d, 'status': student_marks.get(d), 'code': code_for.get(student_marks.get(d), '')}
            for d in dates
        ]
        present = sum(1 for s in student_marks.values() if s == Attendance.STATUS_PRESENT)
        absent = sum(1 for s in student_marks.values() if s == Attendance.STATUS_ABSENT)
        late = sum(1 for s in student_marks.values() if s == Attendance.STATUS_LATE)
        excused = sum(1 for s in student_marks.values() if s == Attendance.STATUS_EXCUSED)
        attended = present + late
        total = present + absent + late + excused
        rating = course_rating(course, student, points=points, marks=marks_by_student.get(student.id, {}))
        rows.append({
            'student': student,
            'profile': getattr(student, 'profile', None),
            'cells': cells,
            'grades': {kind: grades_by_kind.get(kind, {}).get(student.id) for kind, _ in Grade.KIND_CHOICES},
            'attestation': grades_by_kind.get(_attestation_kind(course), {}).get(student.id),
            'point_marks': marks_by_student.get(student.id, {}),
            'rating': rating,
            'present': present,
            'absent': absent,
            'late': late,
            'excused': excused,
            'total': total,
            'skip_percent': round(absent / total * 100) if total else 0,
            'attendance_percent': round(attended / total * 100) if total else None,
        })

    ratings = [row['rating'] for row in rows]
    rating_stats = {
        'students': len(rows),
        'average': round(sum(r['rating'] for r in ratings) / len(ratings)) if ratings else 0,
        'attested': sum(1 for r in ratings if r['verdict'] == VERDICT_YES),
        'risk': sum(1 for r in ratings if r['verdict'] == VERDICT_NO),
        'pending': sum(1 for r in ratings if r['verdict'] == VERDICT_WAIT),
    }

    return render(request, 'education/journal.html', {
        'course': course,
        'rating_stats': rating_stats,
        'rows': rows,
        'dates': dates,
        'kinds': kinds_shown,
        'values': Grade.VALUE_CHOICES,
        'today': timezone.localdate(),
        'attestation_kind': _attestation_kind(course),
        'sessions': len(dates),
        'assignments': course.assignments.annotate(submissions_count=Count('submissions')),
        'points': point_kinds,
        'points_flat': points,
        'work_choices': WORK_CHOICES,
    })


@login_required
def course_points(request, course_id):
    """Настройка контрольных точек (аттестаций) и весов по видам занятий."""
    course = get_object_or_404(Course.objects.select_related('group', 'teacher'), pk=course_id)
    if not role_can(request.user, 'can_grade') or not is_course_teacher(request.user, course):
        raise PermissionDenied
    if request.method == 'POST':
        count = int(request.POST.get('count') or course.attestation_count)
        count = max(1, min(count, 10))
        course.attestation_count = count
        course.admission_score = int(request.POST.get('admission_score') or course.admission_score)
        course.save(update_fields=['attestation_count', 'admission_score'])
        for number in range(1, count + 1):
            point, _ = ControlPoint.objects.get_or_create(course=course, number=number,
                                                          defaults={'title': f'Аттестация {number}'})
            point.weight = int(request.POST.get(f'weight_{number}') or point.weight)
            point.pass_score = int(request.POST.get(f'pass_{number}') or point.pass_score)
            point.date = parse_date(request.POST.get(f'date_{number}'), None)
            point.title = request.POST.get(f'title_{number}') or point.title
            point.closed = bool(request.POST.get(f'closed_{number}'))
            point.save()
            for kind, _label in WORK_CHOICES:
                weight = int(request.POST.get(f'kw_{number}_{kind}') or 0)
                PointWeight.objects.update_or_create(point=point, kind=kind, defaults={'weight': weight})
        messages.success(request, 'Контрольные точки сохранены')
        return redirect('education:course_points', course_id=course.id)

    points = list(course.control_points.prefetch_related('weights'))
    table = []
    for point in points:
        weights = point.kind_weights
        table.append({
            'point': point,
            'number': point.number,
            'cells': [(kind, label, weights.get(kind, 0)) for kind, label in WORK_CHOICES],
            'weight_sum': sum(weights.values()),
        })
    if not table:
        numbers = range(1, course.attestation_count + 1)
        table = [{'point': None, 'number': n, 'cells': [(kind, label, 0) for kind, label in WORK_CHOICES],
                  'weight_sum': 0} for n in numbers]
    return render(request, 'education/points.html', {
        'course': course,
        'table': table,
        'weight_sum': sum(point.weight for point in points),
        'weight_ok': sum(point.weight for point in points) == 100,
        'points': points,
        'work_choices': WORK_CHOICES,
    })


@login_required
@require_POST
def set_point_marks(request, course_id):
    """Преподаватель сохраняет баллы студента по всем видам занятий точек разом."""
    course = get_object_or_404(Course.objects.select_related('teacher'), pk=course_id)
    if not role_can(request.user, 'can_grade') or not is_course_teacher(request.user, course):
        raise PermissionDenied
    student = get_object_or_404(Profile, pk=request.POST.get('student')).user
    points = {str(p.id): p for p in course.control_points.all()}
    for key, raw in request.POST.items():
        if not key.startswith('p') or '_' not in key:
            continue
        point_id, kind = key[1:].split('_', 1)
        point = points.get(point_id)
        if point is None or kind not in dict(WORK_CHOICES):
            continue
        raw = (raw or '').strip()
        if raw == '':
            PointMark.objects.filter(point=point, student=student, kind=kind).delete()
        else:
            try:
                score = max(0, min(int(raw), 100))
            except ValueError:
                continue
            PointMark.objects.update_or_create(point=point, student=student, kind=kind,
                                               defaults={'score': score, 'set_by': request.user})
    messages.success(request, 'Баллы сохранены')
    return redirect('education:journal', course_id=course.id)


@login_required
@require_POST
def close_point(request, course_id):
    """Закрыть/открыть аттестацию — как «Закрыта» у ведомости в ЭОС."""
    course = get_object_or_404(Course.objects.select_related('teacher'), pk=course_id)
    if not role_can(request.user, 'can_grade') or not is_course_teacher(request.user, course):
        raise PermissionDenied
    point = get_object_or_404(ControlPoint, pk=request.POST.get('point'), course=course)
    point.closed = not point.closed
    point.save(update_fields=['closed'])
    return redirect('education:journal', course_id=course.id)


def _attestation_kind(course):
    """Вид контроля, которым закрывается дисциплина, — из формы аттестации курса."""
    if course.control_form in (Course.FORM_CREDIT, Course.FORM_DIFF):
        return Grade.KIND_CREDIT
    if course.control_form == Course.FORM_COURSEWORK:
        return Grade.KIND_COURSEWORK
    return Grade.KIND_EXAM


@login_required
@require_POST
def set_grade(request, course_id):
    course = get_object_or_404(Course.objects.select_related('teacher'), pk=course_id)
    if not role_can(request.user, 'can_grade') or not is_course_teacher(request.user, course):
        raise PermissionDenied
    student_id = request.POST.get('student')
    kind = request.POST.get('kind')
    value = request.POST.get('value')
    date = parse_date(request.POST.get('date'), timezone.localdate())
    if not Enrollment.objects.filter(course=course, student_id=student_id).exists():
        messages.error(request, 'Студент не записан на курс.')
        return redirect('education:journal', course_id=course.id)
    if kind not in dict(Grade.KIND_CHOICES) or value not in dict(Grade.VALUE_CHOICES):
        messages.error(request, 'Некорректная оценка или вид контроля.')
        return redirect('education:journal', course_id=course.id)
    Grade.objects.update_or_create(
        course=course,
        student_id=student_id,
        kind=kind,
        defaults={
            'value': value,
            'date': date,
            'comment': request.POST.get('comment', '').strip()[:200],
            'created_by': request.user,
        },
    )
    messages.success(request, 'Оценка сохранена.')
    return redirect('education:journal', course_id=course.id)


@login_required
@role_required('can_create_courses')
@require_POST
def assignment_create(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    if not is_course_teacher(request.user, course):
        raise PermissionDenied
    title = request.POST.get('title', '').strip()
    if len(title) < 3:
        messages.error(request, 'Название задания — минимум 3 символа.')
        return redirect('education:journal', course_id=course.id)
    Assignment.objects.create(
        course=course,
        title=title,
        description=request.POST.get('description', '').strip()[:4000],
        due_date=parse_date(request.POST.get('due_date')),
        material=request.FILES.get('material') or '',
        created_by=request.user,
    )
    messages.success(request, 'Задание добавлено.')
    if request.POST.get('next') == 'journal':
        return redirect('education:journal', course_id=course.id)
    return redirect('education:course_detail', course_id=course.id)


@login_required
@require_POST
def review_submission(request, submission_id):
    submission = get_object_or_404(
        Submission.objects.select_related('assignment', 'assignment__course'), pk=submission_id
    )
    course = submission.assignment.course
    if not role_can(request.user, 'can_grade') or not is_course_teacher(request.user, course):
        raise PermissionDenied
    status = request.POST.get('status')
    if status not in (Submission.STATUS_REVIEWED, Submission.STATUS_RETURNED):
        messages.error(request, 'Некорректный статус проверки.')
        return redirect('education:course_detail', course_id=course.id)
    submission.status = status
    submission.grade = request.POST.get('grade', '').strip()[:16]
    submission.teacher_comment = request.POST.get('teacher_comment', '').strip()[:4000]
    submission.reviewed_at = timezone.now()
    submission.reviewed_by = request.user
    submission.save()
    messages.success(request, 'Работа проверена.')
    return redirect('education:course_detail', course_id=course.id)


@login_required
def attendance_view(request, course_id):
    course = get_object_or_404(Course.objects.select_related('teacher'), pk=course_id)
    if not role_can(request.user, 'can_grade') or not is_course_teacher(request.user, course):
        raise PermissionDenied
    date = parse_date(request.GET.get('date'), timezone.localdate())
    enrollments = course.enrollments.select_related('student').order_by('student__last_name', 'student__first_name')
    existing = {a.student_id: a.status for a in Attendance.objects.filter(course=course, date=date)}

    if request.method == 'POST':
        date = parse_date(request.POST.get('date'), date)
        valid_statuses = dict(Attendance.STATUS_CHOICES)
        saved = 0
        for enrollment in enrollments:
            status = request.POST.get(f'status_{enrollment.student_id}')
            if status not in valid_statuses:
                continue
            Attendance.objects.update_or_create(
                course=course,
                student=enrollment.student,
                date=date,
                defaults={'status': status, 'marked_by': request.user},
            )
            saved += 1
        messages.success(request, f'Посещаемость за {date:%d.%m.%Y} сохранена ({saved} студентов).')
        return redirect(f"{request.path}?date={date.isoformat()}")

    rows = [{'student': e.student, 'status': existing.get(e.student_id, Attendance.STATUS_PRESENT)} for e in enrollments]
    sessions = list(
        Attendance.objects.filter(course=course).values('date').annotate(
            present=Count('id', filter=Q(status=Attendance.STATUS_PRESENT)),
            absent=Count('id', filter=Q(status=Attendance.STATUS_ABSENT)),
            late=Count('id', filter=Q(status=Attendance.STATUS_LATE)),
            excused=Count('id', filter=Q(status=Attendance.STATUS_EXCUSED)),
        ).order_by('-date')
    )
    return render(request, 'education/attendance.html', {
        'course': course,
        'rows': rows,
        'date': date,
        'statuses': Attendance.STATUS_CHOICES,
        'sessions': sessions,
        'marked_count': len(existing),
    })
