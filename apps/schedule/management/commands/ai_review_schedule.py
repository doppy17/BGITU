"""Разбор файла расписания или плана с коротким резюме локальной модели."""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.schedule.ai_review import (
    review_filename,
    review_import,
    review_plan_file,
    review_plan_source,
    review_schedule_file,
    review_to_markdown,
    review_to_text,
)


class Command(BaseCommand):
    help = (
        'Разбирает расписание и/или учебный план: считает факты кодом, пишет '
        'короткое резюме локальной моделью и сохраняет отчёт в docs/ai-reviews.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--file', dest='schedule_file', help='Файл расписания .xls/.xlsx')
        parser.add_argument('--plan', dest='plan_file', help='Файл плана .xls/.xlsx/.docx/.pdf/.json')
        parser.add_argument('--no-llm', action='store_true', help='Только факты, без резюме модели')
        parser.add_argument(
            '--out', default='docs/ai-reviews',
            help='Папка для Markdown-отчётов (по умолчанию docs/ai-reviews).',
        )

    def handle(self, *args, **options):
        use_llm = not options['no_llm']
        schedule_path = Path(options['schedule_file']).expanduser() if options['schedule_file'] else None
        plan_path = Path(options['plan_file']).expanduser() if options['plan_file'] else None

        if schedule_path and not schedule_path.is_file():
            raise CommandError(f'Файл расписания не найден: {schedule_path}')
        if plan_path and not plan_path.is_file():
            raise CommandError(f'Файл плана не найден: {plan_path}')

        try:
            reviews = []
            if schedule_path:
                reviews.append(self._schedule_review(schedule_path, use_llm))
            if plan_path:
                reviews.append(self._plan_review(plan_path, use_llm))
            if not reviews:
                reviews.extend(self._latest_reviews(use_llm))
        except ValueError as exc:
            raise CommandError(f'Не удалось разобрать файл: {exc}') from exc
        if not reviews:
            raise CommandError(
                'Нет данных для разбора: укажите --file/--plan или загрузите расписание в ЛК.'
            )

        for review in reviews:
            self.stdout.write('')
            self.stdout.write(review_to_text(review))

        path = self._save(reviews, Path(options['out']))
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Отчёт сохранён: {path}'))

    def _schedule_review(self, path, use_llm):
        """Импортированный файл разбираем по базе, новый — по содержимому."""
        from apps.schedule.models import ScheduleImport

        record = (
            ScheduleImport.objects.filter(original_name=path.name)
            .order_by('-imported_at')
            .first()
        )
        if record and record.file and record.file.storage.exists(record.file.name):
            return review_import(record, use_llm=use_llm)
        self.stdout.write(self.style.WARNING(
            f'{path.name}: файла нет среди импортов — разбираю содержимое без записи в базу.'
        ))
        return review_schedule_file(path, use_llm=use_llm)

    def _plan_review(self, path, use_llm):
        from apps.education.models import PlanSource

        source = (
            PlanSource.objects.filter(original_name=path.name)
            .order_by('-uploaded_at')
            .first()
        )
        if source:
            return review_plan_source(source, use_llm=use_llm)
        return review_plan_file(path, use_llm=use_llm)

    def _latest_reviews(self, use_llm):
        from apps.schedule.models import ScheduleImport

        record = ScheduleImport.objects.order_by('-imported_at').first()
        if not record:
            return []
        self.stdout.write(f'Файл не указан — беру последний импорт: {record.original_name}')
        return [review_import(record, use_llm=use_llm)]

    def _save(self, reviews, out):
        folder = out if out.is_absolute() else Path(settings.BASE_DIR) / out
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / review_filename(reviews[0])
        body = '\n\n---\n\n'.join(review_to_markdown(review).rstrip() for review in reviews) + '\n'
        path.write_text(body, encoding='utf-8')
        return path
