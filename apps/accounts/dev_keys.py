"""Страница ввода одноразового ключа доступа разработчика.

Функция временная. Всё, что к ней относится, собрано в четырёх местах:
модель ``AccessKey`` (models.py), этот модуль, команда ``dev_key`` и шаблон
``accounts/access_key.html``. Чтобы убрать функцию, достаточно выключить
``DEV_ACCESS_KEYS`` (``BGITU_DEV_KEYS=0``) или удалить перечисленные файлы,
маршрут ``accounts:dev_key`` и модель с миграцией.
"""
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import AccessKey, Profile

# Не более 5 попыток в минуту на IP — простой счётчик в кеше.
RATE_LIMIT = 5
RATE_WINDOW = 60

MSG_GRANTED = 'Доступ разработчика выдан'
MSG_USED = 'Ключ уже использован'
MSG_INVALID = 'Ключ не подошёл.'
MSG_EXPIRED = 'Срок действия ключа истёк.'
MSG_REVOKED = 'Ключ отозван.'
MSG_RATE = 'Слишком много попыток. Попробуйте через минуту.'


def dev_keys_enabled(view):
    """Страница существует только при включённом флаге — иначе 404."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not getattr(settings, 'DEV_ACCESS_KEYS', False):
            raise Http404('Ключи доступа отключены')
        return view(request, *args, **kwargs)

    return wrapper


def _rate_limited(request):
    """Увеличивает счётчик попыток с IP; True — лимит уже исчерпан."""
    cache_key = f'bgitu:dev-key:{request.META.get("REMOTE_ADDR", "unknown")}'
    if cache.add(cache_key, 1, RATE_WINDOW):
        return False
    try:
        attempts = cache.incr(cache_key)
    except ValueError:  # ключ успел истечь между add и incr
        cache.set(cache_key, 1, RATE_WINDOW)
        return False
    return attempts > RATE_LIMIT


@dev_keys_enabled
@login_required
def access_key_view(request):
    """Форма «Ключ доступа»: одноразовый код выдаёт роль с полным доступом."""
    if request.method != 'POST':
        return render(request, 'accounts/access_key.html')

    if _rate_limited(request):
        messages.error(request, MSG_RATE)
        return render(request, 'accounts/access_key.html', status=429)

    raw = (request.POST.get('code') or '').strip()
    if not raw:
        messages.error(request, 'Введите ключ доступа.')
        return render(request, 'accounts/access_key.html')

    # select_for_update: два параллельных запроса не используют ключ дважды.
    with transaction.atomic():
        key = (
            AccessKey.objects.select_for_update()
            .filter(code_hash=AccessKey.hash_code(raw))
            .first()
        )
        if key is None or not key.check(raw):
            if key is not None and key.is_used:
                messages.error(request, MSG_USED)
            elif key is not None and not key.is_active:
                messages.error(request, MSG_REVOKED)
            elif key is not None and key.is_expired:
                messages.error(request, MSG_EXPIRED)
            else:
                # Неверный код — без подсказок, существует он или нет.
                messages.error(request, MSG_INVALID)
            return render(request, 'accounts/access_key.html')

        # Условный UPDATE — страховка от гонки: ключ достанется только одному.
        claimed = (
            AccessKey.objects
            .filter(pk=key.pk, used_at__isnull=True, is_active=True)
            .update(used_by=request.user, used_at=timezone.now(), is_active=False)
        )
        if not claimed:
            messages.error(request, MSG_USED)
            return render(request, 'accounts/access_key.html')

        profile, _ = Profile.objects.get_or_create(user=request.user)
        profile.role = key.role
        profile.save(update_fields=['role'])
        # Роль разработчика даёт доступ и в Django-админку: включаем staff/superuser.
        if key.role and key.role.is_developer:
            user = request.user
            user.is_staff = True
            user.is_superuser = True
            user.save(update_fields=['is_staff', 'is_superuser'])

    messages.success(request, MSG_GRANTED)
    return redirect('accounts:dev_key')
