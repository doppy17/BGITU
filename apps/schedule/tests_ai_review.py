"""Тесты ИИ-разбора: факты считает код, модель пишет только резюме."""
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files import File
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .ai_review import (
    build_plan_facts,
    build_schedule_facts,
    clean_summary,
    review_import,
    review_plan_file,
    review_schedule_file,
)
from .models import Lesson, ScheduleImport
from .services import run_import

FIXTURE = settings.BASE_DIR / 'fixtures/schedule/bgitu-2026-27-1semestr-IVT-IST-PI-PRI.xls'
PLAN = settings.BASE_DIR / 'fixtures/plan/plan.json'
TEST_MEDIA = tempfile.mkdtemp(prefix='bgitu-ai-review-media-')


def make_import():
    with FIXTURE.open('rb') as handle:
        record = ScheduleImport(original_name=FIXTURE.name)
        record.file.save(FIXTURE.name, File(handle), save=True)
    run_import(record)
    return record


def lesson(group, weekday=1, pair=2, week='all', subject='Физика', room='307',
           building='1', teacher='Петров П.П.', subgroup=''):
    return {
        'group': group, 'weekday': weekday, 'pair': pair, 'week': week,
        'subject': subject, 'room': room, 'building': building,
        'teacher_name': teacher, 'subgroup': subgroup, 'kind': '',
    }


class SummaryTests(TestCase):
    def test_caps_points_and_length(self):
        raw = '\n'.join(f'{index}. ' + 'Очень длинный вывод ' * 20 for index in range(1, 9))
        points = clean_summary(raw)
        self.assertLessEqual(len(points), 5)
        self.assertLessEqual(len('\n'.join(f'• {point}' for point in points)), 500)

    def test_strips_numbering_and_markdown(self):
        points = clean_summary('**1. Первый пункт**\n2) Второй пункт\n- Третий')
        self.assertEqual(len(points), 3)
        self.assertEqual(points[0], 'Первый пункт')
        self.assertEqual(points[2], 'Третий')

    def test_paragraph_is_split_into_sentences(self):
        raw = 'Первое предложение о файле. Второе предложение с числом 268. Третье предложение.'
        points = clean_summary(raw)
        self.assertEqual(len(points), 3)


class ScheduleFactsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.record = make_import()

    def test_import_counters_match_database(self):
        review = review_import(self.record, use_llm=False)
        lessons = Lesson.objects.filter(source=self.record)
        self.assertEqual(review['stats']['lessons'], lessons.count())
        self.assertEqual(review['stats']['groups'], lessons.values('group').distinct().count())
        self.assertEqual(
            review['stats']['teachers'],
            lessons.exclude(teacher_name='').values('teacher_name').distinct().count(),
        )
        self.assertEqual(
            sum(review['stats']['by_week'].values()),
            lessons.count(),
        )

    def test_real_file_matches_imported_record(self):
        """Разбор файла без базы даёт те же цифры, что разбор импорта."""
        from_file = review_schedule_file(FIXTURE, use_llm=False)
        from_db = review_import(self.record, use_llm=False)
        for key in ('lessons', 'groups', 'teachers', 'rooms'):
            self.assertEqual(from_file['stats'][key], from_db['stats'][key], key)

    def test_room_and_teacher_conflicts_are_detected(self):
        room_facts = build_schedule_facts([
            lesson('ИСТ-201', subject='Физика', teacher='Петров П.П.'),
            lesson('ИВТ-101', subject='Информатика', teacher='Сидоров С.С.'),
        ])
        room_codes = {item['code'] for item in room_facts['issues'] if item['count']}
        self.assertIn('room_conflict', room_codes)

        teacher_facts = build_schedule_facts([
            lesson('ИСТ-201', room='307', subject='Физика', teacher='Петров П.П.'),
            lesson('ИВТ-101', room='314', subject='Информатика', teacher='Петров П.П.'),
        ])
        teacher_codes = {item['code'] for item in teacher_facts['issues'] if item['count']}
        self.assertIn('teacher_conflict', teacher_codes)

    def test_stream_lecture_is_not_a_conflict(self):
        """Одна дисциплина, преподаватель и аудитория у двух групп — норма."""
        facts = build_schedule_facts([
            lesson('ИСТ-201', subject='Физика', teacher='Петров П.П.'),
            lesson('ИВТ-101', subject='Физика', teacher='Петров П.П.'),
        ])
        codes = {item['code'] for item in facts['issues'] if item['count']}
        self.assertNotIn('room_conflict', codes)
        self.assertNotIn('teacher_conflict', codes)

    def test_odd_and_even_weeks_do_not_conflict(self):
        facts = build_schedule_facts([
            lesson('ИСТ-201', week='odd', subject='Физика'),
            lesson('ИВТ-101', week='even', subject='Информатика'),
        ])
        codes = {item['code'] for item in facts['issues'] if item['count']}
        self.assertNotIn('room_conflict', codes)

    def test_suspects_and_out_of_grid(self):
        facts = build_schedule_facts([
            lesson('ИСТ-201', pair=12, subject='Итого'),
            lesson('группа без кода', subject='X'),
        ])
        codes = {item['code'] for item in facts['issues'] if item['count']}
        self.assertIn('out_of_grid', codes)
        self.assertIn('suspects', codes)


class ModelSummaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.record = make_import()

    def test_summary_comes_from_model_and_uses_facts(self):
        prompts = []

        def fake_ask(prompt, max_tokens=120):
            prompts.append(prompt)
            return 'Файл разобран без ошибок.\nПроверьте пересечения аудиторий.'

        with mock.patch('apps.schedule.ai_review.ask_local', side_effect=fake_ask):
            review = review_import(self.record)
        self.assertTrue(review['llm']['available'])
        self.assertEqual(len(review['summary_points']), 2)
        self.assertLessEqual(len(review['summary']), 500)
        self.assertIn('Занятий:', prompts[0])
        self.assertIn(str(review['stats']['lessons']), prompts[0])
        self.assertIn('не добавляй числа', prompts[0])

    def test_fallback_without_model_keeps_facts(self):
        with mock.patch('apps.schedule.ai_review.ask_local', return_value=None):
            review = review_import(self.record)
        self.assertFalse(review['llm']['available'])
        self.assertEqual(review['llm']['note'], 'локальная модель недоступна')
        self.assertIsNone(review['summary'])
        self.assertEqual(review['stats']['lessons'], Lesson.objects.filter(source=self.record).count())

    def test_summary_with_invented_number_is_rejected(self):
        """Число, которого нет в фактах, — повод выбросить резюме."""
        with mock.patch('apps.schedule.ai_review.ask_local', return_value='Найдено 999 занятий.'):
            review = review_import(self.record)
        self.assertFalse(review['llm']['available'])
        self.assertIsNone(review['summary'])
        self.assertIn('числа', review['llm']['note'])

    def test_local_llm_import_is_lazy_and_safe(self):
        """Если рантайма модели нет, ask_local тихо возвращает None."""
        from .ai_review import ask_local

        with mock.patch.dict('sys.modules', {'apps.core.local_llm': None}):
            self.assertIsNone(ask_local('проверка', max_tokens=5))


class PlanFactsTests(TestCase):
    def test_plan_json_parses_into_semesters(self):
        review = review_plan_file(PLAN, use_llm=False)
        self.assertEqual(review['stats']['disciplines'], 40)
        self.assertEqual(review['stats']['max_semester'], 8)
        self.assertEqual(review['stats']['missing_semester'], 0)
        self.assertFalse([item for item in review['issues'] if item['count']])

    def test_plan_problems_are_counted(self):
        facts = build_plan_facts([
            {'subject': 'Физика', 'semesters': []},
            {'subject': 'Физика', 'semesters': [1, 9]},
            {'subject': 'Информатика', 'semesters': [1]},
            {'subject': 'Итого', 'semesters': [2]},
        ])
        codes = {item['code'] for item in facts['issues'] if item['count']}
        self.assertIn('no_semester', codes)
        self.assertIn('bad_semester', codes)
        self.assertIn('duplicates', codes)
        self.assertIn('suspects', codes)


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class AIReviewEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.record = make_import()
        cls.staff = User.objects.create_user('ai-staff', password='demo12345', is_staff=True)
        cls.student = User.objects.create_user('ai-student', password='demo12345')

    def url(self):
        return reverse('schedule:ai_review', args=[self.record.pk])

    def test_staff_gets_json_facts_even_without_model(self):
        self.client.force_login(self.staff)
        with mock.patch('apps.schedule.ai_review.ask_local', return_value=None):
            response = self.client.post(self.url())
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertFalse(data['llm']['available'])
        self.assertEqual(data['stats']['lessons'], 268)
        self.assertTrue(data['highlights'])
        self.assertIn('issues', data)

    def test_non_staff_is_forbidden(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(self.url()).status_code, 403)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url()).status_code, 405)

    def test_staff_sees_ai_block_on_import_page(self):
        user = User.objects.create_superuser('ai-dev', password='demo12345')
        self.client.force_login(user)
        response = self.client.get(reverse('schedule:import'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="aiReview"')
        self.assertContains(response, 'js-ai-review')


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class AIReviewCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.record = make_import()

    def test_command_writes_markdown_report(self):
        out = Path(tempfile.mkdtemp(prefix='bgitu-ai-reviews-'))
        with mock.patch('apps.schedule.ai_review.ask_local', return_value=None):
            call_command(
                'ai_review_schedule',
                '--file', str(FIXTURE),
                '--plan', str(PLAN),
                '--out', str(out),
                stdout=StringIO(),
            )
        reports = list(out.glob('*.md'))
        self.assertEqual(len(reports), 1)
        text = reports[0].read_text(encoding='utf-8')
        self.assertIn('# ИИ-разбор · Расписание', text)
        self.assertIn('# ИИ-разбор · Учебный план', text)
        self.assertIn(f'**Занятий:** {Lesson.objects.filter(source=self.record).count()}', text)
        self.assertIn('**Дисциплин:** 40', text)
        self.assertIn('Локальная модель недоступна', text)

    def test_command_defaults_to_latest_import(self):
        out = Path(tempfile.mkdtemp(prefix='bgitu-ai-reviews-'))
        call_command('ai_review_schedule', '--no-llm', '--out', str(out), stdout=StringIO())
        reports = list(out.glob('*.md'))
        self.assertEqual(len(reports), 1)
        self.assertIn('bgitu-2026-27-1semestr', reports[0].name)
