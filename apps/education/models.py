"""Группы, курсы, работы, посещаемость и оценки."""
from django.conf import settings
from django.db import models

WORK_LECTURE = 'lecture'
WORK_PRACTICE = 'practice'
WORK_LAB = 'lab'
WORK_OTHER = 'other'
WORK_CHOICES = [
    (WORK_LECTURE, 'лек.'),
    (WORK_PRACTICE, 'пр.'),
    (WORK_LAB, 'лаб.'),
    (WORK_OTHER, 'др.'),
]


class Group(models.Model):
    code = models.CharField('код группы', max_length=16, unique=True)
    program = models.CharField('направление', max_length=160, blank=True)
    course = models.PositiveSmallIntegerField('курс', default=1)
    institute = models.CharField('институт', max_length=120, default='Инженерно-экономический институт')
    curator = models.CharField('куратор', max_length=120, blank=True)

    class Meta:
        verbose_name = 'группа'
        verbose_name_plural = 'группы'
        ordering = ['code']

    def __str__(self):
        return self.code


class Course(models.Model):
    FORM_EXAM = 'exam'
    FORM_CREDIT = 'credit'
    FORM_DIFF = 'diff'
    FORM_COURSEWORK = 'coursework'
    FORM_NONE = 'none'
    FORM_CHOICES = [
        (FORM_EXAM, 'экзамен'),
        (FORM_CREDIT, 'зачёт'),
        (FORM_DIFF, 'диф. зачёт'),
        (FORM_COURSEWORK, 'курсовая'),
        (FORM_NONE, 'без аттестации'),
    ]

    title = models.CharField('название', max_length=160)
    code = models.CharField('код', max_length=32)
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='courses')
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='teaching_courses')
    semester = models.PositiveSmallIntegerField('семестр', default=1)
    description = models.TextField('описание', blank=True)
    control_form = models.CharField('форма аттестации', max_length=16, choices=FORM_CHOICES, default=FORM_EXAM)
    has_coursework = models.BooleanField('предусмотрена курсовая', default=False)
    attestation_count = models.PositiveSmallIntegerField('аттестаций за семестр', default=3)
    admission_score = models.PositiveSmallIntegerField('порог допуска, %', default=50)
    created_at = models.DateTimeField('создан', auto_now_add=True)

    class Meta:
        verbose_name = 'курс'
        verbose_name_plural = 'курсы'
        ordering = ['title']
        constraints = [models.UniqueConstraint(fields=['code', 'group'], name='uniq_course_code_group')]

    def __str__(self):
        return f'{self.title} · {self.group.code}'

    @property
    def control_label(self):
        return self.get_control_form_display()

    @property
    def is_credit_form(self):
        return self.control_form in (self.FORM_CREDIT, self.FORM_DIFF)


class Enrollment(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='enrollments')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='enrollments')
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'зачисление'
        verbose_name_plural = 'зачисления'
        constraints = [models.UniqueConstraint(fields=['course', 'student'], name='uniq_enrollment')]

    def __str__(self):
        return f'{self.student} · {self.course}'


class Assignment(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='assignments')
    title = models.CharField('название', max_length=180)
    description = models.TextField('описание', blank=True)
    due_date = models.DateField('срок сдачи', null=True, blank=True)
    material = models.FileField('материал', upload_to='materials/%Y/%m/', blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_assignments')
    created_at = models.DateTimeField('создана', auto_now_add=True)

    class Meta:
        verbose_name = 'задание'
        verbose_name_plural = 'задания'
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class Submission(models.Model):
    STATUS_SUBMITTED = 'submitted'
    STATUS_REVIEWED = 'reviewed'
    STATUS_RETURNED = 'returned'
    STATUS_CHOICES = [
        (STATUS_SUBMITTED, 'на проверке'),
        (STATUS_REVIEWED, 'проверена'),
        (STATUS_RETURNED, 'на доработке'),
    ]

    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='submissions')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='submissions')
    file = models.FileField('файл работы', upload_to='works/%Y/%m/')
    comment = models.TextField('комментарий', blank=True)
    submitted_at = models.DateTimeField('загружена', auto_now_add=True)
    status = models.CharField('статус', max_length=16, choices=STATUS_CHOICES, default=STATUS_SUBMITTED)
    grade = models.CharField('оценка', max_length=16, blank=True)
    teacher_comment = models.TextField('комментарий преподавателя', blank=True)
    reviewed_at = models.DateTimeField('проверена', null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_submissions'
    )

    class Meta:
        verbose_name = 'работа'
        verbose_name_plural = 'работы'
        ordering = ['-submitted_at']
        constraints = [models.UniqueConstraint(fields=['assignment', 'student'], name='uniq_submission')]

    def __str__(self):
        return f'{self.student} · {self.assignment}'


class Attendance(models.Model):
    STATUS_PRESENT = 'present'
    STATUS_ABSENT = 'absent'
    STATUS_LATE = 'late'
    STATUS_EXCUSED = 'excused'
    STATUS_CHOICES = [
        (STATUS_PRESENT, 'был'),
        (STATUS_ABSENT, 'не был'),
        (STATUS_LATE, 'опоздал'),
        (STATUS_EXCUSED, 'уважительная'),
    ]

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='attendance')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='attendance')
    date = models.DateField('дата')
    status = models.CharField('статус', max_length=16, choices=STATUS_CHOICES, default=STATUS_PRESENT)
    marked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='marked_attendance')
    marked_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'посещаемость'
        verbose_name_plural = 'посещаемость'
        ordering = ['-date']
        constraints = [models.UniqueConstraint(fields=['course', 'student', 'date'], name='uniq_attendance')]


class Grade(models.Model):
    KIND_EXAM = 'exam'
    KIND_CREDIT = 'credit'
    KIND_COURSEWORK = 'coursework'
    KIND_TEST = 'test'
    KIND_CHOICES = [
        (KIND_EXAM, 'экзамен'),
        (KIND_CREDIT, 'зачёт'),
        (KIND_COURSEWORK, 'курсовая'),
        (KIND_TEST, 'контрольная'),
    ]
    VALUE_CHOICES = [
        ('5', '5'),
        ('4', '4'),
        ('3', '3'),
        ('2', '2'),
        ('зачтено', 'зачтено'),
        ('не зачтено', 'не зачтено'),
    ]

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='grades')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='grades')
    kind = models.CharField('вид контроля', max_length=16, choices=KIND_CHOICES, default=KIND_EXAM)
    value = models.CharField('оценка', max_length=16, choices=VALUE_CHOICES)
    date = models.DateField('дата')
    comment = models.CharField('комментарий', max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_grades')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'оценка'
        verbose_name_plural = 'оценки'
        ordering = ['-date']
        constraints = [models.UniqueConstraint(fields=['course', 'student', 'kind'], name='uniq_grade_kind')]

    def __str__(self):
        return f'{self.student} · {self.course} · {self.value}'

    @property
    def is_fail(self):
        return self.value in ('2', 'не зачтено')

    @property
    def numeric(self):
        return int(self.value) if self.value in ('2', '3', '4', '5') else None


class ControlPoint(models.Model):
    """Контрольная точка (аттестация) дисциплины — КТ1…КТ3 как в ЭОС.

    Вес точки задаёт её долю в итоговом рейтинге дисциплины, внутри точки
    баллы распределяются по видам занятий (лекции/практика/лабораторные/другое).
    """
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='control_points')
    number = models.PositiveSmallIntegerField('номер аттестации', default=1)
    title = models.CharField('название', max_length=120, blank=True)
    weight = models.PositiveSmallIntegerField('вес точки в рейтинге, %', default=20)
    pass_score = models.PositiveSmallIntegerField('проходной балл точки, %', default=50)
    date = models.DateField('дата проведения', null=True, blank=True)
    closed = models.BooleanField('закрыта', default=False)

    class Meta:
        verbose_name = 'контрольная точка'
        verbose_name_plural = 'контрольные точки'
        ordering = ['number']
        constraints = [models.UniqueConstraint(fields=['course', 'number'], name='uniq_point_number')]

    def __str__(self):
        return f'КТ{self.number} · {self.course}'

    @property
    def kind_weights(self):
        return {w.kind: w.weight for w in self.weights.all()}


class PointWeight(models.Model):
    """Вес вида занятий внутри контрольной точки (в сумме 100 % на точку)."""
    point = models.ForeignKey(ControlPoint, on_delete=models.CASCADE, related_name='weights')
    kind = models.CharField('вид занятий', max_length=16, choices=WORK_CHOICES)
    weight = models.PositiveSmallIntegerField('вес, %', default=0)

    class Meta:
        verbose_name = 'вес вида занятий'
        verbose_name_plural = 'веса видов занятий'
        ordering = ['kind']
        constraints = [models.UniqueConstraint(fields=['point', 'kind'], name='uniq_point_kind')]


class PointMark(models.Model):
    """Балл студента по виду занятий внутри контрольной точки (0…100)."""
    point = models.ForeignKey(ControlPoint, on_delete=models.CASCADE, related_name='marks')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='point_marks')
    kind = models.CharField('вид занятий', max_length=16, choices=WORK_CHOICES)
    score = models.PositiveSmallIntegerField('балл, %', null=True, blank=True)
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                               related_name='set_point_marks', null=True, blank=True)
    updated_at = models.DateTimeField('обновлён', auto_now=True)

    class Meta:
        verbose_name = 'балл по точке'
        verbose_name_plural = 'баллы по точкам'
        ordering = ['point__number', 'kind']
        constraints = [models.UniqueConstraint(fields=['point', 'student', 'kind'], name='uniq_point_mark')]

    def __str__(self):
        return f'{self.student} · {self.point} · {self.kind}: {self.score}'


class PlanSource(models.Model):
    """Загруженный файл учебного плана — источник дисциплин направления.

    Один файл = один учебный план направления (09.03.02 и т.п.). Файлы разных
    направлений живут в БД одновременно; при конфликте дисциплины в одном
    семестре побеждает запись из самого свежего по uploaded_at файла.
    """

    file = models.FileField('файл', upload_to='plans/%Y/%m/')
    original_name = models.CharField('имя файла', max_length=255)
    direction = models.CharField('направление', max_length=200, blank=True, db_index=True)
    direction_code = models.CharField('код направления', max_length=32, blank=True, db_index=True)
    size = models.PositiveBigIntegerField('размер, байт', default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='plan_sources'
    )
    uploaded_at = models.DateTimeField('загружен', auto_now_add=True)
    entries_count = models.PositiveIntegerField('дисциплин', default=0)
    note = models.CharField('примечание', max_length=255, blank=True)

    class Meta:
        verbose_name = 'источник учебного плана'
        verbose_name_plural = 'источники учебного плана'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'{self.original_name} · {self.direction or "направление не определено"}'

    @property
    def size_label(self):
        size = self.size or 0
        if size >= 1024 * 1024:
            return f'{size / (1024 * 1024):.1f} МБ'
        if size >= 1024:
            return f'{size / 1024:.0f} КБ'
        return f'{size} Б' if size else '—'


class CurriculumEntry(models.Model):
    """Строка учебного плана: дисциплина и семестры, в которых она читается."""

    code = models.CharField('направление', max_length=16, default='ist')
    subject = models.CharField('дисциплина', max_length=200)
    semester_from = models.PositiveSmallIntegerField('с семестра', default=1)
    semester_to = models.PositiveSmallIntegerField('по семестр', default=1)
    source = models.ForeignKey(
        PlanSource, on_delete=models.CASCADE, null=True, blank=True, related_name='entries'
    )

    class Meta:
        verbose_name = 'учебный план'
        verbose_name_plural = 'учебный план'
        ordering = ['semester_from', 'subject']

    def __str__(self):
        return f'{self.subject} ({self.semester_from}–{self.semester_to})'
