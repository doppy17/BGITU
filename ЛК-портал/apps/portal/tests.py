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


class StubPagesTests(TestCase):
    """Заглушки новых разделов: доступны залогиненным, скрыты от анонимов."""

    def setUp(self):
        self.role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        self.user = get_user_model().objects.create_user('stubstudent', password='demo12345')
        self.user.profile.role = self.role
        self.user.profile.save()

    def test_stub_pages_render_for_user(self):
        self.client.force_login(self.user)
        for name, title in [('stipendia', 'Стипендия'), ('eos', 'ЭОС'), ('profile', 'Профиль')]:
            response = self.client.get(reverse(f'portal:{name}'))
            self.assertEqual(response.status_code, 200, name)
            self.assertContains(response, title)

    def test_stub_pages_redirect_anonymous(self):
        for name in ('stipendia', 'eos', 'profile'):
            response = self.client.get(reverse(f'portal:{name}'))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn('/login/', response.url, name)


class NavCompositionTests(TestCase):
    """Состав левого навбара: 7 разделов, «Студенты» только с правом."""

    def setUp(self):
        self.student_role = Role.objects.create(code='student', name='Студент', can_upload_works=True)
        self.dev_role = Role.objects.create(code='developer', name='Разработчик', is_developer=True,
                                            can_manage_users=True, can_manage_roles=True,
                                            can_approve_roles=True, can_manage_schedule=True)
        self.student = get_user_model().objects.create_user('navstudent', password='demo12345')
        self.student.profile.role = self.student_role
        self.student.profile.save()
        self.dev = get_user_model().objects.create_user('navdev', password='demo12345')
        self.dev.profile.role = self.dev_role
        self.dev.profile.save()

    def test_developer_sees_all_seven_sections(self):
        self.client.force_login(self.dev)
        response = self.client.get(reverse('portal:dashboard'))
        content = response.content.decode()
        for label in ('Обзор', 'Расписание', 'Стипендия', 'ЭОС', 'Роли', 'Профиль', 'Студенты'):
            self.assertContains(response, f'>{label}</a>', html=False)

    def test_nav_is_flat_single_list(self):
        """Единый список без подзаголовка «управление» и разделителей."""
        self.client.force_login(self.dev)
        response = self.client.get(reverse('portal:dashboard'))
        self.assertNotContains(response, 'cab-nav__label')
        self.assertNotContains(response, 'cab-nav__sep')

    def test_top_nav_has_main_site_links(self):
        self.client.force_login(self.dev)
        response = self.client.get(reverse('portal:dashboard'))
        self.assertContains(response, 'class="site-nav"')
        for href, label in [
            ('university.html', 'Университет'),
            ('admission.html', 'Поступающим'),
            ('student.html', 'Студенту'),
            ('science.html', 'Наука'),
            ('news.html', 'Новости'),
            ('contacts.html', 'Контакты'),
        ]:
            self.assertContains(response, href)
            self.assertContains(response, f'>{label}</a>', html=False)

    def test_student_does_not_see_students_link(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('portal:dashboard'))
        content = response.content.decode()
        self.assertNotIn('>Студенты</a>', content)
        for label in ('Обзор', 'Расписание', 'Стипендия', 'ЭОС', 'Роли', 'Профиль'):
            self.assertIn(f'>{label}</a>', content)
