"""Сброс БД портала: один аккаунт-разработчик, расписание и учебный план на месте.

Удаляет ВСЕХ пользователей (вместе с ролями-заявками, курсами, работами,
оценками и посещаемостью — они висят на пользователях) и создаёт единственный
аккаунт developer. Справочники (группы, роли, звонки) сохраняются, расписание
переимпортируется из файла БГИТУ.

    python manage.py reset_db                  # пароль как у демо: demo12345
    python manage.py reset_db --password '...' # свой пароль
    python manage.py reset_db --keep-schedule  # не трогать занятия, только аккаунты
"""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files import File
from django.core.management.base import BaseCommand

from apps.accounts.models import Profile, Role, RoleRequest
from apps.education.models import Assignment, Attendance, Course, Enrollment, Grade, Group, Submission
from apps.schedule.models import Lesson, ScheduleImport
from apps.schedule.services import run_import

DEFAULT_PASSWORD = 'demo12345'
SCHEDULE_FILE = 'fixtures/schedule/bgitu-2026-27-1semestr-IVT-IST-PI-PRI.xls'
SCHEDULE_URL = 'https://bgitu.ru/studentu/raspisanie/ochnoe-obuchenie/2026-27/1-semestr/v8/'
DEVELOPER = {
    'username': 'developer',
    'password': DEFAULT_PASSWORD,
    'first_name': 'Илья',
    'last_name': 'Кузнецов',
    'email': 'i.kuznetsov@bgitu.ru',
}
DEVELOPER_ROLE = {
    'name': 'Разработчик',
    'description': 'Полный доступ ко всем разделам и данным, включая выдачу роли разработчика.',
    'can_manage_users': True,
    'can_manage_roles': True,
    'can_approve_roles': True,
    'can_manage_schedule': True,
    'can_create_courses': True,
    'can_grade': True,
    'can_view_all': True,
    'is_developer': True,
}


class Command(BaseCommand):
    help = 'Очищает БД портала до одного аккаунта developer и переимпортирует расписание.'

    def add_arguments(self, parser):
        parser.add_argument('--password', default=DEFAULT_PASSWORD, help='Пароль единственного аккаунта')
        parser.add_argument('--keep-schedule', action='store_true', help='Не переимпортировать занятия')
        parser.add_argument('--reset-catalog', action='store_true', help='Также удалить группы и курсы')

    def handle(self, *args, **options):
        # Удаляем в порядке зависимостей, чтобы не ловить защищённые внешние ключи.
        Submission.objects.all().delete()
        Assignment.objects.all().delete()
        Attendance.objects.all().delete()
        Grade.objects.all().delete()
        Enrollment.objects.all().delete()
        Course.objects.all().delete()
        RoleRequest.objects.all().delete()
        Profile.objects.all().delete()
        User.objects.all().delete()

        if options['keep_schedule']:
            Lesson.objects.update(source=None)
        ScheduleImport.objects.all().delete()
        if options['reset_catalog']:
            Lesson.objects.all().delete()
            Group.objects.all().delete()
        if not options['keep_schedule']:
            self.import_schedule()

        role, _ = Role.objects.update_or_create(code='developer', defaults=DEVELOPER_ROLE)
        user = User.objects.create_superuser(
            username=DEVELOPER['username'],
            email=DEVELOPER['email'],
            password=options['password'],
            first_name=DEVELOPER['first_name'],
            last_name=DEVELOPER['last_name'],
        )
        profile, _ = Profile.objects.get_or_create(user=user)
        profile.role = role
        profile.save()

        self.stdout.write(self.style.SUCCESS('БД очищена.'))
        self.stdout.write(f'Пользователей: {User.objects.count()} · ролей: {Role.objects.count()} · '
                          f'групп: {Group.objects.count()} · занятий: {Lesson.objects.count()}')
        self.stdout.write(f"Вход: {DEVELOPER['username']} / {options['password']}")

    def import_schedule(self):
        fixture = settings.BASE_DIR / SCHEDULE_FILE
        if not fixture.exists():
            self.stdout.write(self.style.WARNING(f'Файл расписания не найден: {SCHEDULE_FILE} — занятия не залиты.'))
            return
        with fixture.open('rb') as handle:
            record = ScheduleImport(original_name=Path(SCHEDULE_FILE).name, source_url=SCHEDULE_URL)
            record.file.save(Path(SCHEDULE_FILE).name, File(handle), save=True)
        run_import(record, source_url=SCHEDULE_URL, replace_all=True)
        self.stdout.write(f'Расписание: {record.lessons_count} занятий, {record.groups_count} групп.')
