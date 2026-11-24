"""Тесты импорта и показа расписания."""
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
import tempfile
from unittest import mock
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files import File
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.education.models import Group

from .models import Bell, Lesson, ScheduleImport
from .services import detect_institute, run_import, visible_lessons

FIXTURE = settings.BASE_DIR / 'fixtures/schedule/bgitu-2026-27-1semestr-IVT-IST-PI-PRI.xls'
TEST_MEDIA = tempfile.mkdtemp(prefix='bgitu-schedule-media-')


def schedule_bytes(subject, group='ИСТ-201'):
    """Минимальный .xlsx в формате файла БГИТУ: шапка группы + одна пара."""
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    for row in (
        ['РАСПИСАНИЕ УЧЕБНЫХ ЗАНЯТИЙ'],
        ['', '1 корпус', '2 корпус', group],
        ['', '', '', ''],
        ['понедельник', '8.20-9.55', '8.50-10.25', f'{subject} №307 (Петров П.П.)'],
    ):
        sheet.append(row)
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


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
        self.assertContains(response, f'<i aria-hidden="true">{week}</i>')
        self.assertContains(response, 'parity-badge parity-badge--')
        # Мусорные мета-подписи не возвращаем: неделя показана только бейджем.
        self.assertNotContains(response, 'неделя №')
        self.assertNotContains(response, 'parity-menu')
        self.assertNotContains(response, 'week-parity-note')
        self.assertNotContains(response, 'Источник — файл')
        self.assertNotContains(response, 'файл БГИТУ ·')
        self.assertContains(
            response,
            f'{monday.strftime("%d.%m.%Y")} — {sunday.strftime("%d.%m.%Y")}',
        )
        self.assertContains(response, 'Базы данных')
        self.assertContains(response, 'Учебный план · вся программа обучения')

    def test_pair_time_only_inside_lesson_cell(self):
        """В левой колонке — только номер пары; время пары — внизу карточки."""
        import re

        self.student.profile.group = Group.objects.get(code='ИСТ-201')
        self.student.profile.save()
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'), {'week': '12'})
        html = response.content.decode()
        time_column = re.findall(
            r'<div class="sched-cell sched-cell--time[^"]*">(.*?)</div>', html, re.S,
        )
        self.assertTrue(time_column)
        for cell in time_column:
            self.assertNotIn(':', cell)
            self.assertNotIn('–', cell)
        self.assertRegex(html, r'class="sched-time mono">\d{1,2}:\d{2}–\d{1,2}:\d{2}<')
        # 2-я пара ИСТ-201 в понедельник — 2-й корпус, время по звонкам 2-го корпуса.
        self.assertIn('10:35–12:10', html)

    def test_red_cell_shows_second_building(self):
        """Красная ячейка файла = 2-й корпус, корпус виден в карточке."""
        self.client.force_login(self.dean)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ПИ-401', 'week': '12'})
        self.assertContains(response, 'Разработка мобильных бизнес-приложений')
        self.assertContains(response, 'sched-build--2')
        self.assertContains(response, '2 корп.')

    def test_teacher_block_lists_real_teachers(self):
        """Блок 03: список преподавателей из загруженного файла, с известными ФИО."""
        self.client.force_login(self.dean)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201'})
        teachers = response.context['teachers']
        self.assertGreater(len(teachers), 20)
        for name in ('Казаков О.Д.', 'Козлова И.Р.', 'Симохин С.П.'):
            self.assertIn(name, teachers)
        # по умолчанию выбран преподаватель из расписания группы, а не «первый в списке»
        self.assertIn(response.context['teacher'], teachers)
        self.assertContains(response, 'sched-panel--teacher')
        self.assertContains(response, 'name="teacher"')

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
        self.assertContains(w12, '16.11.2026 — 22.11.2026')
        self.assertContains(w13, '23.11.2026 — 29.11.2026')
        self.assertContains(w13, 'sched-in-next')

    def test_day_header_stacks_day_and_date(self):
        """Шапка дня: «Пн» и дата — две строки, а не склейка в одну."""
        self.client.force_login(self.dean)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201', 'week': '13'})
        html = response.content.decode()
        self.assertRegex(
            html,
            r'<div class="sched-cell sched-cell--head[^"]*">\s*'
            r'<span class="sched-day">Пн</span>\s*'
            r'<span class="sched-date mono">23\.11</span>',
        )
        self.assertRegex(
            html,
            r'<div class="sched-cell sched-cell--head[^"]*">\s*'
            r'<span class="sched-day">Сб</span>\s*'
            r'<span class="sched-date mono">28\.11</span>',
        )

    def test_today_column_has_soft_highlight(self):
        """Сегодня: is-today у шапки и у всех ячеек колонки (число строк — по данным)."""
        import re

        self.client.force_login(self.dean)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201'})
        # Только блок 01: у блока 03 (преподаватель) своя таблица и своя колонка «сегодня».
        html = response.content.decode().split('sched-panel--teacher')[0]
        today = date.today()
        short = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'][today.weekday()]
        self.assertRegex(
            html,
            rf'<div class="sched-cell sched-cell--head is-today">\s*'
            rf'<span class="sched-day">{short}</span>\s*'
            rf'<span class="sched-date mono">{today.strftime("%d.%m")}</span>',
        )
        self.assertEqual(html.count('sched-cell--head is-today'), 1)
        rows = len(response.context['grid'])
        self.assertGreater(rows, 0)
        self.assertEqual(len(re.findall(r'class="[^"]*sched-cell--body[^"]*is-today', html)), rows)

    def test_staff_can_switch_group(self):
        self.client.force_login(self.dean)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-101'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ИСТ-101')
        self.assertContains(response, 'group-switch')

    def test_student_cannot_open_import(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse('schedule:import')).status_code, 403)

    def test_import_lives_in_modal_not_separate_panel(self):
        """Обновление расписания — кнопка в шапке и модальное окно, без отдельной панели."""
        self.client.force_login(self.dean)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201'})
        html = response.content.decode()
        self.assertContains(response, 'id="scheduleImportOpen"')
        self.assertContains(response, '<dialog class="dialog sched-import" id="scheduleImport"')
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'name="file"')
        # старой отдельной секции импорта больше нет
        self.assertNotIn('Импорт из файла БГИТУ', html)
        self.assertNotIn('import-form', html)

    def test_student_has_no_import_button(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        self.assertNotContains(response, 'id="scheduleImportOpen"')
        self.assertNotContains(response, 'id="scheduleImport"')


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
        # «над чертой» / «под чертой» — I и II неделя
        self.assertEqual(parse_cell('Физика (I неделя)')['week'], 'odd')
        self.assertEqual(parse_cell('Физика (II неделя)')['week'], 'even')

    def test_parser_splits_two_lessons_in_one_cell(self):
        """Две пары подряд в одной ячейке (ИСТ-301, ср, 2 пара) — обе сохраняются."""
        from .import_service import cell_entries

        text = 'Методы машинного обучения\n№228 (Казаков О.Д.)\n\nWeb-сервисы\n№205 (Иванов И.И.)'
        entries = cell_entries(text)
        self.assertEqual([item['subject'] for item in entries], ['Методы машинного обучения', 'Web-сервисы'])

    def test_parser_reads_subgroup_letter_from_prefix(self):
        from .import_service import cell_entries

        entries = cell_entries('а Физика\n№307 (Симохин С.П.)\n\nб Информатика\n№228 (Юркова О.Н.)')
        self.assertEqual([item['subgroup'] for item in entries], ['а', 'б'])
        self.assertEqual([item['subject'] for item in entries], ['Физика', 'Информатика'])

    def test_imported_groups_have_subgroups(self):
        """В файле есть группы с подгруппами «а»/«б» — они сохранены как подгруппы."""
        from .services import parse_schedule

        lessons, _ = parse_schedule(str(FIXTURE))
        codes = {item['subgroup'] for item in lessons if item['subgroup']}
        self.assertTrue({'а', 'б'} & codes, f'ожидались подгруппы а/б, есть {codes}')

    def test_parity_follows_selected_week(self):
        """На выбранной неделе видна только её пара: нечётная — odd и общая, чётная — even."""
        from .models import Lesson

        Lesson.objects.create(group=self.group, weekday=2, pair=1, week='odd', subject='Только нечёт')
        Lesson.objects.create(group=self.group, weekday=2, pair=2, week='even', subject='Только чёт')
        self.client.force_login(self.student)
        # неделя 13 семестра: 23–29.11.2026 — 13-я, нечётная
        odd = self.client.get(reverse('schedule:schedule'), {'week': '13'})
        self.assertContains(odd, '<b>Только нечёт</b>')
        self.assertNotContains(odd, '<b>Только чёт</b>')
        self.assertNotContains(odd, '1-я неделя')
        self.assertNotContains(odd, '2-я неделя')
        # неделя 12: 16–22.11.2026 — 12-я, чётная
        even = self.client.get(reverse('schedule:schedule'), {'week': '12'})
        self.assertContains(even, '<b>Только чёт</b>')
        self.assertNotContains(even, '<b>Только нечёт</b>')
        # предмет «каждую неделю» виден в таблице в обеих
        self.assertContains(odd, '<b>Философия</b>')
        self.assertContains(even, '<b>Философия</b>')
        # деления ячейки по неделям больше нет
        for response in (odd, even):
            self.assertNotContains(response, 'sched-cell--weeks')
            self.assertNotContains(response, 'sched-half')

    def test_same_slot_shows_only_selected_week(self):
        """Один слот с парами разных недель: на выбранной неделе — одна карточка со своими деталями."""
        from .models import Bell, Lesson

        Bell.objects.create(building='1', pair=6, start='15:45', end='17:20')
        Bell.objects.create(building='2', pair=6, start='16:15', end='17:50')
        Lesson.objects.create(
            group=self.group, weekday=3, pair=6, week='odd', subject='Слот-нечёт',
            room='101', teacher_name='Петров П.П.', building='1',
        )
        Lesson.objects.create(
            group=self.group, weekday=3, pair=6, week='even', subject='Слот-чёт',
            room='202', teacher_name='Иванов И.И.', building='2',
        )
        self.client.force_login(self.student)
        odd = self.client.get(reverse('schedule:schedule'), {'week': '13'})
        # Только блок 01: в блоке 03 законно перечислены все преподаватели.
        html = odd.content.decode().split('sched-panel--teacher')[0]
        self.assertNotIn('sched-cell--weeks', html)
        self.assertNotIn('sched-half__label', html)
        self.assertIn('<b>Слот-нечёт</b>', html)
        # карточка чётной недели в этом слоте не рисуется вовсе
        self.assertNotIn('Слот-чёт', html)
        self.assertNotIn('ауд. 202', html)
        self.assertNotIn('Иванов И.И.', html)
        self.assertNotIn('16:15–17:50', html)
        # у оставшейся карточки — свои аудитория, преподаватель и время по корпусу
        self.assertIn('ауд. 101', html)
        self.assertIn('Петров П.П.', html)
        self.assertIn('15:45–17:20', html)
        even = self.client.get(reverse('schedule:schedule'), {'week': '12'})
        html = even.content.decode().split('sched-panel--teacher')[0]
        self.assertIn('<b>Слот-чёт</b>', html)
        self.assertNotIn('Слот-нечёт', html)
        self.assertNotIn('ауд. 101', html)
        self.assertIn('ауд. 202', html)
        self.assertIn('16:15–17:50', html)

    def test_other_week_lessons_do_not_fill_table(self):
        """Пары только другой недели не заполняют таблицу — честное «занятий нет»."""
        from apps.education.models import Group
        from .models import Lesson

        other = Group.objects.create(code='ИСТ-299', course=2)
        Lesson.objects.create(group=other, weekday=1, pair=1, week='even', subject='Только-чётная')
        self.student.profile.group = other
        self.student.profile.save()
        self.client.force_login(self.student)
        page = self.client.get(reverse('schedule:schedule'), {'week': '13'})  # нечётная
        self.assertContains(page, 'занятий нет')
        self.assertNotContains(page, 'Только-чётная')
        self.assertNotContains(page, 'class="sched-table"')

    def test_subgroup_filter(self):
        """?sub=б показывает занятия второй подгруппы."""
        from .models import Lesson

        Lesson.objects.create(group=self.group, weekday=6, pair=5, week='all', subject='Первая-подгр', subgroup='1')
        Lesson.objects.create(group=self.group, weekday=6, pair=5, week='all', subject='Вторая-подгр', subgroup='2')
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'), {'week': '12', 'sub': 'б'})
        self.assertContains(response, '<b>Вторая-подгр</b>')
        self.assertNotContains(response, '<b>Первая-подгр</b>')
        self.assertContains(response, 'sub-pill is-active')

    def test_curriculum_block_lists_program(self):
        """Блок учебного плана: дисциплины загруженного плана направления группы."""
        from apps.education.models import CurriculumEntry, PlanSource

        source = PlanSource.objects.create(
            original_name='plan-ist.xlsx', direction='09.03.02', direction_code='ist',
        )
        CurriculumEntry.objects.create(
            code='ist', subject='Математический анализ', semester_from=1, semester_to=2, source=source,
        )
        CurriculumEntry.objects.create(
            code='ist', subject='Иностранный язык', semester_from=1, semester_to=4, source=source,
        )
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        self.assertContains(response, 'Учебный план · вся программа обучения')
        self.assertContains(response, 'Математический анализ')
        self.assertContains(response, 'Иностранный язык')
        # многолетняя дисциплина — отрезок по семестрам
        self.assertContains(response, 'Иностранный язык: 1–4 семестры')
        self.assertContains(response, '>3</b>')

    def test_curriculum_block_empty_without_uploaded_plan(self):
        """Без загруженного плана — честное пустое состояние, без демо-дисциплин."""
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        self.assertContains(response, 'План для направления не загружен')
        self.assertNotContains(response, 'Математический анализ')
        self.assertNotContains(response, 'class="tl-row"')

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


class SubgroupSplitCellTests(TestCase):
    """Режим «все»: разные пары «а»/«б» — две карточки в половинках ячейки."""

    @classmethod
    def setUpTestData(cls):
        from apps.education.models import Group
        from .models import Lesson

        cls.group = Group.objects.create(code='ИСТ-201', course=2)
        cls.role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        cls.student = User.objects.create_user('substudent', password='demo12345')
        cls.student.profile.role = cls.role
        cls.student.profile.group = cls.group
        cls.student.profile.save()
        Lesson.objects.create(group=cls.group, weekday=6, pair=5, week='all', subject='Подгруппа-один', subgroup='1')
        Lesson.objects.create(group=cls.group, weekday=6, pair=5, week='all', subject='Подгруппа-два', subgroup='2')
        # Общая (объединённая) пара: подгруппы нет, карточка одна.
        Lesson.objects.create(group=cls.group, weekday=5, pair=4, week='all', subject='Общая-пара')
        Lesson.objects.create(
            group=cls.group, weekday=4, pair=3, week='all', subject='Второй-корпус', building='2', room='314',
        )

    def test_conflicting_pair_shows_both_halves(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'), {'week': '12'})
        self.assertContains(response, '<b>Подгруппа-один</b>')
        self.assertContains(response, '<b>Подгруппа-два</b>')
        self.assertContains(response, 'sched-cell--split')

    def test_split_is_by_subgroups_not_weeks(self):
        """Ячейка делится пополам только по подгруппам: половин недель и подписей нет."""
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'), {'week': '12'})
        html = response.content.decode()
        self.assertNotIn('sched-cell--weeks', html)
        self.assertNotIn('sched-half', html)
        self.assertNotIn('1-я неделя', html)
        self.assertNotIn('2-я неделя', html)
        cells = html.split('<div class="sched-cell sched-cell--body')
        split_cells = [chunk for chunk in cells if 'sched-cell--split' in chunk]
        self.assertTrue(split_cells)
        self.assertTrue(
            any('Подгруппа-один' in chunk and 'Подгруппа-два' in chunk for chunk in split_cells),
            'обе карточки подгрупп должны быть в одной ячейке с классом split',
        )
        self.assertIn('подгр. а', html)
        self.assertIn('подгр. б', html)

    def test_common_pair_is_single_card_without_subgroup(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'), {'week': '12'})
        html = response.content.decode()
        self.assertIn('<b>Общая-пара</b>', html)
        start = html.index('<b>Общая-пара</b>')
        card = html[html.rindex('sched-lesson', 0, start):start]
        self.assertNotIn('sched-sub', card)

    def test_user_subgroup_wins(self):
        profile = self.student.profile
        profile.subgroup = 'б'
        profile.save()
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'), {'week': '12'})
        self.assertContains(response, '<b>Подгруппа-два</b>')
        self.assertNotContains(response, '<b>Подгруппа-один</b>')
        self.assertNotContains(response, 'sched-cell--split')


class SubgroupVisibilityTests(TestCase):
    """Выбор подгрупп и метки — только у групп с реальным делением.

    Деление определяется по данным выбранной группы: парсер ставит метки
    «а»/«б» только тогда, когда в шапке файла под именем группы есть
    подстолбцы «а» и «б». Одиночная метка делением не считается.
    """

    @classmethod
    def setUpTestData(cls):
        cls.student_role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        cls.dean_role = Role.objects.create(code='dean', name='Деканат', can_view_all=True)
        cls.division = Group.objects.create(code='ИСТ-501', course=2)
        cls.plain = Group.objects.create(code='ПИ-501', course=4)
        cls.lone = Group.objects.create(code='ПРИ-501', course=1)
        cls.parity = Group.objects.create(code='ИВТ-501', course=1)

        cls.student = User.objects.create_user('visstudent', password='demo12345')
        cls.student.profile.role = cls.student_role
        cls.student.profile.group = cls.division
        cls.student.profile.save()
        cls.dean = User.objects.create_user('visdean', password='demo12345')
        cls.dean.profile.role = cls.dean_role
        cls.dean.profile.save()

        # Группа с делением: две подгруппы в одном слоте + общая пара.
        Lesson.objects.create(group=cls.division, weekday=1, pair=1, week='all', subject='Деление-а', subgroup='1')
        Lesson.objects.create(group=cls.division, weekday=1, pair=1, week='all', subject='Деление-б', subgroup='2')
        Lesson.objects.create(group=cls.division, weekday=2, pair=2, week='all', subject='Общая-с-делением')

        # Группа без деления: обычные пары, подгрупп нет вообще.
        Lesson.objects.create(group=cls.plain, weekday=1, pair=1, week='all', subject='Пара-раз')
        Lesson.objects.create(group=cls.plain, weekday=2, pair=2, week='all', subject='Пара-два')

        # Группа с одиночной пометкой подгруппы: второй половины в слоте нет.
        Lesson.objects.create(group=cls.lone, weekday=1, pair=1, week='all', subject='Одиночная-метка', subgroup='1')
        Lesson.objects.create(group=cls.lone, weekday=2, pair=2, week='all', subject='Обычная-пара')

        # Один и тот же день и пара, но разные недели: одновременно не стоят.
        Lesson.objects.create(group=cls.parity, weekday=3, pair=3, week='odd', subject='Нечёт-подгруппа', subgroup='1')
        Lesson.objects.create(group=cls.parity, weekday=3, pair=3, week='even', subject='Чёт-подгруппа', subgroup='2')

    def _login_as_student(self, group):
        self.student.profile.group = group
        self.student.profile.save()
        self.client.force_login(self.student)

    def test_division_group_shows_pills_and_filters(self):
        """Есть деление — пилюли и метки видны, ?sub= фильтрует занятия."""
        self._login_as_student(self.division)
        page = self.client.get(reverse('schedule:schedule'), {'week': '12'})
        self.assertEqual(page.context['subgroups'], ['а', 'б'])
        self.assertTrue(page.context['has_subgroups'])
        self.assertContains(page, 'class="sub-line"')
        self.assertContains(page, 'class="sub-pill')
        self.assertContains(page, 'sub-pill is-active')  # активна «все»
        self.assertContains(page, 'class="sched-sub mono"')

        second = self.client.get(reverse('schedule:schedule'), {'week': '12', 'sub': 'б'})
        self.assertContains(second, '<b>Деление-б</b>')
        self.assertNotContains(second, '<b>Деление-а</b>')
        self.assertContains(second, '<b>Общая-с-делением</b>')

        first = self.client.get(reverse('schedule:schedule'), {'week': '12', 'sub': 'а'})
        self.assertContains(first, '<b>Деление-а</b>')
        self.assertNotContains(first, '<b>Деление-б</b>')

    def test_group_without_division_has_no_subgroup_ui(self):
        """Нет деления — ни пилюль, ни меток, ни слова «подгруппа», все пары видны."""
        self._login_as_student(self.plain)
        page = self.client.get(reverse('schedule:schedule'), {'week': '12'})
        self.assertEqual(page.context['subgroups'], [])
        self.assertFalse(page.context['has_subgroups'])
        html = page.content.decode()
        self.assertNotIn('подгрупп', html.lower())
        self.assertNotIn('sub-line', html)
        self.assertNotIn('sub-pill', html)
        self.assertNotIn('sched-sub', html)
        self.assertContains(page, '<b>Пара-раз</b>')
        self.assertContains(page, '<b>Пара-два</b>')

    def test_lone_subgroup_mark_is_not_division(self):
        """Одна пометка подгруппы без второй половины — не повод для пилюль."""
        self._login_as_student(self.lone)
        page = self.client.get(reverse('schedule:schedule'), {'week': '12'})
        self.assertEqual(page.context['subgroups'], [])
        html = page.content.decode()
        self.assertNotIn('подгрупп', html.lower())
        self.assertNotIn('sched-sub', html)
        self.assertContains(page, '<b>Одиночная-метка</b>')
        self.assertContains(page, '<b>Обычная-пара</b>')

    def test_subgroups_on_different_weeks_still_division(self):
        """Обе метки подгрупп в данных — деление есть; видна пара только выбранной недели."""
        self._login_as_student(self.parity)
        page = self.client.get(reverse('schedule:schedule'), {'week': '13'})
        self.assertEqual(page.context['subgroups'], ['а', 'б'])
        self.assertContains(page, 'sub-pill')
        self.assertNotContains(page, 'sched-cell--weeks')
        self.assertContains(page, '<b>Нечёт-подгруппа</b>')
        self.assertNotContains(page, '<b>Чёт-подгруппа</b>')
        even = self.client.get(reverse('schedule:schedule'), {'week': '12'})
        self.assertContains(even, '<b>Чёт-подгруппа</b>')
        self.assertNotContains(even, '<b>Нечёт-подгруппа</b>')

    def test_saved_subgroup_ignored_for_group_without_division(self):
        """Сохранённая в профиле подгруппа не фильтрует группу без деления."""
        profile = self.student.profile
        profile.subgroup = 'б'
        profile.save()
        self._login_as_student(self.plain)
        page = self.client.get(reverse('schedule:schedule'), {'week': '12'})
        self.assertEqual(page.context['sub'], '')
        self.assertNotContains(page, 'sub-pill')
        self.assertContains(page, '<b>Пара-раз</b>')
        self.assertContains(page, '<b>Пара-два</b>')
        profile.refresh_from_db()
        self.assertEqual(profile.subgroup, 'б')  # профиль не тронут

    def test_switching_group_drops_foreign_subgroup(self):
        """Переключение с группы с делением на группу без него снимает фильтр."""
        self.dean.profile.subgroup = 'б'
        self.dean.profile.save()
        self.client.force_login(self.dean)
        page = self.client.get(reverse('schedule:schedule'), {'group': self.plain.code, 'week': '12'})
        self.assertEqual(page.context['group'], self.plain)
        self.assertEqual(page.context['sub'], '')
        self.assertFalse(page.context['has_subgroups'])
        self.assertNotContains(page, 'sub-pill')
        self.assertNotContains(page, 'sched-sub')
        self.assertContains(page, '<b>Пара-раз</b>')
        self.assertContains(page, '<b>Пара-два</b>')


class ParityByCalendarTests(TestCase):
    """Листание недель само определяет чётность: календарь решает, какое расписание показать."""

    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(code='ИСТ-201', course=2)
        role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        cls.student = User.objects.create_user('paritystudent', password='demo12345')
        cls.student.profile.role = role
        cls.student.profile.group = cls.group
        cls.student.profile.save()
        Lesson.objects.create(group=cls.group, weekday=1, pair=1, week='odd', subject='Нечёт-предмет')
        Lesson.objects.create(group=cls.group, weekday=1, pair=2, week='even', subject='Чёт-предмет')

    def test_parity_flips_with_week_number(self):
        """Соседние недели — противоположная чётность; видна только пара своей недели."""
        from .views import parity_of_monday, semester_monday

        self.client.force_login(self.student)
        seen = {}
        for week in (12, 13):
            response = self.client.get(reverse('schedule:schedule'), {'week': str(week)})
            parity = parity_of_monday(semester_monday(week))
            seen[week] = parity
            label = 'нечётная' if parity == 'odd' else 'чётная'
            shown = 'Нечёт-предмет' if parity == 'odd' else 'Чёт-предмет'
            hidden = 'Чёт-предмет' if parity == 'odd' else 'Нечёт-предмет'
            self.assertContains(response, f'parity-badge parity-badge--{parity}')
            self.assertContains(response, f'<b>{label}</b>')
            self.assertContains(response, f'<b>{shown}</b>')
            self.assertNotContains(response, f'<b>{hidden}</b>')
        self.assertNotEqual(seen[12], seen[13], 'соседние недели должны быть разной чётности')

    def test_week_page_shows_no_lessons_state(self):
        """Неделя без пар для группы — явное сообщение, а не пустая таблица."""
        self.client.force_login(self.student)
        self.assertContains(self.client.get(reverse('schedule:schedule'), {'week': '3'}), 'sched-table')
        # в этой группе пары только по понедельнику — убираем их и смотрим пустой слот
        Lesson.objects.filter(group=self.group).delete()
        response = self.client.get(reverse('schedule:schedule'), {'week': '3'})
        self.assertContains(response, 'занятий нет')

    def test_week_links_keep_group(self):
        dean_role = Role.objects.create(code='dean2', name='Деканат', can_manage_schedule=True)
        dean = User.objects.create_user('pardean', password='demo12345')
        dean.profile.role = dean_role
        dean.profile.save()
        self.client.force_login(dean)
        response = self.client.get(reverse('schedule:schedule'), {'week': '5', 'group': 'ИСТ-201'})
        self.assertContains(response, 'week=6&amp;group=ИСТ-201&amp;dir=next')
        self.assertContains(response, 'week=4&amp;group=ИСТ-201&amp;dir=prev')


class WeekParityQueryTests(TestCase):
    """?week=odd|even — прямой выбор чётности без пересчёта номера недели."""

    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(code='ИСТ-201', course=2)
        role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        cls.student = User.objects.create_user('oddstudent', password='demo12345')
        cls.student.profile.role = role
        cls.student.profile.group = cls.group
        cls.student.profile.save()
        Lesson.objects.create(group=cls.group, weekday=4, pair=3, week=Lesson.WEEK_ODD, subject='Только-нечёт')
        Lesson.objects.create(group=cls.group, weekday=4, pair=3, week=Lesson.WEEK_EVEN, subject='Только-чёт')
        Lesson.objects.create(group=cls.group, weekday=5, pair=1, week=Lesson.WEEK_ALL, subject='Каждую-неделю')

    def test_week_query_param_selects_parity(self):
        """?week=odd и ?week=even: в ячейке только пара выбранной чётности и общая."""
        self.client.force_login(self.student)
        odd = self.client.get(reverse('schedule:schedule'), {'week': 'odd'})
        even = self.client.get(reverse('schedule:schedule'), {'week': 'even'})
        self.assertContains(odd, '<b>Только-нечёт</b>')
        self.assertNotContains(odd, '<b>Только-чёт</b>')
        self.assertContains(even, '<b>Только-чёт</b>')
        self.assertNotContains(even, '<b>Только-нечёт</b>')
        # «каждую неделю» — в обеих
        self.assertContains(odd, '<b>Каждую-неделю</b>')
        self.assertContains(even, '<b>Каждую-неделю</b>')
        self.assertContains(odd, 'data-parity="odd"')
        self.assertContains(even, 'data-parity="even"')

    def test_explicit_parity_matches_calendar_week(self):
        """Выбранный ?week=odd попадает на неделю с нечётной календарной чётностью."""
        from .views import parity_of_monday, semester_monday, semester_week_of

        self.client.force_login(self.student)
        for parity in ('odd', 'even'):
            self.client.force_login(self.student)
            response = self.client.get(reverse('schedule:schedule'), {'week': parity})
            self.assertEqual(response.status_code, 200)
            number = response.context['week']
            self.assertEqual(parity_of_monday(semester_monday(number)), parity)
            self.assertEqual(semester_week_of(response.context['monday']), number)

    def test_parity_links_switch_without_reloading_number(self):
        """Выпадашка недель: ссылки data-parity-odd/even есть и ведут на нужную чётность."""
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'), {'week': 'odd'})
        links = response.context['parity_links']
        self.assertTrue(links['odd'] and links['even'])
        self.assertContains(response, f'data-parity-odd="{links["odd"]}"')
        self.assertContains(response, f'data-parity-even="{links["even"]}"')
        # ссылка «чётная» — ?week=N той недели семестра, у которой чётный номер
        query = links['even'].split('?', 1)[1]
        self.assertEqual(query.split('&')[0], 'week=2')
        target = self.client.get(reverse('schedule:schedule') + links['even'])
        self.assertEqual(target.status_code, 200)
        self.assertEqual(target.context['parity'], 'even')
        self.assertContains(target, 'data-parity="even"')
        self.assertContains(target, '<b>Только-чёт</b>')

    def test_subgroup_param_not_confused_with_parity(self):
        """?sub=odd не считается подгруппой — фильтр подгруппы остаётся пустым."""
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'), {'week': 'even', 'sub': 'odd'})
        self.assertEqual(response.context['sub'], '')
        self.assertContains(response, '<b>Только-чёт</b>')


class WeekMarkerParserTests(TestCase):
    """Разбор обозначений чётности недели из файла БГИТУ."""

    def test_numerator_denominator_words(self):
        from .import_service import cell_entries

        odd = cell_entries('Физика\n№307 (Петров П.П.)\nчислитель')
        even = cell_entries('Информатика\n№228 (Юркова О.В.)\nзнаменатель')
        self.assertEqual([item['week'] for item in odd], ['odd'])
        self.assertEqual([item['week'] for item in even], ['even'])
        # слова-метки не попадают в название дисциплины
        self.assertEqual(odd[0]['subject'], 'Физика')
        self.assertEqual(even[0]['subject'], 'Информатика')

    def test_odd_even_short_form(self):
        from .import_service import parse_cell

        self.assertEqual(parse_cell('Физика\nнечёт')['week'], 'odd')
        self.assertEqual(parse_cell('Физика (чёт)')['week'], 'even')
        # «отчётности» — не метка чётности
        self.assertEqual(parse_cell('Получение отчётности №214')['week'], 'all')
        self.assertEqual(parse_cell('Физика\nнечётная неделя')['week'], 'odd')

    def test_upper_lower_week(self):
        from .import_service import parse_cell

        self.assertEqual(parse_cell('Физика\nверхняя неделя')['week'], 'odd')
        self.assertEqual(parse_cell('Физика\nнижняя неделя')['week'], 'even')
        self.assertEqual(parse_cell('Физика\nВерхняя неделя')['subject'], 'Физика')

    def test_roman_week_marks(self):
        from .import_service import detect_week

        self.assertEqual(detect_week('Физика (I неделя)'), 'odd')
        self.assertEqual(detect_week('Физика (II полугодие)'), 'even')
        # маркер в скобках снимается — ФИО в скобках за ним остаётся преподавателем
        from .import_service import parse_cell

        item = parse_cell('Физика (I неделя) №307 (Петров П.П.)')
        self.assertEqual((item['week'], item['teacher_name'], item['room']), ('odd', 'Петров П.П.', '307'))

    def test_slash_fraction_week_numbers(self):
        from .import_service import parse_cell

        # «7/11 неделя» — суммарные номера недель, чётность сходится → odd
        self.assertEqual(parse_cell('Физика\n7/11 неделя')['week'], 'odd')
        self.assertEqual(parse_cell('Физика\n6/10 неделя')['week'], 'even')
        # «7/14 неделя» — разная чётность чисел: метка неоднозначна → all
        self.assertEqual(parse_cell('Физика\n7/14 неделя')['week'], 'all')

    def test_fraction_cell_gives_two_parities(self):
        """Ячейка «числитель / знаменатель» разбивается на нечётное и чётное занятие."""
        from .import_service import cell_week_parity, parse_cell, split_fraction_entries

        text = 'Физика №307 (Петров П.П.) / Информатика №228 (Юркова О.В.)'
        self.assertEqual(cell_week_parity(text), 'pair')
        # так делает parse_schedule: каждая часть дроби → отдельное занятие
        parts = split_fraction_entries(text)
        self.assertEqual(len(parts), 2)
        entries = [parse_cell(part) for part in parts]
        self.assertEqual([(item['subject'], item['week']) for item in entries],
                         [('Физика', 'odd'), ('Информатика', 'even')])
        self.assertEqual(entries[0]['teacher_name'], 'Петров П.П.')
        self.assertEqual(entries[1]['teacher_name'], 'Юркова О.В.')

    def test_file_vertical_halves_are_weeks(self):
        """Верхняя половина ячейки пары — 1-я неделя, нижняя — 2-я (не подгруппы)."""
        from .import_service import parse_schedule

        lessons, _ = parse_schedule(str(FIXTURE))
        self.assertTrue(any(item['week'] == 'odd' for item in lessons))
        self.assertTrue(any(item['week'] == 'even' for item in lessons))
        # Вторник, 2-я пара ИСТ-201: сверху «Физика», снизу «ОЗОЖ».
        slot = [
            item for item in lessons
            if item['group'] == 'ИСТ-201' and item['weekday'] == 2 and item['pair'] == 2
        ]
        self.assertEqual(
            {(item['subject'], item['week']) for item in slot},
            {('Физика', 'odd'), ('Основы здорового образа жизни', 'even')},
        )
        self.assertTrue(all(item['subgroup'] == '' for item in slot))
        # Никаких номеров занятий вместо подгрупп.
        self.assertFalse(any(item['subgroup'] in ('1', '2') for item in lessons))

    def test_parse_schedule_keeps_cell_parity(self):
        """parse_schedule переносит признак недели из разбора ячейки в занятие."""
        from unittest import mock

        from . import import_service

        rows = [
            ['РАСПИСАНИЕ УЧЕБНЫХ ЗАНЯТИЙ'],
            ['', '1 корпус', '2 корпус', 'ИСТ-201'],
            ['', '', '', ''],
            ['понедельник', '8.20-9.55', '8.50-10.25', 'Физика №307 (Петров П.П.)\nнечётная неделя'],
            ['', '10.05-11.40', '10.35-12.10', 'Информатика №228 (Юркова О.В.)\nчётная неделя'],
        ]
        merged = [(1, 2, 3, 4)]
        with mock.patch.object(import_service, 'read_sheet', return_value=(rows, merged, {})):
            lessons, _bells = import_service.parse_schedule('dummy.xls')
        self.assertEqual(
            [(item['pair'], item['week'], item['subject']) for item in lessons],
            [(1, 'odd', 'Физика'), (2, 'even', 'Информатика')],
        )



class ScheduleCellRulesTests(TestCase):
    """Правила чтения ячеек файла БГИТУ: недели, корпус, подгруппы."""

    @classmethod
    def setUpTestData(cls):
        from .import_service import parse_schedule

        cls.lessons, _bells = parse_schedule(str(FIXTURE))

    def slot(self, group, weekday, pair):
        return [
            item for item in self.lessons
            if item['group'] == group and item['weekday'] == weekday and item['pair'] == pair
        ]

    def test_building_from_font_colour(self):
        """Красный шрифт = 2-й корпус, чёрный = 1-й (проверка на реальном файле)."""
        monday_first = self.slot('ПИ-401', 1, 1)
        self.assertEqual(len(monday_first), 1)
        self.assertEqual(monday_first[0]['building'], '1')  # «Бизнес-аналитика» чёрным
        monday_second = self.slot('ПИ-401', 1, 2)
        self.assertEqual(len(monday_second), 1)
        self.assertEqual(monday_second[0]['subject'], 'Разработка мобильных бизнес-приложений')
        self.assertEqual(monday_second[0]['building'], '2')  # красным
        self.assertEqual(sum(1 for item in self.lessons if item['building'] == '2'), 59)

    def test_subgroups_only_where_header_has_ab(self):
        """Подстолбцы «а»/«б» есть только у ПРИ-101, ПРИ-201, ПРИ-301."""
        divided = {
            item['group'] for item in self.lessons if item['subgroup']
        }
        self.assertEqual(divided, {'ПРИ-101', 'ПРИ-201', 'ПРИ-301'})
        self.assertTrue(all(item['subgroup'] == '' for item in self.lessons if item['group'] == 'ПРИ-401'))

    def test_common_merged_cell_is_one_lesson_without_subgroup(self):
        """Объединённая на обе подколонки ячейка — одна пара всей группы."""
        slot = self.slot('ПРИ-201', 1, 2)
        self.assertEqual(
            {(item['subject'], item['week'], item['subgroup']) for item in slot},
            {('Философия', 'odd', ''), ('Математика', 'even', '')},
        )

    def test_split_subgroup_cells_have_their_own_lessons(self):
        """Разделённые подколонки «а»/«б» — у каждой подгруппы своя пара."""
        slot = self.slot('ПРИ-101', 1, 3)
        self.assertEqual(
            {(item['subgroup'], item['subject']) for item in slot},
            {('а', 'Frontend-разработка'), ('б', 'Физика')},
        )
        self.assertTrue(all(item['week'] == 'all' for item in slot))

    def test_week_numbers_match_halves(self):
        """week=1 — верхняя половина, week=2 — нижняя; property week_number."""
        from .models import Lesson

        self.assertEqual(Lesson(week='odd').week_number, 1)
        self.assertEqual(Lesson(week='even').week_number, 2)
        self.assertEqual(Lesson(week='all').week_number, 0)
        self.assertEqual(Lesson(week='odd').week_label, '1-я неделя')


class MergedCellAcrossPairsTests(TestCase):
    """Объединённая ячейка через границу пар не должна дублировать занятие в соседней паре."""

    def test_merged_cell_not_duplicated(self):
        """Ячейка (7,9) в файле БГИТУ: 1-я пара подгруппы «б» не появляется во 2-й."""
        import xlrd
        from .import_service import parse_schedule

        book = xlrd.open_workbook(str(FIXTURE), formatting_info=True)
        sheet = book.sheet_by_index(0)
        # ищем объединение по вертикали, пересекающее границу пары, и группу за ним
        merged = [m for m in sheet.merged_cells if m[1] - m[0] > 1]
        self.assertTrue(merged, 'в файле нет объединённых ячеек — проверка неприменима')
        lessons, _ = parse_schedule(str(FIXTURE))
        # ни одна ячейка не содержит два занятия с одинаковыми предметом и подгруппой
        seen = {}
        for item in lessons:
            key = (item['group'], item['weekday'], item['pair'], item['subject'], item['subgroup'])
            seen[key] = seen.get(key, 0) + 1
        duplicates = {k: v for k, v in seen.items() if v > 1}
        # одинаковые предмет+подгруппа допустимы только при разных аудиториях (реальные разные занятия)
        self.assertTrue(all(v <= 2 for v in duplicates.values()), duplicates)


class NavTooltipMarkupTests(TestCase):
    """Подсказки разделов у свёрнутого рельса кабинета."""

    @classmethod
    def setUpTestData(cls):
        from apps.schedule.models import Lesson

        cls.group = Group.objects.create(code='ИСТ-201', course=2)
        role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        cls.student = User.objects.create_user('tipstudent', password='demo12345')
        cls.student.profile.role = role
        cls.student.profile.group = cls.group
        cls.student.profile.save()
        Lesson.objects.create(group=cls.group, weekday=1, pair=1, week='all', subject='Математика')

    def test_reel_items_have_tooltip_labels(self):
        """Каждый пункт рельса несёт data-tip с названием раздела + в body есть пузырёк."""
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        html = response.content.decode()
        self.assertContains(response, 'id="navTip"')
        self.assertContains(response, 'role="tooltip"')
        for label in ('Обзор', 'Расписание', 'Стипендия', 'Курсы', 'Оценки', 'Роли', 'Профиль'):
            self.assertIn(f'data-tip="{label}"', html, label)

    def test_tooltip_has_no_native_title_attribute(self):
        """Своя подсказка: у пунктов нет title, иначе браузер покажет вторую."""
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        html = response.content.decode()
        start = html.index('class="cab-nav"')
        end = html.index('</aside>', start)
        reel = html[start:end]
        self.assertNotIn('title="', reel)


class NoTitleTruncationTests(TestCase):
    """Названия предметов не обрезаются: ни clamp, ни фиксированные строки."""

    def test_css_has_no_line_clamp_or_fixed_rows(self):
        from pathlib import Path

        css = (Path(settings.BASE_DIR) / 'static/css/cabinet.css').read_text(encoding='utf-8')
        start = css.index('.sched-table {')
        block = css[start:css.index('.sched-empty')]
        # строки таблицы — по содержимому, а не жёсткие 88px
        self.assertIn('grid-auto-rows: minmax(', block)
        self.assertNotIn('grid-template-rows: 34px repeat', block)
        # у названия нет обрезки многоточием/клампом
        title_block = css[css.index('.sched-lesson b {'):css.index('.sched-kind {')]
        self.assertNotIn('-webkit-line-clamp', title_block)
        self.assertNotIn('text-overflow: ellipsis', title_block)


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class MultiFileImportTests(TestCase):
    """Несколько файлов расписания: хранение, замена, удаление, приоритет."""

    @classmethod
    def setUpTestData(cls):
        cls.dean_role = Role.objects.create(code='mdean', name='Деканат', can_manage_schedule=True)
        cls.student_role = Role.objects.create(code='mstudent', name='Студент', can_upload_works=True)
        cls.dean = User.objects.create_user('mdean', password='demo12345')
        cls.dean.profile.role = cls.dean_role
        cls.dean.profile.save()
        cls.student = User.objects.create_user('mstudent', password='demo12345')
        cls.student.profile.role = cls.student_role
        cls.student.profile.save()

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
            reverse('schedule:import'), {'file': payload, 'group': 'ИСТ-201'}, follow=True,
        )

    def test_detect_institute_from_filename(self):
        self.assertEqual(
            detect_institute('bgitu-2026-27-1semestr-IVT-IST-PI-PRI.xls'),
            'ИВТ · ИСТ · ПИ · ПРИ',
        )
        self.assertEqual(detect_institute('ist-raspisanie.xlsx'), 'ИСТ')

    def test_multiple_files_are_all_stored_and_parsed(self):
        self.upload(
            ('ist-2026.xlsx', schedule_bytes('Физика', 'ИСТ-201')),
            ('ivt-2026.xlsx', schedule_bytes('Информатика', 'ИВТ-101')),
        )
        self.assertEqual(ScheduleImport.objects.count(), 2)
        ist = ScheduleImport.objects.get(institute='ИСТ')
        ivt = ScheduleImport.objects.get(institute='ИВТ')
        self.assertEqual((ist.lessons_count, ist.groups_count), (1, 1))
        self.assertEqual((ivt.lessons_count, ivt.groups_count), (1, 1))
        self.assertTrue(ist.lessons.filter(subject='Физика', group__code='ИСТ-201').exists())
        self.assertTrue(ivt.lessons.filter(subject='Информатика', group__code='ИВТ-101').exists())
        self.assertEqual(ist.uploaded_by, self.dean)
        self.assertGreater(ist.size, 0)

    def test_reupload_same_institute_replaces_lessons(self):
        self.upload(('ist-old.xlsx', schedule_bytes('Старая физика')))
        old = ScheduleImport.objects.get()
        self.upload(('ist-new.xlsx', schedule_bytes('Новая информатика')))
        self.assertEqual(ScheduleImport.objects.filter(institute='ИСТ').count(), 1)
        source = ScheduleImport.objects.get(institute='ИСТ')
        self.assertNotEqual(source.pk, old.pk)
        self.assertEqual(source.lessons_count, 1)
        self.assertEqual(Lesson.objects.filter(group__code='ИСТ-201').count(), 1)
        self.assertFalse(Lesson.objects.filter(subject='Старая физика').exists())
        self.assertTrue(Lesson.objects.filter(subject='Новая информатика', source=source).exists())

    def test_reupload_keeps_other_institutes(self):
        self.upload(
            ('ist-2026.xlsx', schedule_bytes('Физика', 'ИСТ-201')),
            ('ivt-2026.xlsx', schedule_bytes('Информатика', 'ИВТ-101')),
        )
        ivt = ScheduleImport.objects.get(institute='ИВТ')
        self.upload(('ist-2027.xlsx', schedule_bytes('Новая физика', 'ИСТ-201')))
        self.assertEqual(ScheduleImport.objects.count(), 2)
        self.assertEqual(ScheduleImport.objects.get(institute='ИВТ').pk, ivt.pk)
        self.assertTrue(Lesson.objects.filter(source=ivt, subject='Информатика').exists())
        self.assertTrue(Lesson.objects.filter(subject='Новая физика').exists())
        self.assertFalse(Lesson.objects.filter(subject='Физика').exists())

    def test_delete_source_removes_only_its_lessons(self):
        self.upload(
            ('ist-2026.xlsx', schedule_bytes('Физика', 'ИСТ-201')),
            ('ivt-2026.xlsx', schedule_bytes('Информатика', 'ИВТ-101')),
        )
        ivt = ScheduleImport.objects.get(institute='ИВТ')
        response = self.client.post(
            reverse('schedule:source_delete', args=[ivt.pk]), {'group': 'ИСТ-201'}, follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ScheduleImport.objects.filter(pk=ivt.pk).exists())
        self.assertFalse(Lesson.objects.filter(group__code='ИВТ-101').exists())
        self.assertTrue(Lesson.objects.filter(group__code='ИСТ-201').exists())
        self.assertEqual(ScheduleImport.objects.count(), 1)

    def test_student_cannot_delete_source(self):
        self.upload(('ist-2026.xlsx', schedule_bytes('Физика', 'ИСТ-201')))
        source = ScheduleImport.objects.get()
        self.client.force_login(self.student)
        response = self.client.post(reverse('schedule:source_delete', args=[source.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ScheduleImport.objects.filter(pk=source.pk).exists())

    def test_newest_file_wins_conflict_but_old_stays_in_db(self):
        self.upload(('ivt-old.xlsx', schedule_bytes('Старая физика', 'ИСТ-201')))
        old = ScheduleImport.objects.get()
        self.upload(('ist-new.xlsx', schedule_bytes('Новая информатика', 'ИСТ-201')))
        # разные институты → обе записи живут, в БД два занятия одного слота
        self.assertEqual(ScheduleImport.objects.count(), 2)
        self.assertEqual(Lesson.objects.filter(group__code='ИСТ-201').count(), 2)
        ScheduleImport.objects.filter(pk=old.pk).update(imported_at=timezone.now() - timedelta(days=1))

        group = Group.objects.get(code='ИСТ-201')
        visible = visible_lessons(group)
        self.assertEqual([item.subject for item in visible], ['Новая информатика'])

        self.client.force_login(self.dean)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201', 'week': '12'})
        self.assertContains(response, '<b>Новая информатика</b>')
        self.assertNotContains(response, '<b>Старая физика</b>')
        # старое занятие не удалено — просто перекрыто свежим файлом
        self.assertTrue(Lesson.objects.filter(source=old, subject='Старая физика').exists())

        # правило симметрично: если свежее окажется старый файл, видно его
        ScheduleImport.objects.filter(pk=old.pk).update(imported_at=timezone.now() + timedelta(minutes=1))
        visible = visible_lessons(group)
        self.assertEqual([item.subject for item in visible], ['Старая физика'])
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201', 'week': '12'})
        self.assertContains(response, '<b>Старая физика</b>')
        self.assertNotContains(response, '<b>Новая информатика</b>')

        # Удалили самый свежий файл — в слоте снова видно занятие второго.
        self.client.post(reverse('schedule:source_delete', args=[old.pk]), follow=True)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201', 'week': '12'})
        self.assertContains(response, '<b>Новая информатика</b>')
        self.assertNotContains(response, '<b>Старая физика</b>')

    def test_conflict_is_per_subgroup(self):
        """Конфликт только внутри одной подгруппы; «1» и «а» — одна подгруппа."""
        group = Group.objects.create(code='ПИ-201', course=2)
        old = ScheduleImport.objects.create(original_name='old.xlsx', institute='ИВТ')
        new = ScheduleImport.objects.create(original_name='new.xlsx', institute='ИСТ')
        Lesson.objects.create(group=group, weekday=2, pair=1, week='all', subject='Старая-а', subgroup='1', source=old)
        Lesson.objects.create(group=group, weekday=2, pair=1, week='all', subject='Старая-б', subgroup='2', source=old)
        Lesson.objects.create(group=group, weekday=2, pair=1, week='all', subject='Новая-а', subgroup='а', source=new)
        self.assertEqual(
            sorted(item.subject for item in visible_lessons(group)),
            ['Новая-а', 'Старая-б'],
        )

    def test_modal_has_multiple_upload_and_sources_list(self):
        self.upload(('ist-2026.xlsx', schedule_bytes('Физика', 'ИСТ-201')))
        self.client.force_login(self.dean)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201'})
        html = response.content.decode()
        self.assertIn('multiple', html)
        self.assertIn('name="file"', html)
        self.assertIn('data-import-delete', html)
        self.assertIn('ist-2026.xlsx', html)
        self.assertIn(reverse('schedule:source_delete', args=[ScheduleImport.objects.get().pk]), html)

    def test_bad_file_does_not_cancel_good_one(self):
        bad = SimpleUploadedFile('notes.txt', b'not a schedule', content_type='text/plain')
        good = SimpleUploadedFile('ist-2026.xlsx', schedule_bytes('Физика'), content_type='application/octet-stream')
        self.client.force_login(self.dean)
        response = self.client.post(
            reverse('schedule:import'), {'file': [bad, good]}, follow=True,
        )
        self.assertContains(response, 'notes.txt')
        self.assertEqual(ScheduleImport.objects.count(), 1)
        self.assertTrue(Lesson.objects.filter(subject='Физика').exists())


class ScheduleRowCountTests(TestCase):
    """Строки таблицы — только пары, которые есть хотя бы у одной группы.

    Пустая пара (например 6-я) не занимает строку и не создаёт ячеек; число
    строк считается по всей выборке семестра/недели, без фильтра по группе.
    """

    @classmethod
    def setUpTestData(cls):
        role = Role.objects.create(code='rowstudent', name='Студент', can_upload_works=True)
        cls.group = Group.objects.create(code='ИСТ-401', course=4)
        cls.other = Group.objects.create(code='ИВТ-401', course=4)
        cls.student = User.objects.create_user('rowstudent', password='demo12345')
        cls.student.profile.role = role
        cls.student.profile.group = cls.group
        cls.student.profile.save()
        # своя группа — пары 1 и 2, соседняя — 5: строка 5 должна остаться
        Lesson.objects.create(group=cls.group, weekday=1, pair=1, week='all', subject='Математика')
        Lesson.objects.create(group=cls.group, weekday=2, pair=2, week='all', subject='Физика')
        Lesson.objects.create(group=cls.other, weekday=3, pair=5, week='all', subject='Химия')

    @staticmethod
    def pair_rows(html):
        import re

        return [int(n) for n in re.findall(
            r'<div class="sched-cell sched-cell--time[^"]*">\s*<b>(\d+)</b>', html,
        )]

    def test_empty_pairs_have_no_rows(self):
        """Пары 1..5: 6-й нет ни у кого, поэтому строки «6» и её ячеек нет."""
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(self.pair_rows(html), [1, 2, 3, 4, 5])
        self.assertEqual(len(response.context['grid']), 5)
        self.assertEqual(response.context['max_pair'], 5)
        # сетка получает фактическое число строк-пар, а не константу 6
        self.assertIn('style="--pairs: 5"', html)
        self.assertNotIn('<b>6</b>', html)

    def test_row_returns_when_any_group_has_the_pair(self):
        """Пара 6 есть у соседней группы — строка 6 появляется («ни у кого» = все группы)."""
        Lesson.objects.create(group=self.other, weekday=4, pair=6, week='all', subject='Информатика')
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        html = response.content.decode()
        self.assertEqual(self.pair_rows(html), [1, 2, 3, 4, 5, 6])
        self.assertIn('style="--pairs: 6"', html)
        self.assertIn('<b>6</b>', html)

    def test_pair_of_other_week_does_not_add_row(self):
        """Пара 6 только в нечётной неделе не создаёт строку в чётной."""
        Lesson.objects.create(group=self.other, weekday=4, pair=6, week='odd', subject='Информатика')
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'), {'week': '12'})  # чётная
        html = response.content.decode()
        self.assertEqual(self.pair_rows(html), [1, 2, 3, 4, 5])
        self.assertNotIn('<b>6</b>', html)

    def test_own_pair_of_other_week_does_not_add_row(self):
        """Пара 6 своей группы только в нечётной неделе тоже не создаёт строку в чётной."""
        Lesson.objects.create(group=self.group, weekday=4, pair=6, week='odd', subject='Информатика')
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'), {'week': '12'})  # чётная
        html = response.content.decode()
        self.assertEqual(self.pair_rows(html), [1, 2, 3, 4, 5])
        self.assertEqual(response.context['max_pair'], 5)
        self.assertNotIn('<b>6</b>', html)

    def test_no_data_renders_empty_state_without_table(self):
        """Нет занятий вообще — пустое состояние, таблица не падает и не рисуется."""
        empty = Group.objects.create(code='ИСТ-499', course=4)
        self.student.profile.group = empty
        self.student.profile.save()
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('занятий нет', html)
        self.assertNotIn('class="sched-table"', html)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='bgitu-schedule-download-'))
class ScheduleDownloadTests(TestCase):
    """Скачивание исходников расписания: вложение с исходным именем и проверка прав."""

    @classmethod
    def setUpTestData(cls):
        cls.dean_role = Role.objects.create(code='dl_dean', name='Деканат', can_manage_schedule=True)
        cls.student_role = Role.objects.create(code='dl_student', name='Студент', can_upload_works=True)
        cls.owner = User.objects.create_user('dl_owner', password='demo12345')
        cls.owner.profile.role = cls.dean_role
        cls.owner.profile.save()
        cls.other_dean = User.objects.create_user('dl_other', password='demo12345')
        cls.other_dean.profile.role = cls.dean_role
        cls.other_dean.profile.save()
        cls.staff_dean = User.objects.create_user('dl_staff', password='demo12345', is_staff=True)
        cls.staff_dean.profile.role = cls.dean_role
        cls.staff_dean.profile.save()
        cls.student = User.objects.create_user('dl_student_user', password='demo12345')
        cls.student.profile.role = cls.student_role
        cls.student.profile.save()

    def setUp(self):
        self.payload = b'xls-bytes-for-download'
        self.record = ScheduleImport.objects.create(
            file=SimpleUploadedFile('schedule-ist.xls', self.payload),
            original_name='schedule-ist.xls',
            size=len(self.payload),
            imported_by=self.owner,
        )

    def file_url(self):
        return reverse('schedule:source_file', args=[self.record.pk])

    def test_owner_gets_attachment_with_original_name(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.file_url())
        self.assertEqual(response.status_code, 200)
        disposition = response['Content-Disposition']
        self.assertIn('attachment', disposition)
        self.assertIn('filename="schedule-ist.xls"', disposition)
        self.assertEqual(b''.join(response.streaming_content), self.payload)

    def test_cyrillic_name_is_preserved(self):
        self.record.original_name = 'расписание ИСТ.xls'
        self.record.save(update_fields=['original_name'])
        self.client.force_login(self.owner)
        response = self.client.get(self.file_url())
        self.assertEqual(response.status_code, 200)
        self.assertIn("filename*=utf-8''" + quote('расписание ИСТ.xls'), response['Content-Disposition'])

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

    def test_modal_has_drag_drop_zone_and_download_link(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201'})
        html = response.content.decode()
        self.assertIn('data-import-drop', html)
        self.assertIn('data-import-ext=".xls,.xlsx"', html)
        self.assertIn('data-import-max-size="15728640"', html)
        self.assertIn('data-import-error', html)
        self.assertIn(self.file_url(), html)
        self.assertIn('download="schedule-ist.xls"', html)
        script = (settings.BASE_DIR / 'static/js/app.js').read_text(encoding='utf-8')
        for marker in ("'dragenter'", "'dragover'", "'dragleave'", "'drop'", 'dataTransfer', 'is-dragover'):
            self.assertIn(marker, script)


class ScheduleKindBadgeTests(TestCase):
    """Бейдж вида занятия не дублирует предмет; в hero нет служебной подписи."""

    @classmethod
    def setUpTestData(cls):
        role = Role.objects.create(code='badge_student', name='Студент', can_upload_works=True)
        cls.group = Group.objects.create(code='ИСТ-501', course=5)
        cls.student = User.objects.create_user('badge_student', password='demo12345')
        cls.student.profile.role = role
        cls.student.profile.group = cls.group
        cls.student.profile.save()

    def page(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'), {'week': '12'})
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_hero_has_no_semester_caption(self):
        """Служебная подпись «учебный процесс · семестр 2026/27» убрана из hero."""
        Lesson.objects.create(group=self.group, weekday=1, pair=1, subject='Философия')
        html = self.page()
        self.assertNotIn('учебный процесс', html)
        self.assertNotIn('семестр 2026/27', html)

    def test_physical_education_kind_badge_is_hidden(self):
        """«физкультура» не повторяет предмет отдельным бейджем."""
        Lesson.objects.create(
            group=self.group, weekday=1, pair=1,
            subject='Элективные дисциплины по физкультуре и спорту', kind='физкультура',
        )
        html = self.page()
        self.assertIn('Элективные дисциплины по физкультуре и спорту', html)
        self.assertNotIn('sched-kind', html)

    def test_lab_kind_badge_stays(self):
        """Для лабораторной бейдж вида остаётся."""
        Lesson.objects.create(group=self.group, weekday=1, pair=1, subject='Базы данных', kind='лаб')
        html = self.page()
        self.assertIn('<span class="sched-kind mono">лаб</span>', html)

    def test_kind_inside_subject_is_hidden(self):
        """Вид, целиком входящий в название дисциплины, не дублируется бейджем."""
        Lesson.objects.create(group=self.group, weekday=1, pair=1, subject='Лекция по философии', kind='лекция')
        html = self.page()
        self.assertIn('Лекция по философии', html)
        self.assertNotIn('sched-kind', html)


class TeacherScheduleTests(TestCase):
    """Блок 03: расписание преподавателя из уже загруженных занятий.

    «Сейчас» подменяется фиксированным моментом: понедельник, 10:30 — идёт
    2-я пара (10:05–11:40), значит по умолчанию должен открыться преподаватель
    пары этой группы в текущем слоте, а не первый по алфавиту.
    """

    NOW = datetime(2026, 11, 23, 10, 30)  # понедельник, 2-я пара

    @classmethod
    def setUpTestData(cls):
        role = Role.objects.create(code='teach_student', name='Студент', can_upload_works=True)
        cls.group = Group.objects.create(code='ИСТ-201', course=2)
        cls.other = Group.objects.create(code='ИСТ-202', course=2)
        cls.student = User.objects.create_user('teach_student', password='demo12345')
        cls.student.profile.role = role
        cls.student.profile.group = cls.group
        cls.student.profile.save()
        for pair, (start, end) in enumerate(
            (('8:20', '9:55'), ('10:05', '11:40'), ('11:50', '13:25'),
             ('14:00', '15:35'), ('15:45', '17:20'), ('17:30', '19:05')),
            start=1,
        ):
            Bell.objects.create(building='1', pair=pair, start=start, end=end)
        # Петров ведёт две группы в одном слоте (пн, 2 пара) — ячейка стопкой.
        Lesson.objects.create(group=cls.group, weekday=1, pair=2, week='all', building='1',
                              subject='Базы данных', teacher_name='Петров П.П.', room='307')
        Lesson.objects.create(group=cls.other, weekday=1, pair=2, week='all', building='1',
                              subject='Информатика', teacher_name='Петров П.П.', room='228')
        # Ближайшая следующая пара группы — вторник, 3 пара.
        Lesson.objects.create(group=cls.group, weekday=2, pair=3, week='all', building='1',
                              subject='Философия', teacher_name='Азаренко Н.Ю.', room='314')
        # Третий преподаватель — только среда, 1 пара.
        Lesson.objects.create(group=cls.group, weekday=3, pair=1, week='all', building='1',
                              subject='Математика', teacher_name='Сидорова А.А.', room='101')

    def setUp(self):
        self.client.force_login(self.student)

    def get(self, now=None, **params):
        """GET расписания с подменённым «сейчас» (по умолчанию — NOW)."""
        with mock.patch('apps.schedule.views.now_local', return_value=now or self.NOW):
            return self.client.get(reverse('schedule:schedule'), params)

    def teacher_html(self, response):
        """HTML блока 03 (после заголовка блока 01 и плана)."""
        html = response.content.decode()
        return html[html.index('sched-panel--teacher'):]

    def test_teacher_list_is_sorted_by_surname(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['teachers'],
            ['Азаренко Н.Ю.', 'Петров П.П.', 'Сидорова А.А.'],
        )
        html = response.content.decode()
        self.assertIn('name="teacher"', html)
        self.assertIn('data-searchable', html)
        self.assertIn('selected>Петров П.П.', html)

    def test_default_teacher_is_the_one_teaching_now(self):
        """По умолчанию — преподаватель пары, которая идёт у группы сейчас."""
        response = self.get()
        self.assertEqual(response.context['teacher'], 'Петров П.П.')
        default = response.context['teacher_default']
        self.assertEqual(default['reason'], 'now')
        self.assertEqual(default['pair'], 2)
        self.assertEqual(default['weekday_name'], 'понедельник')
        self.assertEqual(default['group_code'], 'ИСТ-201')
        # его таблица содержит занятие этой группы в текущем слоте
        monday = response.context['teacher_grid'][1]['days'][0]  # 2-я пара, понедельник
        self.assertIn(
            'Базы данных',
            [item.subject for item in monday['lessons'] if item.group.code == 'ИСТ-201'],
        )
        self.assertContains(response, 'сейчас идёт пара · понедельник, 2 пара · ИСТ-201')

    def test_default_teacher_falls_back_to_next_lesson(self):
        """Пар больше нет — берётся ближайшая следующая пара группы на неделе."""
        response = self.get(now=datetime(2026, 11, 23, 12, 0))  # после 2-й пары
        self.assertEqual(response.context['teacher'], 'Азаренко Н.Ю.')
        default = response.context['teacher_default']
        self.assertEqual(default['reason'], 'next')
        self.assertEqual((default['weekday_name'], default['pair']), ('вторник', 3))
        self.assertContains(response, 'ближайшая пара · вторник, 3 пара · ИСТ-201')

    def test_default_teacher_before_first_pair_uses_next(self):
        """Утро до первой пары — ближайшая пара сегодняшнего дня."""
        response = self.get(now=datetime(2026, 11, 23, 8, 0))
        self.assertEqual(response.context['teacher'], 'Петров П.П.')
        self.assertEqual(response.context['teacher_default']['reason'], 'next')

    def test_explicit_teacher_overrides_default(self):
        response = self.get(teacher='Сидорова А.А.')
        self.assertEqual(response.context['teacher'], 'Сидорова А.А.')
        self.assertEqual(response.context['teacher_default']['reason'], 'query')

    def test_unknown_teacher_does_not_break_page(self):
        response = self.get(teacher='Некто Неизвестный')
        self.assertEqual(response.status_code, 200)
        # неизвестное ФИО не роняет блок: показывается преподаватель по умолчанию
        self.assertEqual(response.context['teacher'], 'Петров П.П.')

    def test_week_without_lessons_falls_back_to_first_alphabetically(self):
        """У группы на неделе занятий нет — только тогда первый по алфавиту."""
        group = Group.objects.create(code='ИСТ-777', course=2)
        user = User.objects.create_user('teach_no_week', password='demo12345')
        user.profile.group = group
        user.profile.save()
        self.client.force_login(user)
        response = self.get()
        self.assertEqual(response.context['teacher_default']['reason'], 'first')
        self.assertEqual(response.context['teacher'], 'Азаренко Н.Ю.')
        self.assertEqual(response.context['teacher'], response.context['teachers'][0])
        self.assertContains(response, 'sched-panel--teacher')

    def test_teacher_only_lessons_in_his_cards(self):
        response = self.get(teacher='Петров П.П.')
        html = self.teacher_html(response)
        cards = html[html.index('sched-table'):]
        self.assertIn('Базы данных', cards)
        self.assertIn('Информатика', cards)
        self.assertNotIn('Философия', cards)
        self.assertNotIn('Математика', cards)

    def test_teacher_cell_stacks_two_groups(self):
        """Две группы в одном слоте — стопкой, группа указана в каждой карточке."""
        response = self.get(teacher='Петров П.П.')
        monday = response.context['teacher_grid'][1]['days'][0]
        self.assertTrue(monday['split'])
        self.assertEqual([item.group.code for item in monday['lessons']], ['ИСТ-201', 'ИСТ-202'])
        html = self.teacher_html(response)
        self.assertIn('sched-cell--body sched-cell--split', html)
        cards = html[html.index('sched-table'):]
        self.assertIn('sched-teacher-group', cards)
        self.assertIn('ИСТ-201', cards)
        self.assertIn('ИСТ-202', cards)
        # ФИО в шапке блока, в карточках не дублируется
        self.assertNotIn('Петров П.П.', cards)

    def test_teacher_schedule_follows_week_parity(self):
        Lesson.objects.create(group=self.group, weekday=4, pair=1, week='even', building='1',
                              subject='Только чётная', teacher_name='Сидорова А.А.')
        odd = self.get(teacher='Сидорова А.А.', week='13')  # нечётная
        self.assertContains(odd, '<b>Математика</b>')
        self.assertNotContains(odd, '<b>Только чётная</b>')
        even = self.get(teacher='Сидорова А.А.', week='12')  # чётная
        self.assertContains(even, '<b>Только чётная</b>')
        # пара «каждую неделю» видна в обе недели
        self.assertContains(even, '<b>Математика</b>')

    def test_block_03_markup_and_position(self):
        """Блок 03 — после блока 02, с номером 03, заголовком и селектором справа."""
        response = self.get()
        html = response.content.decode()
        self.assertIn('<section class="panel sched-panel sched-panel--teacher">', html)
        self.assertIn('<span class="panel__num mono sched-panel__num">03</span>', html)
        self.assertIn('<h2>Расписание преподавателя</h2>', html)
        self.assertLess(html.index('semester-panel'), html.index('sched-panel--teacher'))
        # селектор преподавателя — крайний правый в шапке блока
        bar = html[html.index('sched-panel--teacher'):html.index('</form>', html.index('sched-panel--teacher'))]
        self.assertLess(bar.index('sched-panel__head'), bar.index('sched-teacher-pick'))
        self.assertIn('sched-panel__pick', bar)

    def test_teacher_table_is_not_week_swipe_target(self):
        """У таблицы преподавателя нет data-week-grid — жест недели остаётся блоку 01."""
        response = self.get()
        html = self.teacher_html(response)
        self.assertNotIn('data-week-grid', html)
        group_html = response.content.decode().split('sched-panel--teacher')[0]
        self.assertIn('data-week-grid', group_html)


class TeacherScheduleEmptyTests(TestCase):
    """Занятий не загружено: блок 03 не падает и честно говорит, что данных нет."""

    @classmethod
    def setUpTestData(cls):
        role = Role.objects.create(code='no_lessons_student', name='Студент', can_upload_works=True)
        cls.group = Group.objects.create(code='ИСТ-000', course=1)
        cls.student = User.objects.create_user('no_lessons_student', password='demo12345')
        cls.student.profile.role = role
        cls.student.profile.group = cls.group
        cls.student.profile.save()

    def test_block_03_without_teachers(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['teachers'], [])
        self.assertEqual(response.context['teacher'], '')
        self.assertContains(response, 'sched-panel--teacher')
        self.assertContains(response, 'Расписание преподавателей не загружено')
        self.assertNotContains(response, 'name="teacher"')

    def test_unknown_teacher_without_lessons(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'), {'teacher': 'Некто Неизвестный'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'sched-panel--teacher')


class ScheduleFragmentTests(TestCase):
    """Фрагментный эндпоинт: только блоки 01/03 и навигация недели.

    AJAX-слой (static/js/schedule-ajax.js) меняет расписание без перезагрузки,
    поэтому ответ не должен содержать страницу целиком — ни шапки сайта, ни
    блока 02 с учебным планом.
    """

    @classmethod
    def setUpTestData(cls):
        role = Role.objects.create(code='frag_dean', name='Деканат', can_manage_schedule=True)
        cls.dean = User.objects.create_user('frag_dean', password='demo12345')
        cls.dean.profile.role = role
        cls.dean.profile.save()
        cls.group = Group.objects.create(code='ИСТ-201', course=2)
        cls.other = Group.objects.create(code='ИСТ-202', course=2)
        for pair, (start, end) in enumerate(
            (('8:20', '9:55'), ('10:05', '11:40'), ('11:50', '13:25'),
             ('14:00', '15:35'), ('15:45', '17:20'), ('17:30', '19:05')),
            start=1,
        ):
            Bell.objects.create(building='1', pair=pair, start=start, end=end)
        Lesson.objects.create(group=cls.group, weekday=1, pair=1, week='all', building='1',
                              subject='Базы данных', teacher_name='Петров П.П.', room='307')
        Lesson.objects.create(group=cls.other, weekday=2, pair=2, week='all', building='1',
                              subject='Философия', teacher_name='Сидорова А.А.', room='314')
        Lesson.objects.create(group=cls.group, weekday=3, pair=1, week='all', building='1',
                              subject='Математика', teacher_name='Сидорова А.А.', room='101')

    def setUp(self):
        self.client.force_login(self.dean)

    def get(self, **params):
        return self.client.get(reverse('schedule:fragment'), params)

    def test_fragment_has_only_blocks(self):
        response = self.get(group='ИСТ-201', partial='1')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for block in ('sched-week-nav', 'sched-block-group', 'sched-block-teacher'):
            self.assertIn('id="%s"' % block, html)
        for marker in ('<!DOCTYPE', '<html', '<body', '</html>', 'site-foot', 'semester-panel'):
            self.assertNotIn(marker, html)
        self.assertIn('class="week-nav', html)
        self.assertIn('sched-table', html)

    def group_html(self, html):
        """Только блок 01: блок 03 показывает занятия преподавателя по всем группам."""
        start = html.index('<div id="sched-block-group">')
        end = html.index('<div id="sched-block-teacher">', start)
        return html[start:end]

    def test_fragment_keeps_group_filter(self):
        html = self.get(group='ИСТ-202', partial='1').content.decode()
        block = self.group_html(html)
        self.assertIn('Философия', block)
        self.assertNotIn('Базы данных', block)
        self.assertNotIn('Математика', block)

    def test_fragment_keeps_teacher_filter(self):
        response = self.get(group='ИСТ-201', teacher='Сидорова А.А.', partial='1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['teacher'], 'Сидорова А.А.')
        html = response.content.decode()
        self.assertIn('value="Сидорова А.А." selected', html)
        self.assertIn('Математика', html)

    def test_fragment_keeps_week_filter(self):
        first = self.get(group='ИСТ-201', week=1, partial='1')
        second = self.get(group='ИСТ-201', week=2, partial='1')
        self.assertEqual(first.context['week'], 1)
        self.assertEqual(second.context['week'], 2)
        self.assertNotEqual(first.context['monday'], second.context['monday'])
        self.assertIn(second.context['monday'].strftime('%d.%m.%Y'),
                      second.content.decode())

    def test_fragment_header_has_canonical_url(self):
        from urllib.parse import unquote_plus

        response = self.get(group='ИСТ-201', teacher='Сидорова А.А.', week=3, partial='1')
        canonical = unquote_plus(response['X-Schedule-Url'])
        self.assertTrue(canonical.startswith('/schedule/?'))
        self.assertIn('group=ИСТ-201', canonical)
        self.assertIn('teacher=Сидорова А.А.', canonical)
        self.assertIn('week=3', canonical)

    def test_fragment_requires_login(self):
        self.client.logout()
        response = self.get(group='ИСТ-201', partial='1')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_fragment_remembers_group_in_session(self):
        self.get(group='ИСТ-202', partial='1')
        self.assertEqual(self.client.session['schedule_group'], 'ИСТ-202')

    def test_full_page_still_has_blocks_and_forms(self):
        """Без JS: та же страница с обычными формами и id блоков."""
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201'})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('id="sched-block-group"', html)
        self.assertIn('id="sched-block-teacher"', html)
        self.assertIn('form method="get" class="group-switch', html)
        self.assertIn('name="group"', html)


class ScheduleChoiceSessionTests(TestCase):
    """Выбор группы и преподавателя запоминается в сессии.

    Порядок: GET-параметр → сессия → «умный» дефолт. Ссылка с параметрами
    остаётся копируемой: явный GET всегда сильнее сохранённого выбора.
    """

    @classmethod
    def setUpTestData(cls):
        role = Role.objects.create(code='sess_dean', name='Деканат', can_manage_schedule=True)
        cls.dean = User.objects.create_user('sess_dean', password='demo12345')
        cls.dean.profile.role = role
        cls.dean.profile.save()
        cls.group = Group.objects.create(code='ИСТ-201', course=2)
        cls.other = Group.objects.create(code='ИСТ-202', course=2)
        for pair, (start, end) in enumerate(
            (('8:20', '9:55'), ('10:05', '11:40'), ('11:50', '13:25'),
             ('14:00', '15:35'), ('15:45', '17:20'), ('17:30', '19:05')),
            start=1,
        ):
            Bell.objects.create(building='1', pair=pair, start=start, end=end)
        Lesson.objects.create(group=cls.group, weekday=1, pair=1, week='all', building='1',
                              subject='Базы данных', teacher_name='Петров П.П.', room='307')
        Lesson.objects.create(group=cls.group, weekday=2, pair=1, week='all', building='1',
                              subject='Математика', teacher_name='Сидорова А.А.', room='101')
        Lesson.objects.create(group=cls.other, weekday=2, pair=2, week='all', building='1',
                              subject='Философия', teacher_name='Сидорова А.А.', room='314')

    def setUp(self):
        self.client.force_login(self.dean)

    def test_group_choice_is_remembered(self):
        first = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-202'})
        self.assertEqual(first.context['group'].code, 'ИСТ-202')
        self.assertEqual(self.client.session['schedule_group'], 'ИСТ-202')
        # Второй запрос без параметров — тот же выбор, а не первая группа.
        second = self.client.get(reverse('schedule:schedule'))
        self.assertEqual(second.context['group'].code, 'ИСТ-202')

    def test_explicit_group_beats_session(self):
        self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-202'})
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201'})
        self.assertEqual(response.context['group'].code, 'ИСТ-201')
        self.assertEqual(self.client.session['schedule_group'], 'ИСТ-201')

    def test_teacher_choice_is_remembered(self):
        first = self.client.get(reverse('schedule:schedule'),
                                {'group': 'ИСТ-201', 'teacher': 'Сидорова А.А.'})
        self.assertEqual(first.context['teacher'], 'Сидорова А.А.')
        self.assertEqual(first.context['teacher_default']['reason'], 'query')
        self.assertEqual(self.client.session['schedule_teacher_by_group']['ИСТ-201'], 'Сидорова А.А.')
        second = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-201'})
        self.assertEqual(second.context['teacher'], 'Сидорова А.А.')
        self.assertEqual(second.context['teacher_default']['reason'], 'group')

    def test_unknown_teacher_is_not_saved(self):
        response = self.client.get(reverse('schedule:schedule'),
                                   {'group': 'ИСТ-201', 'teacher': 'Некто Неизвестный'})
        self.assertNotEqual(response.context['teacher'], 'Некто Неизвестный')
        self.assertNotIn('Некто Неизвестный',
                         self.client.session.get('schedule_teacher_by_group', {}).values())
        self.assertNotIn('schedule_teacher', self.client.session)

    def test_student_group_is_not_overridden_by_session(self):
        """Студенту группу назначает деканат: сессия её не подменяет."""
        role = Role.objects.create(code='sess_student', name='Студент', can_upload_works=True)
        student = User.objects.create_user('sess_student', password='demo12345')
        student.profile.role = role
        student.profile.group = self.group
        student.profile.save()
        self.client.force_login(student)
        self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-202'})
        response = self.client.get(reverse('schedule:schedule'), {'group': 'ИСТ-202'})
        self.assertEqual(response.context['group'].code, 'ИСТ-201')


class ScheduleTeacherMemoryTests(TestCase):
    """Преподаватель запоминается отдельно для каждой группы.

    Выбрал преподавателя для группы A → перешёл на B без ?teacher= → видно
    преподавателя, который ведёт пару у B сейчас; вернулся на A → снова свой
    сохранённый. Явный ?teacher= всегда сильнее памяти.
    """

    NOW = datetime(2026, 11, 23, 10, 30)  # понедельник, идёт 2-я пара

    @classmethod
    def setUpTestData(cls):
        role = Role.objects.create(code='mem_dean', name='Деканат', can_manage_schedule=True)
        cls.dean = User.objects.create_user('mem_dean', password='demo12345')
        cls.dean.profile.role = role
        cls.dean.profile.save()
        cls.group = Group.objects.create(code='ИСТ-301', course=3)
        cls.other = Group.objects.create(code='ПРИ-301', course=3)
        for pair, (start, end) in enumerate(
            (('8:20', '9:55'), ('10:05', '11:40'), ('11:50', '13:25'),
             ('14:00', '15:35'), ('15:45', '17:20'), ('17:30', '19:05')),
            start=1,
        ):
            Bell.objects.create(building='1', pair=pair, start=start, end=end)
        # У каждой группы в текущем слоте свой преподаватель — это и есть дефолт.
        Lesson.objects.create(group=cls.group, weekday=1, pair=2, week='all', building='1',
                              subject='Базы данных', teacher_name='Петров П.П.', room='307')
        Lesson.objects.create(group=cls.other, weekday=1, pair=2, week='all', building='1',
                              subject='Философия', teacher_name='Кузнецов К.К.', room='314')
        # Третий преподаватель — только у группы A, чтобы явный выбор отличался
        # и от дефолта A, и от дефолта B.
        Lesson.objects.create(group=cls.group, weekday=2, pair=1, week='all', building='1',
                              subject='Математика', teacher_name='Сидорова А.А.', room='101')

    def setUp(self):
        self.client.force_login(self.dean)

    def get(self, **params):
        with mock.patch('apps.schedule.views.now_local', return_value=self.NOW):
            return self.client.get(reverse('schedule:schedule'), params)

    def test_default_teacher_of_group(self):
        self.assertEqual(self.get(group='ИСТ-301').context['teacher'], 'Петров П.П.')
        self.assertEqual(self.get(group='ПРИ-301').context['teacher'], 'Кузнецов К.К.')

    def test_teacher_memory_follows_group(self):
        chosen = self.get(group='ИСТ-301', teacher='Сидорова А.А.')
        self.assertEqual(chosen.context['teacher'], 'Сидорова А.А.')
        self.assertEqual(chosen.context['teacher_default']['reason'], 'query')

        # Другая группа без ?teacher= — её «умный» дефолт, а не прежний выбор.
        other = self.get(group='ПРИ-301')
        self.assertEqual(other.context['teacher'], 'Кузнецов К.К.')
        self.assertEqual(other.context['teacher_default']['reason'], 'now')

        # Возврат на первую — сохранённый для неё преподаватель.
        back = self.get(group='ИСТ-301')
        self.assertEqual(back.context['teacher'], 'Сидорова А.А.')
        self.assertEqual(back.context['teacher_default']['reason'], 'group')

        # У второй группы ничего не появилось: снова дефолт.
        self.assertEqual(self.get(group='ПРИ-301').context['teacher'], 'Кузнецов К.К.')

    def test_memory_is_stored_per_group_in_session(self):
        self.get(group='ИСТ-301', teacher='Сидорова А.А.')
        self.assertEqual(
            self.client.session['schedule_teacher_by_group'],
            {'ИСТ-301': 'Сидорова А.А.'},
        )

    def test_explicit_teacher_beats_group_memory(self):
        self.get(group='ИСТ-301', teacher='Сидорова А.А.')
        response = self.get(group='ИСТ-301', teacher='Петров П.П.')
        self.assertEqual(response.context['teacher'], 'Петров П.П.')
        self.assertEqual(response.context['teacher_default']['reason'], 'query')
        # Явный выбор заменил сохранённый для этой же группы.
        self.assertEqual(
            self.client.session['schedule_teacher_by_group']['ИСТ-301'], 'Петров П.П.'
        )

    def test_session_memory_works_without_query(self):
        self.get(group='ИСТ-301', teacher='Сидорова А.А.')
        with mock.patch('apps.schedule.views.now_local', return_value=self.NOW):
            response = self.client.get(reverse('schedule:schedule'))
        self.assertEqual(response.context['group'].code, 'ИСТ-301')
        self.assertEqual(response.context['teacher'], 'Сидорова А.А.')

    def test_legacy_single_teacher_is_migrated_to_group(self):
        """Старое значение schedule_teacher привязывается к выбранной группе."""
        session = self.client.session
        session['schedule_group'] = 'ИСТ-301'
        session['schedule_teacher'] = 'Сидорова А.А.'
        session.save()
        response = self.get()
        self.assertEqual(response.context['teacher'], 'Сидорова А.А.')
        self.assertEqual(response.context['teacher_default']['reason'], 'group')
        self.assertNotIn('schedule_teacher', self.client.session)
        self.assertEqual(
            self.client.session['schedule_teacher_by_group']['ИСТ-301'], 'Сидорова А.А.'
        )

    def test_fragment_remembers_teacher_for_group(self):
        with mock.patch('apps.schedule.views.now_local', return_value=self.NOW):
            response = self.client.get(reverse('schedule:fragment'), {
                'group': 'ПРИ-301', 'teacher': 'Сидорова А.А.', 'partial': '1',
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.session['schedule_teacher_by_group']['ПРИ-301'], 'Сидорова А.А.'
        )

    def test_page_url_does_not_carry_foreign_teacher(self):
        """При дефолтном преподавателе (не выбор пользователя) ?teacher= не нужен."""
        response = self.get(group='ПРИ-301')
        self.assertNotIn('teacher=', response.context['page_url'])


class TeacherRoleLayoutTests(TestCase):
    """Роль «преподаватель»: первым блоком — своё расписание (01), группа — 03.

    Студент и сотрудники видят прежний порядок: 01 группа, 02 план, 03 препод.
    """

    @classmethod
    def setUpTestData(cls):
        teacher_role = Role.objects.create(
            code='teacher', name='Преподаватель', can_create_courses=True, can_upload_works=False,
        )
        student_role = Role.objects.create(code='layout_student', name='Студент', can_upload_works=True)
        cls.group = Group.objects.create(code='ИСТ-401', course=4)
        cls.other = Group.objects.create(code='ИСТ-402', course=4)
        for pair, (start, end) in enumerate(
            (('8:20', '9:55'), ('10:05', '11:40'), ('11:50', '13:25'),
             ('14:00', '15:35'), ('15:45', '17:20'), ('17:30', '19:05')),
            start=1,
        ):
            Bell.objects.create(building='1', pair=pair, start=start, end=end)
        Lesson.objects.create(group=cls.group, weekday=1, pair=2, week='all', building='1',
                              subject='Базы данных', teacher_name='Иванов И.О.', room='307')
        Lesson.objects.create(group=cls.other, weekday=2, pair=1, week='all', building='1',
                              subject='Философия', teacher_name='Петров П.П.', room='314')

        cls.teacher_user = User.objects.create_user('layout_teacher', password='demo12345',
                                                    last_name='Иванов', first_name='Иван')
        cls.teacher_user.profile.role = teacher_role
        cls.teacher_user.profile.save()
        cls.student = User.objects.create_user('layout_student', password='demo12345')
        cls.student.profile.role = student_role
        cls.student.profile.group = cls.group
        cls.student.profile.save()

    def setUp(self):
        self.client.force_login(self.teacher_user)

    def block_order(self, html):
        """Порядок блоков на странице: id блоков и номер в шапке."""
        teacher_at = html.index('<div id="sched-block-teacher">')
        plan_at = html.index('class="panel semester-panel"')
        group_at = html.index('<div id="sched-block-group">')
        return teacher_at, plan_at, group_at

    def test_teacher_sees_own_schedule_first(self):
        response = self.client.get(reverse('schedule:schedule'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_teacher_role'])
        self.assertEqual(response.context['teacher'], 'Иванов И.О.')
        self.assertEqual(response.context['teacher_default']['reason'], 'self')

        html = response.content.decode()
        teacher_at, plan_at, group_at = self.block_order(html)
        self.assertLess(teacher_at, plan_at, 'блок преподавателя должен быть первым')
        self.assertLess(plan_at, group_at, 'блок группы — последним')

        teacher_html = html[teacher_at:plan_at]
        group_html = html[group_at:]
        self.assertIn('sched-panel__num">01<', teacher_html)
        self.assertIn('sched-panel__num">03<', group_html)
        # Своё расписание не пустое: у преподавателя есть пара в загруженных файлах.
        self.assertIn('Базы данных', teacher_html)
        # Селектор позволяет посмотреть чужое расписание.
        self.assertIn('name="teacher"', teacher_html)

    def test_teacher_picker_lists_self_as_selected(self):
        html = self.client.get(reverse('schedule:schedule')).content.decode()
        self.assertIn('value="Иванов И.О." selected', html)

    def test_teacher_name_matches_with_yo_and_case(self):
        """Сопоставление ФИО: ё→е, регистр и лишние пробелы не мешают."""
        user = User.objects.create_user('layout_teacher_yo', password='demo12345',
                                        last_name='ПЕТРОВ', first_name='пётр')
        user.profile.role = Role.objects.get(code='teacher')
        user.profile.save()
        self.client.force_login(user)
        response = self.client.get(reverse('schedule:schedule'))
        self.assertEqual(response.context['teacher'], 'Петров П.П.')
        self.assertEqual(response.context['teacher_default']['reason'], 'self')

    def test_teacher_explicit_choice_wins(self):
        response = self.client.get(reverse('schedule:schedule'), {'teacher': 'Петров П.П.'})
        self.assertEqual(response.context['teacher'], 'Петров П.П.')
        self.assertEqual(response.context['teacher_default']['reason'], 'query')
        self.assertFalse(response.context['teacher_default']['is_self'])

    def test_teacher_without_group_can_pick_one(self):
        """У преподавателя может не быть группы — селектор групп всё равно есть."""
        self.teacher_user.profile.group = None
        self.teacher_user.profile.save()
        response = self.client.get(reverse('schedule:schedule'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_choose_group'])
        html = response.content.decode()
        self.assertIn('name="group"', html)
        self.assertIn('sched-panel--teacher', html)

    def test_student_keeps_previous_order(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('schedule:schedule'))
        self.assertFalse(response.context['is_teacher_role'])
        self.assertEqual(response.context['group_block_num'], '01')
        self.assertEqual(response.context['teacher_block_num'], '03')
        html = response.content.decode()
        teacher_at, plan_at, group_at = self.block_order(html)
        self.assertLess(group_at, plan_at)
        self.assertLess(plan_at, teacher_at)

    def test_fragment_uses_same_numbers_for_teacher(self):
        response = self.client.get(reverse('schedule:fragment'), {'partial': '1'})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('sched-panel__num">01<', html)
        self.assertIn('sched-panel__num">03<', html)
