"""Тесты профиля студента: поля, история перемещений и успеваемость."""
import io
import shutil
import tempfile
from datetime import date

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from apps.education.models import Course, Grade, Group

from .models import Profile, ProfileHistory
from .performance import performance_by_semester, total_average


def png_upload(name='avatar.png'):
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), (180, 40, 40)).save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


class ProfileFieldsTests(TestCase):
    """Новые поля профиля сохраняются, производные значения считаются."""

    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(
            code='ИСТ-201', program='09.03.02 Информационные системы и технологии',
            course=2, institute='Инженерно-экономический институт',
        )
        cls.user = User.objects.create_user(
            'student-profile', password='demo12345', first_name='Георгий', last_name='Портнов',
        )

    def setUp(self):
        self.profile = self.user.profile
        self.media_root = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, True)

    def test_new_fields_are_saved(self):
        self.profile.group = self.group
        self.profile.photo = png_upload()
        self.profile.enrollment_year = 2024
        self.profile.department = 'Информационных технологий'
        self.profile.course_override = 3
        self.profile.record_book = 'ИСТ-201/24'
        self.profile.save()

        self.profile.refresh_from_db()
        self.assertTrue(self.profile.photo.name.startswith('profiles/'))
        self.assertEqual(self.profile.enrollment_year, 2024)
        self.assertEqual(self.profile.department, 'Информационных технологий')
        self.assertEqual(self.profile.course_override, 3)
        self.assertEqual(self.profile.record_book, 'ИСТ-201/24')

    def test_photo_is_optional_and_blank_by_default(self):
        self.assertFalse(self.profile.photo)
        self.assertIsNone(self.profile.enrollment_year)
        self.assertEqual(self.profile.department, '')
        self.assertIsNone(self.profile.course_override)

    def test_derived_properties_come_from_group(self):
        self.profile.group = self.group
        self.profile.save()
        self.assertEqual(self.profile.institute, 'Инженерно-экономический институт')
        self.assertEqual(self.profile.program, '09.03.02 Информационные системы и технологии')
        self.assertEqual(self.profile.full_name, 'Портнов Георгий')
        self.assertEqual(self.profile.course, 2)

    def test_course_uses_override_then_group(self):
        self.assertIsNone(self.profile.course)
        self.profile.group = self.group
        self.profile.save()
        self.assertEqual(self.profile.course, 2)

        self.profile.course_override = 4
        self.profile.save()
        self.assertEqual(self.profile.course, 4)

        self.profile.course_override = None
        self.profile.save()
        self.assertEqual(self.profile.course, 2)

    def test_properties_are_empty_without_group(self):
        self.assertEqual(self.profile.institute, '')
        self.assertEqual(self.profile.program, '')
        self.assertIsNone(self.profile.course)


class ProfileHistoryTests(TestCase):
    """Смена группы пишет историю; обычное сохранение — нет."""

    @classmethod
    def setUpTestData(cls):
        cls.group_a = Group.objects.create(
            code='ИСТ-101', program='09.03.02 ИСТ', course=1, institute='Инженерно-экономический институт',
        )
        cls.group_b = Group.objects.create(
            code='ИВТ-301', program='09.03.01 ИВТ', course=3, institute='Институт лесного комплекса',
        )
        cls.user = User.objects.create_user('move-student', password='demo12345')
        cls.profile = cls.user.profile
        cls.profile.group = cls.group_a
        cls.profile.save()

    def setUp(self):
        ProfileHistory.objects.all().delete()

    def test_group_change_creates_history_with_snapshot(self):
        self.profile.group = self.group_b
        self.profile.save()

        history = ProfileHistory.objects.get(user=self.user)
        self.assertEqual(history.kind, ProfileHistory.KIND_GROUP)
        self.assertEqual(history.from_value, 'ИСТ-101')
        self.assertEqual(history.to_value, 'ИВТ-301')
        self.assertIn('Институт: Инженерно-экономический институт → Институт лесного комплекса', history.comment)
        self.assertIn('Направление: 09.03.02 ИСТ → 09.03.01 ИВТ', history.comment)
        self.assertIn('Курс: 1 → 3', history.comment)

    def test_saving_without_group_change_does_not_write_history(self):
        self.profile.phone = '+7 900 000-00-00'
        self.profile.save()
        self.assertFalse(ProfileHistory.objects.exists())

    def test_update_fields_without_group_does_not_write_history(self):
        self.profile.group = self.group_b  # в памяти, но сохраняем только телефон
        self.profile.phone = '+7 900 111-11-11'
        self.profile.save(update_fields=['phone'])

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.group, self.group_a)
        self.assertFalse(ProfileHistory.objects.exists())

    def test_same_group_saved_again_does_not_duplicate(self):
        self.profile.group = self.group_b
        self.profile.save()
        self.profile.save()
        self.profile.refresh_from_db()
        self.profile.save()
        self.assertEqual(ProfileHistory.objects.filter(kind=ProfileHistory.KIND_GROUP).count(), 1)

    def test_initial_group_assignment_is_logged(self):
        user = User.objects.create_user('fresh-student', password='demo12345')
        profile = user.profile
        profile.group = self.group_a
        profile.save()

        history = ProfileHistory.objects.get(user=user)
        self.assertEqual(history.from_value, '')
        self.assertEqual(history.to_value, 'ИСТ-101')

    def test_history_str_is_readable(self):
        history = ProfileHistory.objects.create(
            user=self.user, kind=ProfileHistory.KIND_GROUP, from_value='ИСТ-101', to_value='ИВТ-301',
        )
        self.assertIn('ИСТ-101 → ИВТ-301', str(history))

    def test_loaddata_does_not_crash_or_write_history(self):
        """Фикстуры сохраняют профиль с raw=True — сигнал не должен ни падать, ни логировать."""
        from django.core import serializers
        from django.core.management import call_command

        self.profile.group = self.group_b
        payload = serializers.serialize('json', [self.group_b, self.profile])
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = f'{directory}/profile.json'
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(payload)

        call_command('loaddata', path, verbosity=0)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.group, self.group_b)
        self.assertFalse(ProfileHistory.objects.exists())


class PerformanceTests(TestCase):
    """Успеваемость по семестрам: средний балл, зачёты, экзамены и долги."""

    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user(
            'teacher-perf', password='demo12345', first_name='Пётр', last_name='Иванов',
        )
        cls.group = Group.objects.create(code='ИСТ-201', course=2)
        cls.student = User.objects.create_user('perf-student', password='demo12345')

        def course(title, code, semester):
            return Course.objects.create(
                title=title, code=code, group=cls.group, teacher=cls.teacher, semester=semester,
            )

        def grade(course_obj, kind, value, day):
            return Grade.objects.create(
                course=course_obj, student=cls.student, kind=kind, value=value,
                date=date(2026, 1, day), created_by=cls.teacher,
            )

        grade(course('Математика', 'MATH', 1), Grade.KIND_EXAM, '5', 10)
        grade(course('Физика', 'PHYS', 1), Grade.KIND_CREDIT, 'зачтено', 12)
        grade(course('История', 'HIST', 1), Grade.KIND_EXAM, '4', 15)

        grade(course('Химия', 'CHEM', 2), Grade.KIND_EXAM, '2', 10)
        grade(course('Английский язык', 'ENG', 2), Grade.KIND_CREDIT, 'не зачтено', 12)
        grade(course('Информатика', 'INFO', 2), Grade.KIND_TEST, '5', 14)

    def test_semesters_are_grouped_and_sorted(self):
        semesters = performance_by_semester(self.student)
        self.assertEqual([s['semester'] for s in semesters], [1, 2])
        self.assertEqual(len(semesters[0]['courses']), 3)
        self.assertEqual(len(semesters[1]['courses']), 3)

    def test_first_semester_summary(self):
        first = performance_by_semester(self.student)[0]
        self.assertEqual(first['average'], 4.5)
        self.assertEqual(first['credits'], 1)
        self.assertEqual(first['exams'], 2)
        self.assertFalse(first['has_debt'])

    def test_second_semester_summary_has_debt(self):
        second = performance_by_semester(self.student)[1]
        self.assertEqual(second['average'], 3.5)
        self.assertEqual(second['credits'], 0)
        self.assertEqual(second['exams'], 1)
        self.assertTrue(second['has_debt'])

    def test_course_items_have_all_fields(self):
        item = performance_by_semester(self.student)[0]['courses'][0]
        self.assertEqual(set(item), {'title', 'teacher', 'kind', 'value', 'date'})
        self.assertIn(item['title'], {'История', 'Математика', 'Физика'})
        self.assertEqual(item['teacher'], 'Иванов Пётр')
        self.assertIsInstance(item['date'], date)

    def test_total_average_across_semesters(self):
        # (5 + 4 + 2 + 5) / 4 — зачёты не входят в средний балл
        self.assertEqual(total_average(self.student), 4.0)

    def test_student_without_grades(self):
        empty = User.objects.create_user('no-grades', password='demo12345')
        self.assertEqual(performance_by_semester(empty), [])
        self.assertIsNone(total_average(empty))

    def test_credit_only_semester_has_no_numeric_average(self):
        user = User.objects.create_user('credit-only', password='demo12345')
        course = Course.objects.create(
            title='Физкультура', code='SPORT', group=self.group, teacher=self.teacher, semester=3,
        )
        Grade.objects.create(
            course=course, student=user, kind=Grade.KIND_CREDIT, value='зачтено',
            date=date(2026, 1, 20), created_by=self.teacher,
        )
        semester = performance_by_semester(user)[0]
        self.assertIsNone(semester['average'])
        self.assertEqual(semester['credits'], 1)
        self.assertFalse(semester['has_debt'])
