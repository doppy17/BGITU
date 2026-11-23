"""Загрузка файлов учебного плана и удаление источников.

Файлы планов можно грузить пачкой: каждый сохраняется отдельным PlanSource,
разбирается и получает свой набор CurriculumEntry. Один битый файл не отменяет
остальные. При конфликте дисциплины в семестре приоритет у самого свежего
файла — выборку делает apps.education.curriculum.resolve_curriculum.
"""
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.permissions import role_required

from .models import CurriculumEntry, PlanSource
from .plan_import import (
    PLAN_EXTENSIONS,
    detect_direction,
    direction_sample,
    entries_to_segments,
    parse_plan,
)

MAX_PLAN_SIZE = 20 * 1024 * 1024


def plan_redirect(group_code=''):
    url = reverse('schedule:schedule')
    return f'{url}?group={group_code}' if group_code else url


@login_required
@role_required('can_manage_schedule')
def plan_import(request):
    """Загрузка одного или нескольких файлов плана (.xls/.xlsx/.docx/.pdf)."""
    if request.method != 'POST':
        return redirect('schedule:schedule')

    uploads = request.FILES.getlist('file')
    if not uploads:
        messages.error(request, 'Выберите файлы учебного плана (.xls, .xlsx, .docx или .pdf).')
        return redirect('schedule:schedule')

    group_code = request.POST.get('group', '')
    imported, errors, total_entries = [], [], 0

    for upload in uploads:
        extension = os.path.splitext(upload.name)[1].lower()
        if extension not in PLAN_EXTENSIONS:
            errors.append(f'{upload.name}: поддерживаются .xls, .xlsx, .docx и .pdf.')
            continue
        if upload.size > MAX_PLAN_SIZE:
            errors.append(f'{upload.name}: файл больше 20 МБ.')
            continue
        record = PlanSource.objects.create(
            file=upload,
            original_name=upload.name[:255],
            size=upload.size,
            uploaded_by=request.user,
        )
        try:
            direction = detect_direction(upload.name, direction_sample(record.file.path))
        except ValueError as exc:
            record.file.delete(save=False)
            record.delete()
            errors.append(f'{upload.name}: {exc}.')
            continue
        record.direction = direction['direction']
        record.direction_code = direction['direction_code']
        record.save(update_fields=['direction', 'direction_code'])
        try:
            segments = entries_to_segments(parse_plan(record.file.path))
        except Exception as exc:  # один битый файл не отменяет остальные
            record.file.delete(save=False)
            record.delete()
            errors.append(f'{upload.name}: не удалось разобрать план — {exc}')
            continue
        if not segments:
            record.file.delete(save=False)
            record.delete()
            errors.append(f'{upload.name}: в файле не найдено ни одной дисциплины с семестрами.')
            continue

        with transaction.atomic():
            CurriculumEntry.objects.filter(source=record).delete()
            CurriculumEntry.objects.bulk_create([
                CurriculumEntry(
                    code=record.direction_code,
                    subject=subject[:200],
                    semester_from=start,
                    semester_to=end,
                    source=record,
                )
                for subject, start, end in segments
            ])
            record.entries_count = len(segments)
            record.note = 'импорт дисциплин из файла плана'
            record.save(update_fields=['entries_count', 'note'])

        imported.append(record)
        total_entries += record.entries_count

    if imported:
        names = ', '.join(item.direction or item.original_name for item in imported)
        messages.success(
            request,
            f'Загружено файлов: {len(imported)} · дисциплин: {total_entries} ({names}).',
        )
    for error in errors:
        messages.error(request, error)
    return redirect(plan_redirect(group_code))


@login_required
@require_POST
@role_required('can_manage_schedule')
def plan_source_delete(request, pk):
    """Удалить файл плана вместе с его дисциплинами."""
    record = get_object_or_404(PlanSource, pk=pk)
    name = record.original_name
    direction = record.direction
    with transaction.atomic():
        CurriculumEntry.objects.filter(source=record).delete()
        record.delete()
    label = f'Направление {direction}' if direction else 'Файл'
    messages.success(request, f'{label}: «{name}» удалён вместе с дисциплинами.')
    return redirect(plan_redirect(request.POST.get('group', '')))
