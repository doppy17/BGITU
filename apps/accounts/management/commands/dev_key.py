"""Одноразовые ключи доступа разработчика (временная функция).

Код печатается один раз и дублируется в файл ``DEV_ACCESS_KEYS_FILE``
(по умолчанию ``~/.hermes/status/bgitu-dev-key.txt``, права 600).
"""
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import AccessKey, Role

DEFAULT_STATUS_FILE = '~/.hermes/status/bgitu-dev-key.txt'


def status_path():
    return Path(os.path.expanduser(getattr(settings, 'DEV_ACCESS_KEYS_FILE', DEFAULT_STATUS_FILE)))


def write_code_file(path, code):
    """Пишет код в файл, доступный только владельцу (600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (code + '\n').encode('utf-8'))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


class Command(BaseCommand):
    help = 'Создаёт одноразовый ключ доступа разработчика и показывает код один раз.'

    def add_arguments(self, parser):
        parser.add_argument('--role', default='developer', help='Код роли (по умолчанию developer).')
        parser.add_argument(
            '--days', type=int, default=None,
            help='Срок действия в днях; 0 — без срока (по умолчанию DEV_ACCESS_KEYS_TTL_DAYS).',
        )
        parser.add_argument('--list', action='store_true', dest='list_keys', help='Показать выпущенные ключи.')
        parser.add_argument('--revoke', type=int, metavar='ID', help='Отозвать ключ по id.')

    def handle(self, *args, **options):
        if options['list_keys'] and options['revoke'] is not None:
            raise CommandError('Укажите что-то одно: --list или --revoke.')
        if options['list_keys']:
            return self.show_list()
        if options['revoke'] is not None:
            return self.revoke(options['revoke'])
        return self.create(options['role'], options['days'])

    def create(self, role_code, days):
        if not getattr(settings, 'DEV_ACCESS_KEYS', False):
            raise CommandError('Ключи доступа выключены: DEV_ACCESS_KEYS=false (BGITU_DEV_KEYS=0).')

        role = Role.objects.filter(code=role_code).first()
        if role is None:
            codes = ', '.join(Role.objects.order_by('code').values_list('code', flat=True)) or 'ролей нет'
            raise CommandError(f'Роль «{role_code}» не найдена. Доступные коды: {codes}.')

        if days is None:
            days = int(getattr(settings, 'DEV_ACCESS_KEYS_TTL_DAYS', 14))
        if days < 0:
            raise CommandError('--days не может быть отрицательным (0 — без срока).')

        key, code = AccessKey.issue(role=role, days=days)
        path = status_path()
        write_code_file(path, code)

        expires = 'без срока' if not key.expires_at else timezone.localtime(key.expires_at).strftime('%d.%m.%Y %H:%M')
        self.stdout.write(self.style.SUCCESS(f'Ключ #{key.pk} создан: роль {role.code}, действует до {expires}.'))
        self.stdout.write('Код (показывается один раз):')
        self.stdout.write(code)
        self.stdout.write(f'Сохранён в {path} (права 600).')

    def show_list(self):
        keys = list(AccessKey.objects.select_related('role', 'used_by').order_by('pk'))
        if not keys:
            self.stdout.write('Ключей нет.')
            return
        self.stdout.write(f'{"id":>4}  {"роль":<12}  {"создан":<16}  {"действует до":<16}  статус')
        for key in keys:
            created = timezone.localtime(key.created_at).strftime('%d.%m.%Y %H:%M')
            expires = (
                timezone.localtime(key.expires_at).strftime('%d.%m.%Y %H:%M')
                if key.expires_at else 'без срока'
            )
            status = key.status_label
            if key.used_by_id:
                status += f' · {key.used_by}'
            self.stdout.write(f'{key.pk:>4}  {key.role.code:<12}  {created:<16}  {expires:<16}  {status}')

    def revoke(self, key_id):
        key = AccessKey.objects.filter(pk=key_id).first()
        if key is None:
            raise CommandError(f'Ключ #{key_id} не найден.')
        if not key.is_active:
            self.stdout.write(f'Ключ #{key.pk} уже неактивен ({key.status_label}).')
            return
        key.is_active = False
        key.save(update_fields=['is_active'])
        self.stdout.write(self.style.SUCCESS(f'Ключ #{key.pk} отозван.'))
