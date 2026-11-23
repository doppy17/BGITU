"""Расписание: звонки, занятия, импорт файла с сайта БГИТУ."""
from django.conf import settings
from django.db import models


class Bell(models.Model):
    building = models.CharField('корпус', max_length=8, default='2')
    pair = models.PositiveSmallIntegerField('пара')
    start = models.CharField('начало', max_length=8)
    end = models.CharField('конец', max_length=8)

    class Meta:
        verbose_name = 'звонок'
        verbose_name_plural = 'звонки'
        ordering = ['building', 'pair']
        constraints = [models.UniqueConstraint(fields=['building', 'pair'], name='uniq_bell')]

    def __str__(self):
        return f'{self.building} корпус · {self.pair} пара {self.start}–{self.end}'


class ScheduleImport(models.Model):
    """Загруженный файл расписания — источник занятий.

    Один файл = один охват (институт/направления). Файлы разных охватов
    живут в БД одновременно; повторная загрузка файла того же охвата
    заменяет занятия предыдущего. При конфликте слотов между разными
    файлами побеждает занятие из самого свежего по imported_at.
    """

    file = models.FileField('файл', upload_to='schedule/%Y/%m/')
    original_name = models.CharField('имя файла', max_length=255)
    institute = models.CharField('институт / охват', max_length=160, blank=True, db_index=True)
    size = models.PositiveBigIntegerField('размер, байт', default=0)
    source_url = models.URLField('источник', blank=True)
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='schedule_imports'
    )
    imported_at = models.DateTimeField('импортирован', auto_now_add=True)
    lessons_count = models.PositiveIntegerField('занятий', default=0)
    groups_count = models.PositiveSmallIntegerField('групп', default=0)
    note = models.CharField('примечание', max_length=255, blank=True)

    class Meta:
        verbose_name = 'источник расписания'
        verbose_name_plural = 'источники расписания'
        ordering = ['-imported_at']

    def __str__(self):
        return f'{self.original_name} · {self.imported_at:%d.%m.%Y}'

    # Имена в духе «источник файла»: uploaded_at/author — те же поля, что
    # imported_at/imported_by, без дублирования данных в БД.
    @property
    def uploaded_at(self):
        return self.imported_at

    @property
    def uploaded_by(self):
        return self.imported_by

    @property
    def author(self):
        return self.imported_by

    @property
    def size_label(self):
        size = self.size or 0
        if size >= 1024 * 1024:
            return f'{size / (1024 * 1024):.1f} МБ'
        if size >= 1024:
            return f'{size / 1024:.0f} КБ'
        return f'{size} Б' if size else '—'


# Понятный алиас для кода, который говорит об «источнике», а не об импорте.
ScheduleSource = ScheduleImport


class Lesson(models.Model):
    WEEK_ALL = 'all'
    WEEK_ODD = 'odd'  # 1-я учебная неделя (верхняя половина ячейки в файле)
    WEEK_EVEN = 'even'  # 2-я учебная неделя (нижняя половина)
    WEEK_CHOICES = [
        (WEEK_ALL, 'каждую неделю'),
        (WEEK_ODD, '1-я неделя'),
        (WEEK_EVEN, '2-я неделя'),
    ]
    BUILDING_CHOICES = [('1', '1 корпус'), ('2', '2 корпус')]

    group = models.ForeignKey('education.Group', on_delete=models.CASCADE, related_name='lessons')
    weekday = models.PositiveSmallIntegerField('день недели')  # 1 — понедельник
    pair = models.PositiveSmallIntegerField('пара')
    week = models.CharField('неделя', max_length=8, choices=WEEK_CHOICES, default=WEEK_ALL)
    subject = models.CharField('дисциплина', max_length=180)
    kind = models.CharField('вид занятия', max_length=40, blank=True)
    teacher_name = models.CharField('преподаватель', max_length=120, blank=True)
    room = models.CharField('аудитория', max_length=40, blank=True)
    building = models.CharField('корпус', max_length=1, choices=BUILDING_CHOICES, blank=True, default='')
    subgroup = models.CharField('подгруппа', max_length=8, blank=True)
    source = models.ForeignKey(ScheduleImport, on_delete=models.SET_NULL, null=True, blank=True, related_name='lessons')

    class Meta:
        verbose_name = 'занятие'
        verbose_name_plural = 'занятия'
        ordering = ['weekday', 'pair', 'subgroup']
        indexes = [models.Index(fields=['group', 'weekday', 'week'])]

    def __str__(self):
        return f'{self.group} · {self.subject}'

    @property
    def weekday_name(self):
        return ['', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'][self.weekday]

    @property
    def subgroup_label(self):
        """Подпись подгруппы для интерфейса: «1» и «2» из файла = «а» и «б»."""
        return {'1': 'а', '2': 'б'}.get(str(self.subgroup), str(self.subgroup or ''))

    @property
    def week_number(self):
        """1 или 2 для половин пары; 0 — занятие каждую неделю."""
        return {'odd': 1, 'even': 2}.get(str(self.week), 0)

    @property
    def week_label(self):
        return {'all': '', 'odd': '1-я неделя', 'even': '2-я неделя'}.get(str(self.week), '')

    @property
    def kind_label(self):
        """Вид занятия для бейджа: пусто, если он дублирует название дисциплины.

        У физкультуры вид «физкультура» повторяет предмет — бейдж не нужен.
        В общем случае вид скрывается, когда он целиком входит в название
        дисциплины или наоборот.
        """
        kind = str(self.kind or '').strip()
        subject = str(self.subject or '').strip().lower()
        if not kind:
            return ''
        lowered = kind.lower()
        if 'физкультур' in subject:
            return ''
        if subject and (lowered in subject or subject in lowered):
            return ''
        return kind

    @property
    def building_label(self):
        return f'{self.building} корпус' if self.building else ''
