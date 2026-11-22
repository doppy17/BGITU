from django.test import TestCase
from django.urls import reverse

from apps.site.pages import PAGES


class PublicSiteTests(TestCase):
    """Публичная часть: все страницы реестра открываются, ссылка на ЛК ведёт в кабинет."""

    def test_all_pages_render(self):
        for page in PAGES:
            with self.subTest(page=page.name):
                response = self.client.get(reverse(f'site:{page.name}'))
                self.assertEqual(response.status_code, 200)

    def test_index_is_root(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Личный кабинет')

    def test_cabinet_button_leads_to_portal(self):
        response = self.client.get('/')
        self.assertContains(response, reverse('portal:dashboard'))

    def test_schedule_page_is_timetable(self):
        self.assertEqual(self.client.get('/timetable/').status_code, 200)

    def test_active_nav_item(self):
        response = self.client.get(reverse('site:news'))
        self.assertContains(response, 'aria-current="page"')
