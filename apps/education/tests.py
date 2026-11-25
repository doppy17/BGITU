"""Тесты учебного процесса: курсы, работы, оценки, посещаемость."""
import shutil
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import quote

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

    def test_course_form_saves_attestation_settings(self):
        self.client.force_login(self.teacher)
        self.client.post(reverse('education:course_create'), {
            'title': 'Веб-разработка', 'code': 'WEB-201', 'group': self.group.id,
            'semester': '4', 'attestation_count': '4', 'admission_score': '45',
            'control_form': 'credit',
        })
        course = Course.objects.get(code='WEB-201')
        self.assertEqual(course.attestation_count, 4)
        self.assertEqual(course.admission_score, 45)
        self.assertEqual(course.control_form, Course.FORM_CREDIT)

    def test_course_form_rejects_bad_attestation_count(self):
        self.client.force_login(self.teacher)
        response = self.client.post(reverse('education:course_create'), {
            'title': 'Веб-разработка', 'code': 'WEB-202', 'group': self.group.id,
            'semester': '4', 'attestation_count': '9', 'admission_score': '140',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Course.objects.filter(code='WEB-202').exists())
        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('Аттестаций' in m for m in messages))
        self.assertTrue(any('Порог допуска' in m for m in messages))

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

    def test_demo_entries_hidden_and_kept_in_db(self):
        """Демо-строки не показываются ни до, ни после загрузки; в БД остаются."""
        demo = CurriculumEntry.objects.create(code='ist', subject='Демо-дисциплина', semester_from=1, semester_to=1)
        self.assertNotIn('Демо-дисциплина', {row['subject'] for row in resolve_curriculum('ist')})
        self.upload(('09.03.02-ist-2026.xlsx', plan_bytes([('Математика', [1])])))
        self.assertTrue(CurriculumEntry.objects.filter(pk=demo.pk).exists())
        self.assertEqual({row['subject'] for row in resolve_curriculum('ist')}, {'Математика'})
        self.client.force_login(self.dean)
        self.client.post(reverse('education:plan_source_delete', args=[PlanSource.objects.get().pk]), follow=True)
        # Без загруженного плана блок пуст — демо-строка не возвращается.
        self.assertEqual(resolve_curriculum('ist'), [])
        self.assertTrue(CurriculumEntry.objects.filter(pk=demo.pk).exists())

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


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='bgitu-plan-download-'))
class PlanDownloadTests(TestCase):
    """Скачивание загруженных файлов плана: вложение, исходное имя и права."""

    @classmethod
    def setUpTestData(cls):
        cls.dean_role = Role.objects.create(code='pdl_dean', name='Деканат', can_manage_schedule=True)
        cls.student_role = Role.objects.create(code='pdl_student', name='Студент', can_upload_works=True)
        cls.group = Group.objects.create(
            code='ИСТ-201', course=2, program='09.03.02 «Информационные системы и технологии»',
        )
        cls.owner = User.objects.create_user('pdl_owner', password='demo12345')
        cls.owner.profile.role = cls.dean_role
        cls.owner.profile.save()
        cls.other_dean = User.objects.create_user('pdl_other', password='demo12345')
        cls.other_dean.profile.role = cls.dean_role
        cls.other_dean.profile.save()
        cls.staff_dean = User.objects.create_user('pdl_staff', password='demo12345', is_staff=True)
        cls.staff_dean.profile.role = cls.dean_role
        cls.staff_dean.profile.save()
        cls.student = User.objects.create_user('pdl_student_user', password='demo12345')
        cls.student.profile.role = cls.student_role
        cls.student.profile.save()

    def setUp(self):
        self.payload = b'plan-bytes-for-download'
        self.record = PlanSource.objects.create(
            file=SimpleUploadedFile('plan-ist.xlsx', self.payload),
            original_name='plan-ist.xlsx',
            direction='09.03.02',
            direction_code='ist',
            size=len(self.payload),
            uploaded_by=self.owner,
        )

    def file_url(self):
        return reverse('education:plan_source_file', args=[self.record.pk])

    def test_owner_gets_attachment_with_original_name(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.file_url())
        self.assertEqual(response.status_code, 200)
        disposition = response['Content-Disposition']
        self.assertIn('attachment', disposition)
        self.assertIn('filename="plan-ist.xlsx"', disposition)
        self.assertEqual(b''.join(response.streaming_content), self.payload)

    def test_cyrillic_name_is_preserved(self):
        self.record.original_name = 'Учебный план 09.03.02 ИСТ.xlsx'
        self.record.save(update_fields=['original_name'])
        self.client.force_login(self.owner)
        response = self.client.get(self.file_url())
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "filename*=utf-8''" + quote('Учебный план 09.03.02 ИСТ.xlsx'),
            response['Content-Disposition'],
        )

    def test_staff_can_download_foreign_file(self):
        self.client.force_login(self.staff_dean)
        self.assertEqual(self.client.get(self.file_url()).status_code, 200)

    def test_other_dean_cannot_download_foreign_file(self):
        self.client.force_login(self.other_dean)
        self.assertEqual(self.client.get(self.file_url()).status_code, 403)

    def test_student_cannot_download(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(self.file_url()).status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.file_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_plan_modal_has_drag_drop_zone_and_download_link(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201'})
        html = response.content.decode()
        self.assertIn('data-import-ext=".xls,.xlsx,.docx,.pdf"', html)
        self.assertIn('data-import-max-size="20971520"', html)
        self.assertIn('data-import-error', html)
        self.assertIn(self.file_url(), html)
        self.assertIn('download="plan-ist.xlsx"', html)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='bgitu-plan-scope-'))
class CurriculumDirectionScopeTests(TestCase):
    """План группы: только дисциплины её направления из загруженных файлов."""

    @classmethod
    def setUpTestData(cls):
        cls.dean_role = Role.objects.create(code='scope_dean', name='Деканат', can_manage_schedule=True)
        cls.ist_group = Group.objects.create(
            code='ИСТ-101', course=1, program='09.03.02 «Информационные системы и технологии»',
        )
        cls.ivt_group = Group.objects.create(
            code='ИВТ-101', course=1, program='09.03.01 «Информатика и вычислительная техника»',
        )
        cls.dean = User.objects.create_user('scope_dean', password='demo12345')
        cls.dean.profile.role = cls.dean_role
        cls.dean.profile.save()

    def make_plan(self, code, name, entries):
        source = PlanSource.objects.create(
            original_name=name, direction=code, direction_code=code, entries_count=len(entries),
        )
        for subject, start, end in entries:
            CurriculumEntry.objects.create(
                code=code, subject=subject, semester_from=start, semester_to=end, source=source,
            )
        return source

    def open_group(self, group):
        self.client.force_login(self.dean)
        response = self.client.get(reverse('schedule:schedule'), {'group': group.code})
        self.assertEqual(response.status_code, 200)
        return response, response.content.decode()

    def test_group_sees_only_its_direction_plan(self):
        self.make_plan('ist', 'plan-ist.xlsx', [('Математика', 1, 2), ('Базы данных', 3, 3)])
        self.make_plan('09-03-01', 'plan-ivt.xlsx', [('Информатика', 1, 1), ('Физика', 2, 2)])

        response, html = self.open_group(self.ist_group)
        self.assertContains(response, 'Математика')
        self.assertContains(response, 'Базы данных')
        self.assertNotContains(response, 'Информатика')
        self.assertNotContains(response, 'Физика')
        self.assertEqual(html.count('class="tl-row"'), 2)

        response, html = self.open_group(self.ivt_group)
        self.assertContains(response, 'Информатика')
        self.assertContains(response, 'Физика')
        self.assertNotContains(response, 'Математика')
        self.assertNotContains(response, 'Базы данных')
        self.assertEqual(html.count('class="tl-row"'), 2)

    def test_group_without_plan_shows_empty_state_not_demo(self):
        """Нет загруженного плана — пусто: демо-план миграции не показывается."""
        self.assertTrue(CurriculumEntry.objects.filter(code='ist', source__isnull=True).exists())
        response, html = self.open_group(self.ist_group)
        self.assertContains(response, 'План для направления не загружен')
        self.assertNotContains(response, 'Математический анализ')
        self.assertNotContains(response, 'Иностранный язык')
        self.assertEqual(html.count('class="tl-row"'), 0)

    def test_foreign_and_orphan_entries_do_not_leak(self):
        """Строка чужого файла и демо-строка не видны даже при совпадении кода."""
        foreign = PlanSource.objects.create(
            original_name='plan-ivt.xlsx', direction='09.03.01', direction_code='09-03-01',
        )
        CurriculumEntry.objects.create(
            code='ist', subject='Чужая дисциплина', semester_from=1, semester_to=1, source=foreign,
        )
        no_direction = PlanSource.objects.create(original_name='plan-unknown.xlsx')
        CurriculumEntry.objects.create(
            code='ist', subject='Файл без направления', semester_from=1, semester_to=1, source=no_direction,
        )
        CurriculumEntry.objects.create(
            code='ist', subject='Осиротевшая дисциплина', semester_from=2, semester_to=2,
        )
        response, html = self.open_group(self.ist_group)
        self.assertNotContains(response, 'Чужая дисциплина')
        self.assertNotContains(response, 'Файл без направления')
        self.assertNotContains(response, 'Осиротевшая дисциплина')
        self.assertContains(response, 'План для направления не загружен')
        self.assertEqual(html.count('class="tl-row"'), 0)

    def test_freshest_source_wins_for_each_group(self):
        """Приоритет свежего файла работает для каждого направления, а не для одного."""
        for code, name in (('ist', 'Математика'), ('09-03-01', 'Информатика')):
            self.make_plan(code, f'{code}-old.xlsx', [(name, 3, 3)])
        for code, name in (('ist', 'Математика'), ('09-03-01', 'Информатика')):
            self.make_plan(code, f'{code}-new.xlsx', [(name, 3, 4)])

        response, _ = self.open_group(self.ist_group)
        self.assertContains(response, 'Математика: 3–4 семестры')
        response, _ = self.open_group(self.ivt_group)
        self.assertContains(response, 'Информатика: 3–4 семестры')

    def test_plan_code_comes_from_program_then_group_prefix(self):
        self.assertEqual(plan_code_for_group(self.ist_group), 'ist')
        self.assertEqual(plan_code_for_group(self.ivt_group), '09-03-01')
        # Шифр программы важнее названия группы: ИВТ-101 учится по 09.03.02.
        self.assertEqual(plan_code_for_group(Group(code='ИВТ-101', program='09.03.02')), 'ist')
        self.assertEqual(plan_code_for_group(Group(code='ИВТ-101')), 'ivt')
        self.assertEqual(plan_code_for_group(None), '')

    def test_unknown_or_empty_direction_has_no_plan(self):
        self.assertEqual(resolve_curriculum(''), [])
        self.assertEqual(resolve_curriculum('нет-такого'), [])


class JournalTests(TestCase):
    """Журнал дисциплины: сетка «студенты × даты», формы контроля и аттестация."""

    def setUp(self):
        self.student_role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        self.teacher_role = Role.objects.create(code='teacher', name='Преподаватель',
                                                can_create_courses=True, can_grade=True)
        self.group = Group.objects.create(code='ИСТ-201', course=2)
        self.student = self.make_user('jstudent', self.student_role, self.group)
        self.teacher = self.make_user('jteacher', self.teacher_role)
        self.course = Course.objects.create(
            title='Базы данных', code='БД-201', group=self.group, teacher=self.teacher, semester=3,
            control_form=Course.FORM_CREDIT, has_coursework=True,
        )
        Enrollment.objects.create(course=self.course, student=self.student)

    def make_user(self, username, role, group=None):
        user = User.objects.create_user(username=username, password='demo12345')
        profile = user.profile
        profile.role = role
        profile.group = group
        profile.save()
        return user

    def test_journal_shows_attendance_grid_and_attestation_column(self):
        for day, status in ((date(2026, 11, 10), Attendance.STATUS_PRESENT),
                            (date(2026, 11, 17), Attendance.STATUS_ABSENT)):
            Attendance.objects.create(course=self.course, student=self.student, date=day, status=status,
                                      marked_by=self.teacher)
        self.client.force_login(self.teacher)
        response = self.client.get(reverse('education:journal', args=[self.course.id]))
        self.assertEqual(response.status_code, 200)
        # колонки-даты
        self.assertContains(response, '10.11')
        self.assertContains(response, '17.11')
        # отметки: был/не был
        self.assertContains(response, '>б<')
        self.assertContains(response, '>н<')
        # колонка аттестации по форме контроля курса (зачёт) + признак курсовой
        self.assertContains(response, 'Итог')
        self.assertContains(response, 'курсовая')
        self.assertContains(response, 'не выставлен')
        # колонка курсовой появляется по флагу дисциплины, даже без оценок
        self.assertGreaterEqual(response.content.decode().count('курсовая'), 2)

    def test_attestation_uses_course_control_form(self):
        from apps.education.views import _attestation_kind
        self.assertEqual(_attestation_kind(self.course), Grade.KIND_CREDIT)
        self.course.control_form = Course.FORM_EXAM
        self.assertEqual(_attestation_kind(self.course), Grade.KIND_EXAM)
        self.course.control_form = Course.FORM_COURSEWORK
        self.assertEqual(_attestation_kind(self.course), Grade.KIND_COURSEWORK)
        self.course.control_form = Course.FORM_NONE
        self.assertEqual(_attestation_kind(self.course), Grade.KIND_EXAM)

    def test_attestation_mark_appears_in_journal(self):
        Grade.objects.create(course=self.course, student=self.student, kind=Grade.KIND_CREDIT,
                             value='зачтено', date=date(2026, 11, 20), created_by=self.teacher)
        self.client.force_login(self.teacher)
        response = self.client.get(reverse('education:journal', args=[self.course.id]))
        self.assertContains(response, 'зачтено')
        self.assertContains(response, '20.11.2026')

    def test_student_cannot_open_journal(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('education:journal', args=[self.course.id]))
        self.assertEqual(response.status_code, 403)

    def test_attendance_page_lists_sessions_and_marks_date(self):
        Attendance.objects.create(course=self.course, student=self.student, marked_by=self.teacher,
                                  date=date(2026, 11, 10), status=Attendance.STATUS_LATE)
        self.client.force_login(self.teacher)
        response = self.client.get(reverse('education:attendance', args=[self.course.id]))
        self.assertContains(response, 'Проведённые занятия')
        self.assertContains(response, '10.11.2026')

        self.client.post(reverse('education:attendance', args=[self.course.id]), {
            'date': '2026-11-24',
            f'status_{self.student.id}': Attendance.STATUS_EXCUSED,
        })
        self.assertTrue(Attendance.objects.filter(
            course=self.course, student=self.student, date=date(2026, 11, 24),
            status=Attendance.STATUS_EXCUSED,
        ).exists())

    def test_course_form_accepts_control_form_and_coursework(self):
        self.client.force_login(self.teacher)
        self.client.post(reverse('education:course_create'), {
            'title': 'Философия', 'code': 'ФИЛ-201', 'group': self.group.id, 'semester': '3',
            'control_form': 'diff', 'has_coursework': '1',
        })
        course = Course.objects.get(code='ФИЛ-201')
        self.assertEqual(course.control_form, Course.FORM_DIFF)
        self.assertTrue(course.has_coursework)

    def test_student_sees_own_attendance_on_course_card(self):
        Attendance.objects.create(course=self.course, student=self.student, marked_by=self.teacher,
                                  date=date(2026, 11, 10), status=Attendance.STATUS_PRESENT)
        Attendance.objects.create(course=self.course, student=self.student, marked_by=self.teacher,
                                  date=date(2026, 11, 17), status=Attendance.STATUS_ABSENT)
        self.client.force_login(self.student)
        response = self.client.get(reverse('education:course_detail', args=[self.course.id]))
        self.assertContains(response, 'был: 1')
        self.assertContains(response, 'не был: 1')

        response = self.client.get(reverse('education:courses'))
        self.assertContains(response, 'пропусков: 1')
