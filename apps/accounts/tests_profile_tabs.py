"""Тесты вкладок профиля (E4): «Доп. контакты» и «Безопасность».

Проверяем доступ, активное состояние вкладок, смену пароля штатным
``PasswordChangeView`` (валидаторы Django, сообщение об успехе, редирект)
и то, что чужой пароль через свою вкладку не меняется.
"""
import re

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone


def active_tab_href(html):
    """href ссылки вкладки с классом is-active — для проверки подсветки."""
    match = re.search(r'<a class="profile-tabs__link is-active"[^>]*href="([^"]+)"', html)
    return match.group(1) if match else None


class ProfileTabsBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            'tabs-student', password='demo12345', email='tabs@example.ru',
            first_name='Иван', last_name='Тестов',
        )
        cls.profile = cls.user.profile
        cls.profile.phone = '+7 900 000-11-22'
        cls.profile.department = 'Информационных технологий'
        cls.profile.save()
        cls.other = User.objects.create_user('tabs-other', password='demo12345')

    def setUp(self):
        self.client.force_login(self.user)


class ProfileTabsPagesTests(ProfileTabsBase):
    def test_all_three_tabs_open(self):
        for name in ('portal:profile', 'accounts:profile_contacts', 'accounts:profile_security'):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'profile-tabs', status_code=200)

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        for name in ('accounts:profile_contacts', 'accounts:profile_security'):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('accounts:login'), response['Location'])

    def test_profile_page_has_no_tabs(self):
        """Вкладок на странице профиля быть не должно: блоки 01–03 идут подряд."""
        response = self.client.get(reverse('portal:profile'))
        content = response.content.decode()
        self.assertNotIn('profile-tabs__link', content)
        self.assertNotIn('Разделы профиля', content)
        self.assertLess(content.index('Данные студента'), content.index('Студенческий билет'))

    def test_active_tab_follows_current_page(self):
        cases = {
            'accounts:profile_edit': reverse('portal:profile'),
            'accounts:profile_contacts': reverse('accounts:profile_contacts'),
            'accounts:profile_security': reverse('accounts:profile_security'),
        }
        for name, expected_href in cases.items():
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                content = response.content.decode()
                self.assertEqual(active_tab_href(content), expected_href)
                # ровно одна активная вкладка (в nav кабинета aria-current свой)
                self.assertEqual(content.count('profile-tabs__link is-active'), 1)


class ProfileContactsTests(ProfileTabsBase):
    def test_contacts_show_phone_and_email(self):
        response = self.client.get(reverse('accounts:profile_contacts'))
        self.assertContains(response, '+7 900 000-11-22')
        self.assertContains(response, 'tabs@example.ru')
        self.assertContains(response, 'href="tel:+7900000-11-22"')
        self.assertContains(response, 'href="mailto:tabs@example.ru"')

    def test_contacts_point_to_edit_form(self):
        response = self.client.get(reverse('accounts:profile_contacts'))
        self.assertContains(response, reverse('accounts:profile_edit'))

    def test_contacts_tab_has_add_form(self):
        response = self.client.get(reverse('accounts:profile_contacts'))
        content = response.content.decode()
        self.assertIn('Добавить контакт', content)
        self.assertIn(reverse('accounts:contact_add'), content)

    def test_empty_contacts_have_placeholders(self):
        self.profile.phone = ''
        self.profile.save(update_fields=['phone'])
        self.user.email = ''
        self.user.save(update_fields=['email'])
        response = self.client.get(reverse('accounts:profile_contacts'))
        self.assertContains(response, 'не указан')
        self.assertContains(response, 'не указана')


class ProfileSecurityTests(ProfileTabsBase):
    url_name = 'accounts:profile_security'

    def test_page_has_password_form_and_last_login(self):
        self.user.refresh_from_db()
        response = self.client.get(reverse(self.url_name))
        content = response.content.decode()
        for field_name in ('old_password', 'new_password1', 'new_password2'):
            self.assertIn(f'name="{field_name}"', content)
        self.assertIn('Последний вход', content)
        self.assertIn(timezone.localdate().strftime('%d.%m.%Y'), content)
        self.assertIn('class="input"', content)
        self.assertEqual(active_tab_href(content), reverse(self.url_name))

    def test_password_change_success_redirects_to_profile(self):
        response = self.client.post(reverse(self.url_name), {
            'old_password': 'demo12345',
            'new_password1': 'Zarnitsa-2026!',
            'new_password2': 'Zarnitsa-2026!',
        }, follow=True)
        self.assertRedirects(response, reverse('portal:profile'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Zarnitsa-2026!'))
        self.assertContains(response, 'Пароль изменён')
        # сессия после смены пароля остаётся рабочей
        self.assertEqual(self.client.get(reverse('portal:profile')).status_code, 200)

    def test_new_password_can_be_used_to_log_in(self):
        self.client.post(reverse(self.url_name), {
            'old_password': 'demo12345',
            'new_password1': 'Zarnitsa-2026!',
            'new_password2': 'Zarnitsa-2026!',
        })
        self.client.logout()
        self.assertTrue(self.client.login(username='tabs-student', password='Zarnitsa-2026!'))
        self.assertFalse(self.client.login(username='tabs-student', password='demo12345'))

    def test_wrong_current_password_shows_error(self):
        response = self.client.post(reverse(self.url_name), {
            'old_password': 'not-my-password',
            'new_password1': 'Zarnitsa-2026!',
            'new_password2': 'Zarnitsa-2026!',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'неправильно')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('demo12345'))

    def test_weak_new_password_is_rejected_by_validators(self):
        response = self.client.post(reverse(self.url_name), {
            'old_password': 'demo12345',
            'new_password1': '12345678',
            'new_password2': '12345678',
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('demo12345'))
        self.assertContains(response, 'цифр')

    def test_mismatched_repeat_is_rejected(self):
        response = self.client.post(reverse(self.url_name), {
            'old_password': 'demo12345',
            'new_password1': 'Zarnitsa-2026!',
            'new_password2': 'Zarnitsa-2027!',
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('demo12345'))

    def test_other_users_password_is_untouched(self):
        self.client.logout()
        self.client.force_login(self.other)
        response = self.client.post(reverse(self.url_name), {
            'old_password': 'demo12345',
            'new_password1': 'Chuzhoy-2026!',
            'new_password2': 'Chuzhoy-2026!',
        })
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.other.refresh_from_db()
        self.assertTrue(self.user.check_password('demo12345'))
        self.assertTrue(self.other.check_password('Chuzhoy-2026!'))

    def test_anonymous_cannot_change_password(self):
        self.client.logout()
        response = self.client.post(reverse(self.url_name), {
            'old_password': 'demo12345',
            'new_password1': 'Zarnitsa-2026!',
            'new_password2': 'Zarnitsa-2026!',
        })
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('demo12345'))


class ProfileLastLoginTests(ProfileTabsBase):
    def test_last_login_is_rendered_from_user(self):
        self.user.last_login = timezone.now()
        self.user.save(update_fields=['last_login'])
        response = self.client.get(reverse('accounts:profile_security'))
        expected = timezone.localtime(self.user.last_login).strftime('%d.%m.%Y')
        self.assertContains(response, expected)
        self.assertContains(response, timezone.localtime(self.user.last_login).strftime('%H:%M'))
