"""Тесты импорта и показа расписания."""
from datetime import date, timedelta
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

    def test_schedule_shows_table_and_dates(self):
        """Таблица с днями и диапазоном дат текущей недели семестра."""
        from .views import semester_monday, semester_week_of

        self.student.profile.group = Group.objects.get(code='ИСТ-201')
        self.student.profile.save()
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Раздел в разработке')
        week = semester_week_of(date.today())
        monday = semester_monday(week)
        sunday = monday + timedelta(days=6)
        self.assertContains(response, f'неделя № {week}')
        self.assertContains(response, f'{monday.strftime("%d.%m")} — {sunday.strftime("%d.%m.%Y")}')
        self.assertContains(response, 'Базы данных')
        self.assertContains(response, 'Учебный план · вся программа обучения')

    def test_week_arrows_and_limits(self):
        """Стрелки листают недели; на границах семестра — выключены."""
        self.client.force_login(self.dean)
        first = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201', 'week': '1'})
        self.assertContains(first, 'week=2')
        self.assertContains(first, 'dir=next')
        self.assertContains(first, 'is-disabled')  # стрелка «назад» неактивна
        last = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201', 'week': '18'})
        self.assertContains(last, 'is-disabled')  # стрелка «вперёд» неактивна
        self.assertNotContains(last, 'week=19')

    def test_week_switch_changes_dates(self):
        self.client.force_login(self.dean)
        w12 = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201', 'week': '12'})
        w13 = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201', 'week': '13', 'dir': 'next'})
        self.assertContains(w12, '16.11 — 22.11.2026')
        self.assertContains(w13, '23.11 — 29.11.2026')
        self.assertContains(w13, 'sched-in-next')

    def test_staff_can_switch_group(self):
        self.client.force_login(self.dean)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-101'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ИСТ-101')
        self.assertContains(response, 'class="group-switch"')

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

    def test_parity_follows_selected_week(self):
        """Занятия чёт/нечёт показываются по чётности ВЫБРАННОЙ недели."""
        from .models import Lesson

        Lesson.objects.create(group=self.group, weekday=2, pair=1, week='odd', subject='Только нечёт')
        Lesson.objects.create(group=self.group, weekday=2, pair=2, week='even', subject='Только чёт')
        self.client.force_login(self.student)
        # неделя 12 семестра: 16–22.11.2026, ISO 47 → нечётная
        odd = self.client.get(reverse('schedule:schedule'), {'week': '12'})
        self.assertContains(odd, '<b>Только нечёт</b>')
        self.assertNotContains(odd, '<b>Только чёт</b>')
        # неделя 13: 23–29.11.2026, ISO 48 → чётная
        even = self.client.get(reverse('schedule:schedule'), {'week': '13'})
        self.assertContains(even, '<b>Только чёт</b>')
        self.assertNotContains(even, '<b>Только нечёт</b>')
        # предмет «каждую неделю» виден в таблице в обеих
        self.assertContains(odd, '<b>Философия</b>')
        self.assertContains(even, '<b>Философия</b>')

    def test_curriculum_block_lists_program(self):
        """Блок учебного плана: все дисциплины программы по семестрам."""
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        self.assertContains(response, 'Учебный план · вся программа обучения')
        self.assertContains(response, 'Математический анализ')
        self.assertContains(response, 'Иностранный язык')
        # многолетняя дисциплина — отрезок по семестрам
        self.assertContains(response, 'Иностранный язык: 1–4 семестры')
        self.assertContains(response, '>3</b><em>сем.</em>')

    def test_curriculum_marks_current_semester(self):
        from datetime import date

        from .views import current_program_semester

        expected = current_program_semester(2, date.today())  # ИСТ-201 — 2 курс
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        self.assertContains(response, f'is-now" style="--w: {expected}"')


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
