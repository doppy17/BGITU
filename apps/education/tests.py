"""Тесты учебного процесса: курсы, работы, оценки, посещаемость."""
import shutil
import tempfile
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Role

from .curriculum import plan_code_for_group, resolve_curriculum, semester_winners
from .models import (
    Assignment, Attendance, Course, CurriculumEntry, Enrollment, Grade, Group,
    PlanSource, Submission,
)
from .plan_import import (
    detect_direction, entries_from_rows, entries_to_segments, parse_plan, subject_key,
)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='bgitu-test-media-'))
class EducationFlowTests(TestCase):
    def setUp(self):
        self.student_role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        self.teacher_role = Role.objects.create(code='teacher', name='Преподаватель', can_create_courses=True, can_grade=True)
        self.group = Group.objects.create(code='ИСТ-201', course=2)
        self.student = self.make_user('student', self.student_role, self.group)
        self.student2 = self.make_user('student2', self.student_role, self.group)
        self.teacher = self.make_user('teacher', self.teacher_role)
        self.course = Course.objects.create(
            title='Базы данных', code='БД-201', group=self.group, teacher=self.teacher, semester=3,
        )
        for student in (self.student, self.student2):
            Enrollment.objects.create(course=self.course, student=student)

    def make_user(self, username, role, group=None):
        user = User.objects.create_user(username=username, password='demo12345')
        profile = user.profile
        profile.role = role
        profile.group = group
        profile.save()
        return user

    def test_teacher_creates_course_and_students_enroll(self):
        self.client.force_login(self.teacher)
        response = self.client.post(reverse('education:course_create'), {
            'title': 'Веб-разработка', 'code': 'WEB-201', 'group': self.group.id,
            'semester': '4', 'description': 'Практикум',
        })
        course = Course.objects.get(code='WEB-201')
        self.assertRedirects(response, reverse('education:course_detail', args=[course.id]))
        self.assertEqual(course.enrollments.count(), 2)
        self.assertEqual(course.teacher, self.teacher)

    def test_student_uploads_work_and_teacher_reviews(self):
        assignment = Assignment.objects.create(
            course=self.course, title='Лабораторная 1', created_by=self.teacher,
        )
        self.client.force_login(self.student)
        response = self.client.post(
            reverse('education:submit_work', args=[assignment.id]),
            {'file': SimpleUploadedFile('work.txt', b'lab result', content_type='text/plain'), 'comment': 'Готово'},
        )
        self.assertRedirects(response, reverse('education:course_detail', args=[self.course.id]))
        submission = Submission.objects.get(assignment=assignment, student=self.student)
        self.assertEqual(submission.status, Submission.STATUS_SUBMITTED)
        self.assertTrue(submission.file.name.endswith('.txt'))

        self.client.force_login(self.teacher)
        self.client.post(
            reverse('education:review_submission', args=[submission.id]),
            {'status': 'reviewed', 'grade': '5', 'teacher_comment': 'Отлично'},
        )
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.STATUS_REVIEWED)
        self.assertEqual(submission.grade, '5')
        self.assertEqual(submission.reviewed_by, self.teacher)

    def test_student_cannot_upload_to_foreign_course(self):
        foreign_group = Group.objects.create(code='ПИ-201', course=2)
        foreign_course = Course.objects.create(title='Чужой', code='X-1', group=foreign_group, teacher=self.teacher)
        assignment = Assignment.objects.create(course=foreign_course, title='Чужая лаба', created_by=self.teacher)
        self.client.force_login(self.student)
        response = self.client.post(
            reverse('education:submit_work', args=[assignment.id]),
            {'file': SimpleUploadedFile('work.txt', b'x')},
        )
        self.assertRedirects(response, reverse('education:courses'))
        self.assertFalse(Submission.objects.filter(assignment=assignment, student=self.student).exists())

    def test_grade_and_attendance_by_teacher(self):
        self.client.force_login(self.teacher)
        self.client.post(
            reverse('education:set_grade', args=[self.course.id]),
            {'student': self.student.id, 'kind': 'exam', 'value': '5', 'date': date.today().isoformat()},
        )
        grade = Grade.objects.get(course=self.course, student=self.student, kind='exam')
        self.assertEqual(grade.value, '5')

        response = self.client.post(
            reverse('education:attendance', args=[self.course.id]),
            {'date': date.today().isoformat(), f'status_{self.student.id}': 'absent',
             f'status_{self.student2.id}': 'present'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Attendance.objects.filter(course=self.course, date=date.today()).count(), 2)
        self.assertEqual(
            Attendance.objects.get(course=self.course, student=self.student, date=date.today()).status,
            Attendance.STATUS_ABSENT,
        )

    def test_student_cannot_grade_or_mark_attendance(self):
        self.client.force_login(self.student)
        self.assertEqual(
            self.client.post(reverse('education:set_grade', args=[self.course.id]), {'student': self.student.id, 'kind': 'exam', 'value': '5'}).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse('education:attendance', args=[self.course.id])).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse('education:journal', args=[self.course.id])).status_code,
            403,
        )

    def test_foreign_teacher_cannot_open_journal(self):
        other = self.make_user('teacher2', self.teacher_role)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('education:journal', args=[self.course.id])).status_code, 403)


# --------------------------------------------------------------------------- #
# Учебный план: загрузка из файлов, приоритет источников, удаление
# --------------------------------------------------------------------------- #

PLAN_HEADER = [
    'Дисциплина', '1 семестр', '2 семестр', '3 семестр', '4 семестр',
    '5 семестр', '6 семестр', '7 семестр', '8 семестр',
]


def plan_bytes(entries, title='Учебный план 09.03.02 «Информационные системы и технологии»'):
    """Минимальный .xlsx плана: шапка семестров и отметки «+» по колонкам."""
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append([title])
    sheet.append(PLAN_HEADER)
    for name, semesters in entries:
        sheet.append([name] + ['+' if number in semesters else '' for number in range(1, 9)])
    buffer = __import__('io').BytesIO()
    book.save(buffer)
    return buffer.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='bgitu-plan-media-'))
class PlanImportTests(TestCase):
    """Многoфайловый импорт плана: все файлы в БД, свежий побеждает, удаление чистит."""

    @classmethod
    def setUpTestData(cls):
        cls.student_role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        cls.dean_role = Role.objects.create(code='dean', name='Деканат', can_manage_schedule=True)
        cls.group = Group.objects.create(
            code='ИСТ-201', course=2, program='09.03.02 «Информационные системы и технологии»',
        )
        cls.student = User.objects.create_user('plan_student', password='demo12345')
        cls.student.profile.role = cls.student_role
        cls.student.profile.group = cls.group
        cls.student.profile.save()
        cls.dean = User.objects.create_user('plan_dean', password='demo12345')
        cls.dean.profile.role = cls.dean_role
        cls.dean.profile.save()

    def upload(self, *files):
        payload = [
            SimpleUploadedFile(
                name, content,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )
            for name, content in files
        ]
        self.client.force_login(self.dean)
        return self.client.post(
            reverse('education:plan_import'), {'file': payload, 'group': 'ИСТ-201'}, follow=True,
        )

    def test_multiple_files_are_all_stored_and_parsed(self):
        self.upload(
            ('Учебный план 09.03.02 ИСТ 2026.xlsx', plan_bytes([('Математика', [1, 2]), ('Базы данных', [3])])),
            ('09.03.01-ivt-2026.xlsx', plan_bytes([('Информатика', [1])], title='План 09.03.01')),
        )
        self.assertEqual(PlanSource.objects.count(), 2)
        ist = PlanSource.objects.get(direction_code='ist')
        ivt = PlanSource.objects.get(direction_code='09-03-01')
        self.assertEqual(ist.entries_count, 2)  # Математика 1–2 и Базы данных 3
        self.assertEqual(ivt.entries_count, 1)
        self.assertEqual(ist.uploaded_by, self.dean)
        self.assertGreater(ist.size, 0)
        self.assertIn('09.03.02', ist.direction)
        self.assertEqual(CurriculumEntry.objects.filter(source=ist).count(), 2)
        self.assertTrue(CurriculumEntry.objects.filter(
            source=ist, subject='Математика', semester_from=1, semester_to=2,
        ).exists())
        self.assertEqual(
            {row['subject'] for row in resolve_curriculum('ist')},
            {'Математика', 'Базы данных'},
        )

    def test_same_direction_reupload_supersedes_but_keeps_old(self):
        """Файл того же направления заменяет план по семестрам, старый остаётся в БД."""
        self.upload(('09.03.02-ist-2025.xlsx', plan_bytes([('Математика', [1, 2])])))
        old = PlanSource.objects.get()
        self.upload(('09.03.02-ist-2026.xlsx', plan_bytes([('Математика', [2])])))
        new = PlanSource.objects.exclude(pk=old.pk).get()

        self.assertEqual(PlanSource.objects.count(), 2)  # любой файл можно удалить отдельно
        winners = semester_winners('ist')
        key = subject_key('Математика')
        self.assertEqual(winners[(key, 2)].source_id, new.pk)
        # Семестр 1 в новом файле не описан — остаётся из старого, без потерь.
        self.assertEqual(winners[(key, 1)].source_id, old.pk)
        self.assertEqual(
            {row['subject']: row['segments'] for row in resolve_curriculum('ist')},
            {'Математика': [(1, 2)]},
        )

        self.client.force_login(self.dean)
        self.client.post(
            reverse('education:plan_source_delete', args=[new.pk]), {'group': 'ИСТ-201'}, follow=True,
        )
        winners = semester_winners('ist')
        self.assertEqual(winners[(key, 1)].source_id, old.pk)
        self.assertEqual(winners[(key, 2)].source_id, old.pk)
        self.assertTrue(CurriculumEntry.objects.filter(source=old).exists())

    def test_newest_file_wins_conflict_old_stays_in_db(self):
        self.upload(('09.03.02-ist-old.xlsx', plan_bytes([('Базы данных', [3, 4])])))
        old = PlanSource.objects.get()
        self.upload(('09.03.02-ist-new.xlsx', plan_bytes([('Базы данных', [3, 4])])))
        new = PlanSource.objects.exclude(pk=old.pk).get()

        winners = semester_winners('ist')
        key = subject_key('Базы данных')
        self.assertEqual(winners[(key, 3)].source_id, new.pk)
        self.assertEqual(winners[(key, 4)].source_id, new.pk)
        # Старая запись не удалена — просто перекрыта свежим файлом.
        self.assertTrue(CurriculumEntry.objects.filter(source=old, subject='Базы данных').exists())
        # Удаление свежего файла возвращает старый план.
        self.client.force_login(self.dean)
        self.client.post(reverse('education:plan_source_delete', args=[new.pk]), follow=True)
        self.assertEqual(semester_winners('ist')[(key, 3)].source_id, old.pk)

    def test_delete_source_removes_only_its_disciplines(self):
        self.upload(
            ('09.03.02-ist-2026.xlsx', plan_bytes([('Математика', [1])])),
            ('09.03.01-ivt-2026.xlsx', plan_bytes([('Информатика', [1])], title='План 09.03.01')),
        )
        other = PlanSource.objects.get(direction_code='09-03-01')
        self.client.force_login(self.dean)
        response = self.client.post(
            reverse('education:plan_source_delete', args=[other.pk]), {'group': 'ИСТ-201'}, follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PlanSource.objects.filter(pk=other.pk).exists())
        self.assertFalse(CurriculumEntry.objects.filter(source_id=other.pk).exists())
        self.assertTrue(CurriculumEntry.objects.filter(code='ist').exists())
        self.assertEqual(PlanSource.objects.count(), 1)

    def test_group_and_semesters_survive_resolution(self):
        """Группа и все семестры дисциплины сохраняются, разрывы не склеиваются."""
        self.upload(('09.03.02-ist-2026.xlsx', plan_bytes([
            ('Физическая культура', [1, 3]),
            ('Индустриальная разработка', [1, 2, 3, 4, 5, 6, 7, 8]),
            ('Преддипломная практика', [8]),
        ])))
        self.assertEqual(plan_code_for_group(self.group), 'ist')
        rows = {row['subject']: row['segments'] for row in resolve_curriculum('ist')}
        self.assertEqual(rows['Физическая культура'], [(1, 1), (3, 3)])
        self.assertEqual(rows['Индустриальная разработка'], [(1, 8)])
        self.assertEqual(rows['Преддипломная практика'], [(8, 8)])

        self.client.force_login(self.dean)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201'})
        self.assertContains(response, 'Физическая культура: 1-й семестр')
        self.assertContains(response, 'Физическая культура: 3-й семестр')
        self.assertContains(response, 'Индустриальная разработка: 1–8 семестры')
        self.assertContains(response, 'ИСТ-201')

    def test_demo_entries_hidden_but_not_deleted_after_upload(self):
        demo = CurriculumEntry.objects.create(code='ist', subject='Демо-дисциплина', semester_from=1, semester_to=1)
        self.upload(('09.03.02-ist-2026.xlsx', plan_bytes([('Математика', [1])])))
        self.assertTrue(CurriculumEntry.objects.filter(pk=demo.pk).exists())
        self.assertNotIn('Демо-дисциплина', {row['subject'] for row in resolve_curriculum('ist')})
        self.client.force_login(self.dean)
        self.client.post(reverse('education:plan_source_delete', args=[PlanSource.objects.get().pk]), follow=True)
        self.assertIn('Демо-дисциплина', {row['subject'] for row in resolve_curriculum('ist')})

    def test_page_has_plan_modal_files_and_delete(self):
        self.upload(('09.03.02-ist-2026.xlsx', plan_bytes([('Математика', [1])])))
        source = PlanSource.objects.get()
        self.client.force_login(self.dean)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201'})
        html = response.content.decode()
        self.assertContains(response, 'id="planImportOpen"')
        self.assertContains(response, 'Обновить план')
        self.assertContains(response, '<dialog class="dialog sched-import" id="planImport"')
        self.assertContains(response, 'name="file"')
        self.assertIn('multiple', html)
        self.assertIn('09.03.02-ist-2026.xlsx', html)
        self.assertIn(reverse('education:plan_source_delete', args=[source.pk]), html)
        self.assertContains(response, 'data-import-delete')

        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        self.assertNotContains(response, 'id="planImportOpen"')
        self.assertNotContains(response, 'id="planImport"')

    def test_student_cannot_import_or_delete_plan(self):
        source = PlanSource.objects.create(
            original_name='plan.xlsx', direction='09.03.02', direction_code='ist',
        )
        self.client.force_login(self.student)
        response = self.client.post(
            reverse('education:plan_import'), {'file': [SimpleUploadedFile('x.xlsx', b'x')]},
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse('education:plan_source_delete', args=[source.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(PlanSource.objects.filter(pk=source.pk).exists())

    def test_bad_file_does_not_cancel_good_one(self):
        bad = SimpleUploadedFile('notes.txt', b'not a plan', content_type='text/plain')
        good = SimpleUploadedFile(
            '09.03.02-ist-2026.xlsx', plan_bytes([('Математика', [1])]),
            content_type='application/octet-stream',
        )
        self.client.force_login(self.dean)
        response = self.client.post(
            reverse('education:plan_import'), {'file': [bad, good]}, follow=True,
        )
        self.assertContains(response, 'notes.txt')
        self.assertEqual(PlanSource.objects.count(), 1)
        self.assertTrue(CurriculumEntry.objects.filter(subject='Математика').exists())


class PlanParserTests(TestCase):
    """Разбор таблиц и файлов плана, определение направления."""

    def test_wide_table_with_semester_columns(self):
        rows = [
            ['План 09.03.02'],
            PLAN_HEADER,
            ['Б1.О.1 Иностранный язык', '+', '+', '', '+', '', '', '', ''],
            ['Итого', '', '', '', '', '', '', '', ''],
        ]
        self.assertEqual(
            entries_from_rows(rows),
            [('Иностранный язык', [1, 2, 4])],
        )

    def test_long_table_with_semester_column(self):
        rows = [
            ['Дисциплина', 'Семестр'],
            ['Математика', '1'],
            ['Математика', '2'],
            ['Физика', '3'],
        ]
        self.assertEqual(
            entries_from_rows(rows),
            [('Математика', [1]), ('Математика', [2]), ('Физика', [3])],
        )

    def test_long_table_with_semester_range(self):
        rows = [
            ['Дисциплина', 'Семестр с', 'Семестр по'],
            ['Иностранный язык', '1', '4'],
        ]
        self.assertEqual(entries_from_rows(rows), [('Иностранный язык', [1, 2, 3, 4])])

    def test_docx_table_parsed(self):
        """docx — это zip с XML: таблица плана читается без python-docx."""
        import zipfile
        from xml.sax.saxutils import escape

        rows = [
            ['Дисциплина', '1 семестр', '2 семестр', '3 семестр'],
            ['Математика', '+', '+', ''],
        ]
        body = ''.join(
            '<w:tr>' + ''.join(
                f'<w:tc><w:p><w:r><w:t>{escape(str(cell))}</w:t></w:r></w:p></w:tc>'
                for cell in row
            ) + '</w:tr>'
            for row in rows
        )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body><w:tbl>{body}</w:tbl></w:body></w:document>'
        )
        path = Path(tempfile.mkdtemp(prefix='bgitu-plan-docx-')) / 'plan.docx'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('word/document.xml', xml)
        self.assertEqual(parse_plan(path), [('Математика', [1, 2])])

    def test_segments_merge_duplicates_and_split_gaps(self):
        self.assertEqual(
            entries_to_segments([
                ('Математика', [1, 2]),
                ('математика ', [2, 3]),
                ('Физическая культура', [1, 3]),
            ]),
            [('Математика', 1, 3), ('Физическая культура', 1, 1), ('Физическая культура', 3, 3)],
        )

    def test_detect_direction_from_filename_and_content(self):
        self.assertEqual(detect_direction('Учебный план 09.03.02 ИСТ 2026.pdf')['direction_code'], 'ist')
        self.assertEqual(detect_direction('plan-ivt-2026.xlsx')['direction_code'], 'ivt')
        with self.assertRaises(ValueError):
            detect_direction('plan.xlsx')

    def test_pdf_fixture_parses_block_one(self):
        if not shutil.which('pdftotext'):
            self.skipTest('pdftotext не установлен')
        fixture = Path(settings.BASE_DIR) / 'fixtures/plan/Учебный план 09.03.02 ИСТ 2026 (БГИТУ, очная, бакалавриат).pdf'
        for path in (fixture, *Path(settings.BASE_DIR, 'fixtures/plan').glob('*.pdf')):
            if path.exists():
                entries = parse_plan(path)
                self.assertGreaterEqual(len(entries), 30)
                keys = {subject_key(name) for name, _ in entries}
                self.assertIn('математика', keys)
                self.assertIn('базы данных', keys)
                semesters = dict(entries)
                self.assertEqual(sorted(semesters['Базы данных']), [1, 2, 3, 4])
                return
        self.skipTest('нет файла плана в fixtures/plan')
