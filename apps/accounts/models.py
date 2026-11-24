"""Роли, профили и заявки на получение роли."""
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

# Простая транслитерация для ФИО латиницей — без словаря исключений ГОСТ.
TRANSLIT_MAP = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
}


def transliterate(text):
    """Кириллица → латиница; регистр первой буквы сохраняется."""
    result = []
    for char in text or '':
        replacement = TRANSLIT_MAP.get(char.lower())
        if replacement is None:
            result.append(char)
        elif char.isupper() and replacement:
            result.append(replacement[0].upper() + replacement[1:])
        else:
            result.append(replacement)
    return ''.join(result)


# Нормативный срок обучения по уровням — для расчёта года окончания.
STUDY_TERM_YEARS = {
    'bachelor': 4,
    'specialist': 5,
    'master': 2,
    'postgraduate': 3,
    'aspirant': 3,
}
DEFAULT_STUDY_TERM_YEARS = 4


class Role(models.Model):
    """Роль в системе. Права — булевы флаги, developer обходит все проверки."""

    code = models.SlugField('код', max_length=40, unique=True)
    name = models.CharField('название', max_length=80)
    description = models.TextField('описание', blank=True)
    can_manage_users = models.BooleanField('управление пользователями', default=False)
    can_manage_roles = models.BooleanField('управление ролями', default=False)
    can_approve_roles = models.BooleanField('рассмотрение заявок на роли', default=False)
    can_manage_schedule = models.BooleanField('управление расписанием', default=False)
    can_create_courses = models.BooleanField('создание курсов', default=False)
    can_grade = models.BooleanField('оценки и ведомости', default=False)
    can_upload_works = models.BooleanField('загрузка работ', default=True)
    can_view_all = models.BooleanField('видит все группы', default=False)
    is_developer = models.BooleanField('разработчик (полный доступ)', default=False)
    is_system = models.BooleanField('системная роль', default=False)
    created_at = models.DateTimeField('создана', auto_now_add=True)

    class Meta:
        verbose_name = 'роль'
        verbose_name_plural = 'роли'
        ordering = ['id']

    def __str__(self):
        return self.name

    @property
    def permission_list(self):
        fields = [
            ('can_manage_users', 'пользователи'),
            ('can_manage_roles', 'роли'),
            ('can_approve_roles', 'заявки на роли'),
            ('can_manage_schedule', 'расписание'),
            ('can_create_courses', 'курсы'),
            ('can_grade', 'оценки'),
            ('can_upload_works', 'работы'),
            ('can_view_all', 'все группы'),
            ('is_developer', 'полный доступ'),
        ]
        return [label for field, label in fields if getattr(self, field)]


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name='profiles', null=True, blank=True)
    group = models.ForeignKey('education.Group', on_delete=models.SET_NULL, related_name='profiles', null=True, blank=True)
    subgroup = models.CharField(
        'подгруппа', max_length=8, blank=True, default='',
        choices=[('', 'не выбрана'), ('а', 'подгруппа «а»'), ('б', 'подгруппа «б»')],
        help_text='Подгруппа по расписанию — в файле БГИТУ это колонки «а»/«б» или пара под номером',
    )
    theme = models.CharField(
        'тема оформления', max_length=10, blank=True,
        choices=[('paper', 'Бумага'), ('surik', 'Сурик'), ('latun', 'Латунь'), ('brass', 'Бронза')],
        help_text='Выбор пользователя переносится между устройствами',
    )
    record_book = models.CharField('зачётная книжка', max_length=32, blank=True)
    phone = models.CharField('телефон', max_length=32, blank=True)
    birth_date = models.DateField('дата рождения', null=True, blank=True)
    photo = models.ImageField('фото', upload_to='profiles/', blank=True)
    enrollment_year = models.PositiveSmallIntegerField('год поступления', null=True, blank=True)
    department = models.CharField('кафедра', max_length=160, blank=True)
    course_override = models.PositiveSmallIntegerField('курс (если не совпадает с группой)', null=True, blank=True)
    institute_override = models.CharField(
        'институт', max_length=120, blank=True,
        help_text='если институт не совпадает с группой',
    )

    # --- Поля по образцу ЭОС БГИТУ ---
    LEVEL_BACHELOR = 'bachelor'
    LEVEL_SPECIALIST = 'specialist'
    LEVEL_MASTER = 'master'
    LEVEL_POSTGRADUATE = 'postgraduate'
    EDUCATION_LEVEL_CHOICES = [
        (LEVEL_BACHELOR, 'бакалавриат'),
        (LEVEL_SPECIALIST, 'специалитет'),
        (LEVEL_MASTER, 'магистратура'),
        (LEVEL_POSTGRADUATE, 'аспирантура'),
    ]
    FORM_FULL_TIME = 'full_time'
    FORM_PART_TIME = 'part_time'
    FORM_CORRESPONDENCE = 'correspondence'
    STUDY_FORM_CHOICES = [
        (FORM_FULL_TIME, 'очная'),
        (FORM_PART_TIME, 'очно-заочная'),
        (FORM_CORRESPONDENCE, 'заочная'),
    ]

    name_latin = models.CharField(
        'ФИО латиницей', max_length=160, blank=True,
        help_text='транслитерация фамилии и имени; заполняется автоматически, если пусто',
    )
    citizenship = models.CharField('гражданство', max_length=60, default='Российское')
    plan_name = models.CharField('план', max_length=120, blank=True, help_text='например, 09.03.02-2025.plx')
    plan_code = models.CharField('код плана', max_length=32, blank=True, help_text='например, 09.03.02')
    scopus_id = models.CharField('Scopus ID', max_length=64, blank=True)
    ticket_number = models.CharField(
        'студенческий билет', max_length=32, blank=True,
        help_text='если пусто — берётся номер зачётной книжки',
    )
    ticket_order = models.CharField('приказ о зачислении', max_length=60, blank=True)
    ticket_issue_date = models.DateField('дата выдачи билета', null=True, blank=True)
    education_level = models.CharField(
        'уровень образования', max_length=16, blank=True, choices=EDUCATION_LEVEL_CHOICES,
    )
    study_form = models.CharField(
        'форма обучения', max_length=16, blank=True, choices=STUDY_FORM_CHOICES,
    )
    direction = models.CharField(
        'направление подготовки', max_length=160, blank=True,
        help_text='если пусто — берётся направление группы',
    )

    class Meta:
        verbose_name = 'профиль'
        verbose_name_plural = 'профили'

    def __str__(self):
        return self.user.get_full_name() or self.user.username

    def save(self, *args, **kwargs):
        """Дозаполняет производные поля ЭОС, если пользователь их не указал."""
        update_fields = kwargs.get('update_fields')

        def should_fill(field, *sources):
            """При частичном сохранении трогаем только запрошенные поля."""
            if update_fields is None:
                return True
            return field in update_fields or any(source in update_fields for source in sources)

        autofilled = []

        if not self.name_latin and should_fill('name_latin'):
            suggestion = self.suggest_latin()
            if suggestion:
                self.name_latin = suggestion
                autofilled.append('name_latin')

        if not self.ticket_number and self.record_book and should_fill('ticket_number', 'record_book'):
            self.ticket_number = self.record_book
            autofilled.append('ticket_number')

        if not self.direction and self.group_id and should_fill('direction', 'group'):
            program = self.group.program if self.group else ''
            if program:
                self.direction = program
                autofilled.append('direction')

        if update_fields is not None and autofilled:
            kwargs['update_fields'] = set(update_fields) | set(autofilled)

        super().save(*args, **kwargs)

    def suggest_latin(self):
        """Транслитерация «Фамилия Имя» для поля ``name_latin``."""
        parts = (
            transliterate(self.user.last_name).strip(),
            transliterate(self.user.first_name).strip(),
        )
        return ' '.join(part for part in parts if part)

    @property
    def display_name(self):
        name = f'{self.user.last_name} {self.user.first_name}'.strip()
        return name or self.user.username

    @property
    def full_name(self):
        """ФИО в русском порядке — фамилия, имя (отчество в User не хранится)."""
        return self.display_name

    @property
    def course(self):
        """Курс обучения: ручной override важнее курса группы."""
        if self.course_override is not None:
            return self.course_override
        group = self.group
        return group.course if group else None

    @property
    def study_term_years(self):
        """Нормативный срок обучения в годах по уровню образования."""
        level = str(self.education_level or '')
        return STUDY_TERM_YEARS.get(level, DEFAULT_STUDY_TERM_YEARS)

    @property
    def graduation_year(self):
        """Год окончания: год поступления + нормативный срок уровня.

        ``None``, если год поступления не заполнен — угадывать нечего.
        """
        if not self.enrollment_year:
            return None
        return self.enrollment_year + self.study_term_years

    @property
    def study_years_label(self):
        """Годы обучения для билета: «2025–2029»; пусто без года поступления."""
        if self.graduation_year is None:
            return ''
        return f'{self.enrollment_year}–{self.graduation_year}'

    @property
    def institute(self):
        """Институт: ручной override важнее института группы."""
        if self.institute_override:
            return self.institute_override
        group = self.group
        return group.institute if group else ''

    @property
    def program(self):
        group = self.group
        return group.program if group else ''

    @property
    def direction_display(self):
        """Направление подготовки: своё значение, иначе направление группы."""
        return self.direction or self.program

    @property
    def ticket_direction(self):
        """Направление для блока студенческого билета."""
        return self.direction or self.program or ''

    @property
    def ticket_level_label(self):
        """Подпись уровня образования; «—», если поле пустое."""
        return self.get_education_level_display() or '—'

    @property
    def ticket_form_label(self):
        """Подпись формы обучения; «—», если поле пустое."""
        return self.get_study_form_display() or '—'

    @property
    def ticket_number_or_record(self):
        """Номер билета, а если его нет — номер зачётной книжки."""
        return self.ticket_number or self.record_book or ''

    @property
    def faculty(self):
        """Институт/факультет — то же, что ``institute``, под именем ЭОС."""
        return self.institute

    @property
    def last_login_at(self):
        """Последний вход в кабинет (``User.last_login``)."""
        return self.user.last_login

    @property
    def average_grade(self):
        """Средний балл по числовым оценкам 2–5; ``None``, если оценок нет."""
        from .performance import total_average

        return total_average(self.user)

    @property
    def excellent_share(self):
        """Доля оценок «5» среди числовых, в процентах; 0 без оценок."""
        from .performance import grade_statistics

        return grade_statistics(self.user)['excellent_share']

    @property
    def good_share(self):
        """Доля оценок «4» среди числовых, в процентах; 0 без оценок."""
        from .performance import grade_statistics

        return grade_statistics(self.user)['good_share']


class Contact(models.Model):
    """Контакт пользователя: телефон, почта, мессенджер — без ограничения числа.

    Один контакт может быть основным (``is_primary``); частичный уникальный
    индекс не даёт завести второму primary у того же пользователя.
    """

    KIND_PHONE = 'phone'
    KIND_EMAIL = 'email'
    KIND_TELEGRAM = 'telegram'
    KIND_VK = 'vk'
    KIND_WHATSAPP = 'whatsapp'
    KIND_OTHER = 'other'
    KIND_CHOICES = [
        (KIND_PHONE, 'телефон'),
        (KIND_EMAIL, 'почта'),
        (KIND_TELEGRAM, 'Telegram'),
        (KIND_VK, 'ВКонтакте'),
        (KIND_WHATSAPP, 'WhatsApp'),
        (KIND_OTHER, 'другое'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='contacts',
        verbose_name='пользователь',
    )
    kind = models.CharField('тип', max_length=16, choices=KIND_CHOICES, default=KIND_PHONE)
    value = models.CharField('контакт', max_length=200)
    label = models.CharField(
        'подпись', max_length=60, blank=True,
        help_text='например, «личный» или «рабочий»',
    )
    is_primary = models.BooleanField('основной', default=False)
    order = models.PositiveSmallIntegerField('порядок', default=0)
    created_at = models.DateTimeField('создан', auto_now_add=True)

    class Meta:
        verbose_name = 'контакт'
        verbose_name_plural = 'контакты'
        ordering = ['order', 'id']
        indexes = [
            models.Index(fields=['user'], name='accounts_contact_user_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user'],
                condition=models.Q(is_primary=True),
                name='uniq_primary_contact_per_user',
            ),
        ]

    def __str__(self):
        suffix = f' ({self.label})' if self.label else ''
        primary = ' ★' if self.is_primary else ''
        return f'{self.user} · {self.get_kind_display()}: {self.value}{suffix}{primary}'


class ProfileHistory(models.Model):
    """История учебных перемещений: смена группы и связанных с ней атрибутов.

    Записи создаёт сигнал ``pre_save`` профиля: при смене ``Profile.group``
    появляется строка ``kind='group'`` (было → стало), а снимки института,
    направления и курса группы до/после лежат в ``comment``.
    """

    KIND_GROUP = 'group'
    KIND_INSTITUTE = 'institute'
    KIND_PROGRAM = 'program'
    KIND_COURSE = 'course'
    KIND_CHOICES = [
        (KIND_GROUP, 'группа'),
        (KIND_INSTITUTE, 'институт'),
        (KIND_PROGRAM, 'направление'),
        (KIND_COURSE, 'курс'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile_history',
        verbose_name='пользователь',
    )
    created_at = models.DateTimeField('создана', auto_now_add=True)
    kind = models.CharField('тип изменения', max_length=16, choices=KIND_CHOICES, default=KIND_GROUP)
    from_value = models.CharField('было', max_length=160, blank=True, default='')
    to_value = models.CharField('стало', max_length=160, blank=True, default='')
    comment = models.TextField('комментарий', blank=True)

    class Meta:
        verbose_name = 'история профиля'
        verbose_name_plural = 'история профиля'
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return f'{self.user} · {self.get_kind_display()}: {self.from_value or "—"} → {self.to_value or "—"}'


class RoleRequest(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'на рассмотрении'),
        (STATUS_APPROVED, 'одобрена'),
        (STATUS_REJECTED, 'отклонена'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='role_requests')
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='requests')
    comment = models.TextField('обоснование', blank=True)
    status = models.CharField('статус', max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField('создана', auto_now_add=True)
    reviewed_at = models.DateTimeField('рассмотрена', null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_role_requests'
    )
    review_comment = models.TextField('комментарий проверяющего', blank=True)

    class Meta:
        verbose_name = 'заявка на роль'
        verbose_name_plural = 'заявки на роль'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} → {self.role} ({self.get_status_display()})'


# --- Одноразовые ключи доступа (временная функция, флаг DEV_ACCESS_KEYS) ---
DEFAULT_ACCESS_KEY_ROLE = 'developer'


def default_access_key_role():
    """Роль по умолчанию для ключа — developer (если такая роль уже создана)."""
    return Role.objects.filter(code=DEFAULT_ACCESS_KEY_ROLE).values_list('id', flat=True).first()


class _AccessKeyCheck:
    """Двойной доступ к имени ``check``.

    Django вызывает ``AccessKey.check(**kwargs)`` как classmethod системных
    проверок, а нам нужен ``key.check(raw)`` на экземпляре. Дескриптор
    разводит эти вызовы, чтобы не сломать ``manage.py check``.
    """

    def __get__(self, instance, owner):
        if instance is None:
            return owner._model_check
        return instance._check_code


class AccessKey(models.Model):
    """Одноразовый ключ доступа: выдаёт роль, сам код в БД не хранится.

    Хранится только sha256-хеш. Ключ одноразовый: после выдачи роли
    заполняются ``used_by``/``used_at`` и ``is_active`` снимается.
    Функция временная — удаляется вместе с командой ``dev_key``,
    страницей ``/access/`` и шаблоном ``accounts/access_key.html``.
    """

    code_hash = models.CharField('sha256-хеш кода', max_length=64, unique=True)
    role = models.ForeignKey(
        Role, on_delete=models.PROTECT, related_name='access_keys',
        verbose_name='роль', default=default_access_key_role,
    )
    created_at = models.DateTimeField('создан', auto_now_add=True)
    expires_at = models.DateTimeField('действует до', null=True, blank=True)
    used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='used_access_keys', verbose_name='использован',
    )
    used_at = models.DateTimeField('использован в', null=True, blank=True)
    is_active = models.BooleanField('активен', default=True)

    class Meta:
        verbose_name = 'ключ доступа'
        verbose_name_plural = 'ключи доступа'
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return f'Ключ #{self.pk or "—"} → {self.role} ({self.status_label})'

    @staticmethod
    def hash_code(raw):
        """sha256 от кода в hex — только он попадает в базу."""
        return hashlib.sha256((raw or '').strip().encode('utf-8')).hexdigest()

    @classmethod
    def issue(cls, role=None, days=14):
        """Создаёт ключ и возвращает пару (запись, код в открытом виде).

        Код существует только в возвращённом значении и в выводе команды:
        ``secrets.token_urlsafe(24)`` даёт ровно 32 URL-safe символа.
        """
        if role is None:
            role = Role.objects.filter(code=DEFAULT_ACCESS_KEY_ROLE).first()
        if role is None:
            raise ValueError('Роль developer не найдена — создайте её перед выпуском ключа.')
        raw = secrets.token_urlsafe(24)
        expires_at = timezone.now() + timedelta(days=days) if days else None
        key = cls.objects.create(code_hash=cls.hash_code(raw), role=role, expires_at=expires_at)
        return key, raw

    @property
    def is_used(self):
        return self.used_at is not None

    @property
    def is_expired(self):
        return bool(self.expires_at and timezone.now() >= self.expires_at)

    @property
    def status_label(self):
        if self.is_used:
            return 'использован'
        if not self.is_active:
            return 'отозван'
        if self.is_expired:
            return 'просрочен'
        return 'активен'

    # key.check(raw) — см. дескриптор выше; на классе остаётся Model.check().
    check = _AccessKeyCheck()  # type: ignore[assignment]

    def _check_code(self, raw):
        """Реализация ``key.check(raw)`` — сверка кода с хешем."""
        if not raw or not self.code_hash:
            return False
        if not self.is_active or self.is_used or self.is_expired:
            return False
        return hmac.compare_digest(str(self.code_hash), self.hash_code(raw))

    @classmethod
    def _model_check(cls, **kwargs):
        """Системная проверка модели — то, что Django ждёт от ``check``."""
        return super().check(**kwargs)
