"""Тесты импорта и показа расписания."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files import File
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Role
from apps.education.models import Group

from .models import Lesson, ScheduleImport
from .services import run_import

FIXTURE = settings.BASE_DIR / 'fixtures/schedule/bgitu-2026-27-1semestr-IVT-IST-PI-PRI.xls'


class ScheduleImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student_role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        cls.dean_role = Role.objects.create(code='dean', name='Деканат', can_manage_schedule=True)
        cls.student = User.objects.create_user('student', password='demo12345')
        cls.student.profile.role = cls.student_role
        cls.student.profile.save()
        cls.dean = User.objects.create_user('dean', password='demo12345')
        cls.dean.profile.role = cls.dean_role
        cls.dean.profile.save()

        with FIXTURE.open('rb') as handle:
            record = ScheduleImport(original_name=FIXTURE.name)
            record.file.save(FIXTURE.name, File(handle), save=True)
        run_import(record)
        cls.record = record

    def test_import_fills_lessons_and_groups(self):
        self.assertTrue(FIXTURE.exists(), 'нет файла расписания')
        self.assertGreater(Lesson.objects.count(), 200)
        self.assertGreaterEqual(Group.objects.count(), 10)
        self.assertTrue(Group.objects.filter(code='ИСТ-201').exists())
        self.assertEqual(self.record.lessons_count, Lesson.objects.count())

    def test_lessons_have_subject_and_room(self):
        lesson = Lesson.objects.filter(group__code='ИСТ-201').first()
        self.assertTrue(lesson.subject)
        self.assertTrue(lesson.teacher_name)
        self.assertTrue(lesson.room)
        self.assertIn(lesson.weekday, range(1, 7))
        self.assertIn(lesson.pair, range(1, 7))

    def test_schedule_page_is_wip(self):
        """Раздел «Расписание» в разработке — страница открывается, но без данных."""
        self.student.profile.group = Group.objects.get(code='ИСТ-201')
        self.student.profile.save()
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Раздел в разработке')
        self.assertNotContains(response, 'Базы данных')

    def test_schedule_page_wip_for_staff(self):
        self.client.force_login(self.dean)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-101'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Раздел в разработке')

    def test_student_cannot_open_import(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse('schedule:import')).status_code, 403)


class WeekParityTests(TestCase):
    """Чётность недели: без пометок в файле — одно расписание, без переключателя."""

    @classmethod
    def setUpTestData(cls):
        from apps.education.models import Group
        from .models import Lesson

        cls.group = Group.objects.create(code='ИСТ-201', course=2)
        cls.student_role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        cls.student = User.objects.create_user('pstudent', password='demo12345')
        cls.student.profile.role = cls.student_role
        cls.student.profile.group = cls.group
        cls.student.profile.save()
        Lesson.objects.create(group=cls.group, weekday=1, pair=1, week='all', subject='Философия')

    def test_parser_detects_week_marker(self):
        from .import_service import parse_cell

        self.assertEqual(parse_cell('Физика нечётная\n314 (Симохин С.П.)')['week'], 'odd')
        self.assertEqual(parse_cell('Математика чётн.')['week'], 'even')
        self.assertEqual(parse_cell('Базы данных\n482 (Козлова И.Р.)')['week'], 'all')

    def test_schedule_wip_with_parity_lessons(self):
        """Раздел WIP даже при наличии недельных занятий."""
        from .models import Lesson

        Lesson.objects.create(group=self.group, weekday=2, pair=1, week='odd', subject='Только нечёт')
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'), {'week': 'odd'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Раздел в разработке')
        self.assertNotContains(response, 'Только нечёт')


class ServerImportPathTests(TestCase):
    """Импорт по пути: только файлы расписания из fixtures/schedule."""

    @classmethod
    def setUpTestData(cls):
        cls.dean_role = Role.objects.create(code='dean', name='Деканат', can_manage_schedule=True)
        cls.dean = User.objects.create_user('pdean', password='demo12345')
        cls.dean.profile.role = cls.dean_role
        cls.dean.profile.save()

    def test_rejects_path_outside_schedule_dir(self):
        self.client.force_login(self.dean)
        for bad in ('../../config/settings/base.py', '/etc/passwd', 'manage.py', ''):
            response = self.client.post(reverse('schedule:import_server'), {'path': bad}, follow=True)
            self.assertContains(response, 'Некорректный путь к файлу.', msg_prefix=bad)
