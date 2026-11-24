"""Тесты ролей, заявок и управления пользователями."""
import hashlib
import inspect
import re
import stat
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models.signals import post_save
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, clear_url_caches, path, reverse
from django.utils import timezone

from apps.education.models import Group

from .models import AccessKey, Profile, Role, RoleRequest
from .signals import create_profile


class RoleFlowTests(TestCase):
    def setUp(self):
        self.student_role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        self.teacher_role = Role.objects.create(code='teacher', name='Преподаватель', can_create_courses=True, can_grade=True)
        self.dean_role = Role.objects.create(code='dean', name='Деканат', can_manage_users=True, can_approve_roles=True)
        self.admin_role = Role.objects.create(
            code='admin', name='Администратор', can_manage_users=True, can_manage_roles=True, can_approve_roles=True
        )
        self.dev_role = Role.objects.create(code='developer', name='Разработчик', can_manage_roles=True, is_developer=True)
        self.group = Group.objects.create(code='ИСТ-201', course=2)

        self.student = self.make_user('student', self.student_role, self.group)
        self.teacher = self.make_user('teacher', self.teacher_role)
        self.dean = self.make_user('dean', self.dean_role)
        self.admin = self.make_user('admin', self.admin_role)
        self.developer = self.make_user('developer', self.dev_role)

    def make_user(self, username, role, group=None):
        user = User.objects.create_user(username=username, password='demo12345', first_name='Тест', last_name=username)
        profile = user.profile
        profile.role = role
        profile.group = group
        profile.save()
        return user

    def test_student_requests_role_and_dean_approves(self):
        self.client.force_login(self.student)
        response = self.client.post(
            reverse('accounts:role_request_create', args=[self.teacher_role.id]),
            {'comment': 'Веду практику'},
        )
        self.assertRedirects(response, reverse('accounts:roles'))
        request_obj = RoleRequest.objects.get(user=self.student, role=self.teacher_role)
        self.assertEqual(request_obj.status, RoleRequest.STATUS_PENDING)

        self.client.force_login(self.dean)
        response = self.client.post(
            reverse('accounts:role_request_review', args=[request_obj.id, 'approve']),
            {'review_comment': 'Согласовано'},
        )
        self.assertRedirects(response, reverse('accounts:role_requests'))
        request_obj.refresh_from_db()
        self.student.profile.refresh_from_db()
        self.assertEqual(request_obj.status, RoleRequest.STATUS_APPROVED)
        self.assertEqual(self.student.profile.role, self.teacher_role)

    def test_developer_role_requires_developer_approval(self):
        RoleRequest.objects.create(user=self.student, role=self.dev_role)
        request_obj = RoleRequest.objects.get(user=self.student, role=self.dev_role)

        self.client.force_login(self.dean)
        self.client.post(reverse('accounts:role_request_review', args=[request_obj.id, 'approve']))
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, RoleRequest.STATUS_PENDING)

        self.client.force_login(self.developer)
        self.client.post(reverse('accounts:role_request_review', args=[request_obj.id, 'approve']))
        request_obj.refresh_from_db()
        self.student.profile.refresh_from_db()
        self.assertEqual(request_obj.status, RoleRequest.STATUS_APPROVED)
        self.assertEqual(self.student.profile.role, self.dev_role)

    def test_role_creation_permission_and_developer_flag(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse('accounts:role_create')).status_code, 403)

        self.client.force_login(self.admin)
        response = self.client.post(reverse('accounts:role_create'), {
            'name': 'Методист', 'code': 'methodist', 'description': 'Планы',
            'can_create_courses': 'on', 'can_grade': 'on',
        })
        self.assertRedirects(response, reverse('accounts:roles'))
        self.assertTrue(Role.objects.filter(code='methodist').exists())

        response = self.client.post(reverse('accounts:role_create'), {
            'name': 'Полный', 'code': 'fullaccess', 'is_developer': 'on',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Role.objects.filter(code='fullaccess').exists())

    def test_only_developer_can_assign_developer_role(self):
        group = Group.objects.create(code='ИСТ-101', course=1)
        self.client.force_login(self.dean)
        response = self.client.post(
            reverse('accounts:student_update', args=[self.student.id]),
            {'role': self.dev_role.id, 'group': group.id, 'record_book': 'ИСТ-101/01'},
        )
        self.assertRedirects(response, reverse('accounts:students'))
        self.student.profile.refresh_from_db()
        self.assertEqual(self.student.profile.role, self.student_role)

        self.client.force_login(self.developer)
        self.client.post(
            reverse('accounts:student_update', args=[self.student.id]),
            {'role': self.student_role.id, 'group': group.id, 'record_book': 'ИСТ-101/01'},
        )
        self.student.profile.refresh_from_db()
        self.assertEqual(self.student.profile.group, group)

    def test_student_cannot_open_management_pages(self):
        self.client.force_login(self.student)
        for name in ('accounts:students', 'accounts:role_create', 'accounts:role_requests'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403 if name != 'accounts:role_requests' else 200)


class RegistrationTests(TestCase):
    def setUp(self):
        self.role = Role.objects.create(code='student', name='Студент', is_system=True, can_upload_works=True)
        self.group = Group.objects.create(code='ИСТ-201', course=2)
        self.payload = {
            'last_name': 'Портнов',
            'first_name': 'Георгий',
            'email': 'newstudent@example.ru',
            'password1': 'Bgu2026!study',
            'password2': 'Bgu2026!study',
        }

    def test_register_creates_profile_and_logs_in(self):
        response = self.client.post(reverse('accounts:register'), self.payload)
        self.assertRedirects(response, reverse('portal:dashboard'))
        user = User.objects.get(email='newstudent@example.ru')
        self.assertEqual(user.username, 'newstudent@example.ru')
        self.assertEqual(user.profile.role, self.role)
        self.assertIsNone(user.profile.group)
        self.assertEqual(self.client.session['_auth_user_id'], str(user.pk))
        self.assertContains(self.client.get(reverse('portal:dashboard')), 'Раздел в разработке')

    def test_register_ignores_posted_group(self):
        """Группа на регистрации не указывается: присланный group игнорируется."""
        self.payload['group'] = str(self.group.id)
        response = self.client.post(reverse('accounts:register'), self.payload)
        self.assertRedirects(response, reverse('portal:dashboard'))
        user = User.objects.get(email='newstudent@example.ru')
        self.assertIsNone(user.profile.group)

    def test_register_ignores_invalid_group(self):
        """Даже мусор в group не мешает регистрации — это не ошибка формы."""
        self.payload['group'] = 'not-a-group'
        response = self.client.post(reverse('accounts:register'), self.payload)
        self.assertRedirects(response, reverse('portal:dashboard'))
        self.assertIsNone(User.objects.get(email='newstudent@example.ru').profile.group)

    def test_register_page_has_no_group_field(self):
        response = self.client.get(reverse('accounts:register'))
        self.assertNotContains(response, 'name="group"')
        self.assertNotContains(response, 'id="group"')

    def test_register_ignores_posted_username(self):
        self.payload['username'] = 'hacker-name'
        self.client.post(reverse('accounts:register'), self.payload)
        user = User.objects.get(email='newstudent@example.ru')
        self.assertEqual(user.username, 'newstudent@example.ru')

    def test_register_rejects_duplicate_email(self):
        User.objects.create_user('legacy-login', email='newstudent@example.ru', password='Bgu2026!study')
        response = self.client.post(reverse('accounts:register'), self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'уже зарегистрирован')
        self.assertEqual(User.objects.filter(email='newstudent@example.ru').count(), 1)

    def test_register_rejects_password_mismatch(self):
        self.payload['password2'] = 'Other2026!study'
        response = self.client.post(reverse('accounts:register'), self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'не совпадают')
        self.assertFalse(User.objects.filter(email='newstudent@example.ru').exists())


class LoginTests(TestCase):
    """Вход по почте; вход по старому логину остаётся совместимым."""

    def setUp(self):
        self.user = User.objects.create_user(
            'legacy-login', email='student@example.ru', password='Bgu2026!study'
        )

    def test_login_by_email(self):
        response = self.client.post(reverse('accounts:login'), {
            'username': 'student@example.ru',
            'password': 'Bgu2026!study',
        })
        self.assertRedirects(response, '/cabinet/')

    def test_login_by_email_case_insensitive(self):
        response = self.client.post(reverse('accounts:login'), {
            'username': 'STUDENT@example.ru',
            'password': 'Bgu2026!study',
        })
        self.assertRedirects(response, '/cabinet/')

    def test_login_by_legacy_username(self):
        response = self.client.post(reverse('accounts:login'), {
            'username': 'legacy-login',
            'password': 'Bgu2026!study',
        })
        self.assertRedirects(response, '/cabinet/')

    def test_login_wrong_password_shows_error(self):
        response = self.client.post(reverse('accounts:login'), {
            'username': 'student@example.ru',
            'password': 'wrong-pass-2026',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Неверная почта/логин или пароль')


class ThemePreferenceTests(TestCase):
    """Тема сохраняется за пользователем и переживает перезаход/выход."""

    @classmethod
    def setUpTestData(cls):
        from apps.accounts.models import Role

        cls.role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        cls.user = User.objects.create_user('thememan', password='demo12345')
        cls.user.profile.role = cls.role
        cls.user.profile.save()

    def test_theme_settings_endpoint_saves_to_profile(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('accounts:theme_set'), {'theme': 'latun'})
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {'ok': True, 'theme': 'latun'})
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.theme, 'latun')
        self.assertEqual(response.cookies['bgitu-theme'].value, 'latun')

    def test_theme_settings_accepts_brass(self):
        """Новая светлая тема «Бронза» сохраняется в профиле и cookie."""
        self.client.force_login(self.user)
        response = self.client.post(reverse('accounts:theme_set'), {'theme': 'brass'})
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {'ok': True, 'theme': 'brass'})
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.theme, 'brass')
        self.assertEqual(response.cookies['bgitu-theme'].value, 'brass')

    def test_theme_rejects_unknown_value(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('accounts:theme_set'), {'theme': 'pink'})
        self.assertEqual(response.status_code, 400)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.theme, '')

    def test_theme_brass_renders_on_login(self):
        """Тема brass рендерится на <html> и есть в обеих панелях выбора."""
        self.client.cookies['bgitu-theme'] = 'brass'
        response = self.client.get(reverse('accounts:login'))
        self.assertContains(response, 'data-theme="brass"')
        content = response.content.decode()
        # мобильное меню + попап «Тема» — кнопка должна быть в обоих блоках
        self.assertEqual(content.count('data-theme-set="brass"'), 2)

    def test_theme_survives_new_session(self):
        """После выхода и нового входа страница рендерится с сохранённой темой."""
        self.client.force_login(self.user)
        self.client.post(reverse('accounts:theme_set'), {'theme': 'surik'})
        self.client.logout()
        self.client.force_login(self.user)
        response = self.client.get(reverse('portal:dashboard'))
        self.assertContains(response, 'data-theme="surik"')

    def test_anonymous_uses_cookie(self):
        self.client.cookies['bgitu-theme'] = 'latun'
        response = self.client.get(reverse('accounts:login'))
        self.assertContains(response, 'data-theme="latun"')

    def test_anonymous_default_is_paper(self):
        response = self.client.get(reverse('accounts:login'))
        self.assertContains(response, 'data-theme="paper"')


class RegistrationProfileTests(TestCase):
    """Профиль создаётся всегда, ФИО необязательны, сообщение понятное."""

    def setUp(self):
        self.payload = {
            'last_name': 'Портнов',
            'first_name': 'Георгий',
            'email': 'profile-check@example.ru',
            'password1': 'Bgu2026!study',
            'password2': 'Bgu2026!study',
        }

    def test_profile_created_even_without_signal(self):
        """Даже если сигнал не сработал, регистрация создаёт Profile."""
        post_save.disconnect(create_profile, sender=User)
        self.addCleanup(post_save.connect, create_profile, sender=User)

        response = self.client.post(reverse('accounts:register'), self.payload)

        self.assertRedirects(response, reverse('portal:dashboard'))
        user = User.objects.get(email=self.payload['email'])
        self.assertTrue(Profile.objects.filter(user=user).exists())

    def test_names_are_optional(self):
        self.payload.pop('last_name')
        self.payload.pop('first_name')

        response = self.client.post(reverse('accounts:register'), self.payload)

        self.assertRedirects(response, reverse('portal:dashboard'))
        user = User.objects.get(email=self.payload['email'])
        self.assertEqual(user.first_name, '')
        self.assertEqual(user.last_name, '')
        self.assertTrue(hasattr(user, 'profile'))

    def test_success_message_and_cabinet_redirect(self):
        response = self.client.post(reverse('accounts:register'), self.payload, follow=True)

        self.assertRedirects(response, reverse('portal:dashboard'))
        self.assertContains(response, 'Аккаунт создан')


class RegistrationWithoutRolesTests(TestCase):
    """Если базовой роли student нет, регистрация не падает и оставляет role=None."""

    def test_register_without_student_role(self):
        Role.objects.all().delete()
        response = self.client.post(reverse('accounts:register'), {
            'email': 'no-roles@example.ru',
            'password1': 'Bgu2026!study',
            'password2': 'Bgu2026!study',
        })

        self.assertRedirects(response, '/cabinet/')
        user = User.objects.get(email='no-roles@example.ru')
        self.assertIsNone(user.profile.role)


class AuthPageTests(TestCase):
    """На странице входа не должно остаться демо-подсказок."""

    def test_login_page_has_no_demo_block(self):
        response = self.client.get(reverse('accounts:login'))
        content = response.content.decode()
        for marker in ('auth-demo', 'демо-доступы', 'demo12345'):
            self.assertNotIn(marker, content)


class DevKeyLinkTests(TestCase):
    """Ссылка «Ввести ключ доступа» управляется флагом из контекст-процессора."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._stub = None
        try:
            reverse('accounts:dev_key')
        except NoReverseMatch:
            # Страница /access/ подключается отдельным модулем; в тестах
            # подставляем заглушку, чтобы проверить рендер ссылки.
            from apps.accounts import urls as accounts_urls

            cls._stub = path('access/', lambda request: HttpResponse('ключ доступа'), name='dev_key')
            accounts_urls.urlpatterns.append(cls._stub)
            clear_url_caches()

    @classmethod
    def tearDownClass(cls):
        if cls._stub is not None:
            from apps.accounts import urls as accounts_urls

            if cls._stub in accounts_urls.urlpatterns:
                accounts_urls.urlpatterns.remove(cls._stub)
            clear_url_caches()
        super().tearDownClass()

    @override_settings(DEV_ACCESS_KEYS={'test-key': 'Студент'})
    def test_link_visible_when_enabled(self):
        for url_name in ('login', 'register'):
            response = self.client.get(reverse(f'accounts:{url_name}'))
            self.assertContains(response, 'Ввести ключ доступа')
            self.assertContains(response, 'href="/access/"')

    @override_settings(DEV_ACCESS_KEYS=None)
    def test_link_hidden_when_disabled(self):
        for url_name in ('login', 'register'):
            response = self.client.get(reverse(f'accounts:{url_name}'))
            self.assertNotContains(response, 'Ввести ключ доступа')

    def test_processor_tolerates_missing_setting(self):
        from . import context_processors

        with mock.patch.object(context_processors, 'settings', SimpleNamespace()):
            self.assertEqual(context_processors.dev_access(None), {'dev_keys_enabled': False})


class AccessKeyTests(TestCase):
    """Одноразовые ключи доступа: выдача роли, срок, флаг, хранение только хеша."""

    def setUp(self):
        cache.clear()
        self.dev_role = Role.objects.create(code='developer', name='Разработчик', is_developer=True)
        self.student_role = Role.objects.create(code='student', name='Студент')
        self.user = User.objects.create_user(username='key-user', password='Bgu2026!study')
        self.user.profile.role = self.student_role
        self.user.profile.save(update_fields=['role'])
        self.client.force_login(self.user)
        self.url = reverse('accounts:dev_key')

    def tearDown(self):
        cache.clear()

    def test_valid_key_grants_developer_role(self):
        key, raw = AccessKey.issue(role=self.dev_role, days=1)

        response = self.client.post(self.url, {'code': raw}, follow=True)

        self.assertContains(response, 'Доступ разработчика выдан')
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.role, self.dev_role)
        self.assertTrue(self.user.profile.role.is_developer)
        key.refresh_from_db()
        self.assertEqual(key.used_by, self.user)
        self.assertIsNotNone(key.used_at)
        self.assertFalse(key.is_active)

    def test_key_is_one_time_for_another_user(self):
        key, raw = AccessKey.issue(role=self.dev_role)
        self.assertRedirects(self.client.post(self.url, {'code': raw}), self.url)

        other = User.objects.create_user(username='key-other', password='Bgu2026!study')
        other.profile.role = self.student_role
        other.profile.save(update_fields=['role'])
        self.client.force_login(other)

        response = self.client.post(self.url, {'code': raw})

        self.assertContains(response, 'Ключ уже использован')
        other.profile.refresh_from_db()
        self.assertEqual(other.profile.role, self.student_role)
        self.assertFalse(other.profile.role.is_developer)

    def test_expired_key_is_rejected(self):
        key, raw = AccessKey.issue(role=self.dev_role, days=1)
        AccessKey.objects.filter(pk=key.pk).update(expires_at=timezone.now() - timedelta(minutes=1))

        response = self.client.post(self.url, {'code': raw})

        self.assertContains(response, 'Срок действия ключа истёк')
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.role, self.student_role)
        key.refresh_from_db()
        self.assertFalse(key.is_used)

    @override_settings(DEV_ACCESS_KEYS=False)
    def test_disabled_flag_returns_404(self):
        key, raw = AccessKey.issue(role=self.dev_role)

        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, {'code': raw}).status_code, 404)

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.role, self.student_role)

    def test_check_method_verifies_code_and_keeps_model_check(self):
        key, raw = AccessKey.issue(role=self.dev_role)

        self.assertTrue(key.check(raw))
        self.assertFalse(key.check('wrong-code'))
        self.assertTrue(inspect.ismethod(AccessKey.check))

    def test_raw_code_is_never_stored(self):
        key, raw = AccessKey.issue(role=self.dev_role)

        self.assertEqual(len(raw), 32)
        self.assertEqual(key.code_hash, hashlib.sha256(raw.encode('utf-8')).hexdigest())
        self.assertFalse(AccessKey.objects.filter(code_hash=raw).exists())
        stored = [str(value) for row in AccessKey.objects.values() for value in row.values()]
        self.assertNotIn(raw, stored)
        self.assertNotIn(raw, str(key))

    def test_wrong_code_is_generic_and_rate_limited(self):
        for _ in range(5):
            response = self.client.post(self.url, {'code': 'wrong-code'})
            self.assertContains(response, 'Ключ не подошёл')

        key, raw = AccessKey.issue(role=self.dev_role)
        response = self.client.post(self.url, {'code': raw})

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, 'Слишком много попыток', status_code=429)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.role, self.student_role)
        key.refresh_from_db()
        self.assertFalse(key.is_used)

    def test_command_creates_key_and_writes_code_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bgitu-dev-key.txt'
            out = StringIO()
            with override_settings(DEV_ACCESS_KEYS_FILE=str(path)):
                call_command('dev_key', '--days', '3', stdout=out)

            lines = [line.strip() for line in re.sub(r'\x1b\[[0-9;]*m', '', out.getvalue()).splitlines()]
            code = next(line for line in lines if re.fullmatch(r'[A-Za-z0-9_-]{32}', line))

            key = AccessKey.objects.get()
            self.assertEqual(len(code), 32)
            self.assertEqual(key.role, self.dev_role)
            self.assertEqual(key.code_hash, AccessKey.hash_code(code))
            self.assertIsNotNone(key.expires_at)
            self.assertEqual(path.read_text().strip(), code)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertFalse(AccessKey.objects.filter(code_hash=code).exists())

    def test_command_list_and_revoke(self):
        key, _ = AccessKey.issue(role=self.dev_role)

        out = StringIO()
        call_command('dev_key', '--list', stdout=out)
        listing = out.getvalue()
        self.assertIn(str(key.pk), listing)
        self.assertIn('developer', listing)
        self.assertIn('активен', listing)

        out = StringIO()
        call_command('dev_key', '--revoke', str(key.pk), stdout=out)
        key.refresh_from_db()
        self.assertFalse(key.is_active)
        self.assertIn('отозван', out.getvalue())

    @override_settings(DEV_ACCESS_KEYS=False)
    def test_command_refuses_when_disabled(self):
        with self.assertRaises(CommandError):
            call_command('dev_key', stdout=StringIO())
        self.assertFalse(AccessKey.objects.exists())
