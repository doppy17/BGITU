"""Профили создаются автоматически для каждого пользователя.

Здесь же — авто-лог перемещений: смена ``Profile.group`` пишет запись в
``ProfileHistory`` со снимком института/направления/курса группы до и после.
"""
from django.contrib.auth.models import User
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Profile, ProfileHistory, Role


@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if not created:
        return
    role = Role.objects.filter(code='student').first()
    Profile.objects.get_or_create(user=instance, defaults={'role': role})


def _snapshot_from_row(row):
    """Снимок группы из словаря значений ORM (может быть пустым)."""
    course = row.get('group__course')
    return {
        'code': row.get('group__code') or '',
        'institute': row.get('group__institute') or '',
        'program': row.get('group__program') or '',
        'course': str(course) if course is not None else '',
    }


def _snapshot_from_group(group):
    if group is None:
        return {'code': '', 'institute': '', 'program': '', 'course': ''}
    return {
        'code': group.code or '',
        'institute': group.institute or '',
        'program': group.program or '',
        'course': str(group.course) if group.course is not None else '',
    }


def _history_comment(before, after):
    """Строки «Институт: было → стало» только по изменившимся атрибутам."""
    lines = []
    for label, key in (('Институт', 'institute'), ('Направление', 'program'), ('Курс', 'course')):
        old = before[key] or '—'
        new = after[key] or '—'
        if old != new:
            lines.append(f'{label}: {old} → {new}')
    return '\n'.join(lines)


@receiver(pre_save, sender=Profile)
def log_group_change(sender, instance, raw=False, using='default', **kwargs):
    """Пишет историю при фактической смене группы профиля.

    Сравнение идёт с БД, а не с ``instance``: профиль могли загрузить раньше и
    группа в памяти уже устарела. Любые сбои (loaddata, недомигрированная БД)
    не должны ломать сохранение профиля — поэтому широкий try/except.
    """
    if raw or instance.pk is None:
        return

    update_fields = kwargs.get('update_fields')
    if update_fields is not None and 'group' not in update_fields:
        return

    try:
        old_row = (
            Profile.objects.using(using)
            .filter(pk=instance.pk)
            .values('group_id', 'group__code', 'group__institute', 'group__program', 'group__course')
            .first()
        )
        if old_row is None or old_row['group_id'] == instance.group_id:
            return

        from apps.education.models import Group

        new_group = (
            Group.objects.using(using).filter(pk=instance.group_id).first()
            if instance.group_id
            else None
        )
        before = _snapshot_from_row(old_row)
        after = _snapshot_from_group(new_group)
        ProfileHistory.objects.using(using).create(
            user_id=instance.user_id,
            kind=ProfileHistory.KIND_GROUP,
            from_value=before['code'],
            to_value=after['code'],
            comment=_history_comment(before, after),
        )
    except Exception:
        # История — вспомогательные данные: её отсутствие не повод падать
        # на регистрации, loaddata или тестах без миграций.
        pass
