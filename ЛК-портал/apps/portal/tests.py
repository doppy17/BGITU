from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Role


class ThemeUITests(TestCase):
    """Переключатель тем (paper/surik/latun) — как на основном сайте bgitu."""

    @classmethod
    def setUpTestData(cls):
        cls.role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        cls.user = get_user_model().objects.create_user('tstudent', password='demo12345')
        cls.user.profile.role = cls.role
        cls.user.profile.save()

    def test_login_page_has_theme_popover(self):
        response = self.client.get(reverse('accounts:login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-theme="paper"')
        self.assertContains(response, 'id="theme-pop"')
        for name in ('paper', 'surik', 'latun'):
            self.assertContains(response, f'data-theme-set="{name}"')
        self.assertContains(response, 'js/theme.js')

    def test_dashboard_has_theme_button(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('portal:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="theme-btn"')
        self.assertContains(response, 'popovertarget="theme-pop"')
        self.assertContains(response, 'id="theme-pop"')
