"""Тесты полей профиля по образцу ЭОС БГИТУ (задача E1)."""
from datetime import date

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.education.models import Course, Grade, Group

from .models import Profile, transliterate
from .performance import grade_statistics


class TransliterationTests(SimpleTestCase):
    def test_russian_letters_are_transliterated(self):
        self.assertEqual(transliterate('Портнов'), 'Portnov')
        self.assertEqual(transliterate('Щербаков Яна'), 'Shcherbakov Yana')
        self.assertEqual(transliterate('Ёлкин'), 'Elkin')

    def test_empty_and_latin_text_is_kept(self):
        self.assertEqual(transliterate(''), '')
        self.assertEqual(transliterate('Smith'), 'Smith')


class ProfileEosFieldsTests(TestCase):
    """Новые поля сохраняются, авто-заполнение и расчётные свойства работают."""

    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(
            code='ИСТ-201', program='09.03.02 Информационные системы и технологии',
            course=2, institute='Инженерно-экономический институт',
        )
        cls.user = User.objects.create_user(
            'eos-student', password='demo12345', first_name='Георгий', last_name='Портнов',
        )
        cls.profile = cls.user.profile

    def test_new_eos_fields_are_saved(self):
        profile = self.profile
        profile.group = self.group
        profile.name_latin = 'Portnov Georgiy'
        profile.citizenship = 'Казахстанское'
        profile.plan_name = '09.03.02-2025.plx'
        profile.plan_code = '09.03.02'
        profile.scopus_id = '57200000000'
        profile.ticket_number = 'СТ-123456'
        profile.ticket_order = '№ 1234-с от 01.09.2025'
        profile.ticket_issue_date = date(2025, 9, 1)
        profile.education_level = Profile.LEVEL_BACHELOR
        profile.study_form = Profile.FORM_FULL_TIME
        profile.direction = '09.03.02 Информационные системы и технологии'
        profile.save()

        profile.refresh_from_db()
        self.assertEqual(profile.name_latin, 'Portnov Georgiy')
        self.assertEqual(profile.citizenship, 'Казахстанское')
        self.assertEqual(profile.plan_name, '09.03.02-2025.plx')
        self.assertEqual(profile.plan_code, '09.03.02')
        self.assertEqual(profile.scopus_id, '57200000000')
        self.assertEqual(profile.ticket_number, 'СТ-123456')
        self.assertEqual(profile.ticket_order, '№ 1234-с от 01.09.2025')
        self.assertEqual(profile.ticket_issue_date, date(2025, 9, 1))
        self.assertEqual(profile.education_level, Profile.LEVEL_BACHELOR)
        self.assertEqual(profile.study_form, Profile.FORM_FULL_TIME)
        self.assertEqual(profile.direction, '09.03.02 Информационные системы и технологии')

    def test_defaults_and_autofill_on_save(self):
        user = User.objects.create_user(
            'eos-autofill', password='demo12345', last_name='Щербаков', first_name='Яна',
        )
        profile = user.profile
        profile.record_book = 'ИСТ-201/25'
        profile.group = self.group
        profile.save()

        profile.refresh_from_db()
        self.assertEqual(profile.name_latin, 'Shcherbakov Yana')
        self.assertEqual(profile.citizenship, 'Российское')
        self.assertEqual(profile.ticket_number, 'ИСТ-201/25')
        self.assertEqual(profile.direction, self.group.program)

    def test_suggest_latin_uses_last_and_first_name(self):
        self.assertEqual(self.profile.suggest_latin(), 'Portnov Georgiy')

    def test_explicit_values_are_not_overwritten(self):
        profile = self.profile
        profile.name_latin = 'Portnov G.'
        profile.ticket_number = 'СТ-999999'
        profile.record_book = 'ИСТ-201/24'
        profile.save()

        profile.refresh_from_db()
        self.assertEqual(profile.name_latin, 'Portnov G.')
        self.assertEqual(profile.ticket_number, 'СТ-999999')

    def test_direction_falls_back_to_group_program(self):
        self.profile.group = self.group
        self.assertEqual(self.profile.direction_display, self.group.program)

        self.profile.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.direction, self.group.program)

    def test_choice_labels(self):
        profile = self.profile
        profile.education_level = Profile.LEVEL_MASTER
        profile.study_form = Profile.FORM_CORRESPONDENCE
        profile.save()

        profile.refresh_from_db()
        self.assertEqual(profile.get_education_level_display(), 'магистратура')
        self.assertEqual(profile.get_study_form_display(), 'заочная')

    def test_faculty_and_course_follow_group_and_overrides(self):
        profile = self.profile
        profile.group = self.group
        profile.save()
        self.assertEqual(profile.faculty, 'Инженерно-экономический институт')
        self.assertEqual(profile.course, 2)

        profile.institute_override = 'Институт лесного комплекса'
        profile.course_override = 4
        profile.save()
        self.assertEqual(profile.faculty, 'Институт лесного комплекса')
        self.assertEqual(profile.course, 4)

    def test_last_login_at_reads_user_last_login(self):
        self.assertIsNone(self.profile.last_login_at)

        self.user.last_login = timezone.now()
        self.user.save(update_fields=['last_login'])
        self.assertEqual(self.profile.last_login_at, self.user.last_login)


class ProfilePerformanceStatsTests(TestCase):
    """Средний балл и доли «5»/«4» считаются по Grade; без оценок — None/0."""

    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user(
            'eos-teacher', password='demo12345', first_name='Пётр', last_name='Иванов',
        )
        cls.group = Group.objects.create(code='ИСТ-301', program='09.03.02 ИСТ', course=3)
        cls.user = User.objects.create_user(
            'eos-perf', password='demo12345', first_name='Анна', last_name='Сидорова',
        )
        cls.profile = cls.user.profile

        def add_grade(title, code, value, kind=Grade.KIND_EXAM):
            course = Course.objects.create(
                title=title, code=code, group=cls.group, teacher=cls.teacher, semester=1,
            )
            Grade.objects.create(
                course=course, student=cls.user, kind=kind, value=value,
                date=date(2026, 1, 10), created_by=cls.teacher,
            )

        add_grade('Математика', 'MATH', '5')
        add_grade('Физика', 'PHYS', '5')
        add_grade('История', 'HIST', '4')
        add_grade('Химия', 'CHEM', '3')
        add_grade('Физкультура', 'SPORT', 'зачтено', Grade.KIND_CREDIT)

    def test_average_grade(self):
        self.assertEqual(self.profile.average_grade, 4.25)

    def test_shares_are_percentages_of_numeric_grades(self):
        self.assertEqual(self.profile.excellent_share, 50.0)
        self.assertEqual(self.profile.good_share, 25.0)

    def test_grade_statistics_dict(self):
        stats = grade_statistics(self.user)
        self.assertEqual(stats['average'], 4.25)
        self.assertEqual(stats['excellent_share'], 50.0)
        self.assertEqual(stats['good_share'], 25.0)
        self.assertEqual(stats['numeric_count'], 4)

    def test_no_grades_returns_none_and_zero(self):
        user = User.objects.create_user('eos-empty', password='demo12345')
        profile = user.profile

        self.assertIsNone(profile.average_grade)
        self.assertEqual(profile.excellent_share, 0)
        self.assertEqual(profile.good_share, 0)

        stats = grade_statistics(user)
        self.assertIsNone(stats['average'])
        self.assertEqual(stats['excellent_share'], 0)
        self.assertEqual(stats['good_share'], 0)


class ProfileAdminTests(TestCase):
    """Новые поля доступны в админке, list_display остаётся прежним."""

    def test_admin_fieldsets_cover_eos_fields(self):
        model_admin = admin.site._registry[Profile]
        fields = [
            name
            for _, options in model_admin.fieldsets
            for name in options['fields']
        ]
        for field in (
            'name_latin', 'citizenship', 'plan_name', 'plan_code', 'scopus_id',
            'ticket_number', 'ticket_order', 'ticket_issue_date',
            'education_level', 'study_form', 'direction',
        ):
            self.assertIn(field, fields)

    def test_admin_list_display_is_unchanged(self):
        model_admin = admin.site._registry[Profile]
        self.assertEqual(model_admin.list_display, ('user', 'role', 'group', 'record_book'))
