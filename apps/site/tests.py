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


class SearchTests(TestCase):
    """Поиск ищет и по сайту, и по разделам кабинета."""

    def test_site_page_found(self):
        response = self.client.get(reverse('site:search'), {'q': 'расписание'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Расписание')
        self.assertContains(response, reverse('site:schedule'))

    def test_cabinet_section_found(self):
        response = self.client.get(reverse('site:search'), {'q': 'стипендия'})
        self.assertContains(response, reverse('portal:stipendia'))
        self.assertContains(response, 'кабинет')

    def test_empty_query_shows_hints(self):
        response = self.client.get(reverse('site:search'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'часто ищут')

    def test_no_results_message(self):
        response = self.client.get(reverse('site:search'), {'q': 'ксерокопия'})
        self.assertContains(response, 'Ничего не нашлось')

    def test_search_dialog_present_everywhere(self):
        """Диалог поиска в стиле макета есть и на сайте, и в кабинете."""
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user('searcher', password='demo12345')
        self.client.force_login(user)
        for url in ('/', '/news/', reverse('portal:dashboard')):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'id="search"')
                self.assertContains(response, 'data-search-open')
                self.assertContains(response, 'id="bgitu-index"')

    def test_search_index_includes_cabinet(self):
        import json
        import re

        response = self.client.get('/')
        match = re.search(r'id="bgitu-index" type="application/json">(.*?)</script>', response.content.decode(), re.S)
        self.assertIsNotNone(match)
        index = json.loads(match.group(1))
        urls = {item['url'] for item in index}
        self.assertIn(reverse('portal:stipendia'), urls)
        self.assertIn(reverse('schedule:schedule'), urls)
        self.assertIn(reverse('site:news'), urls)
        self.assertTrue(any(item['category'] == 'Кабинет' for item in index))


class NoOldSiteLinksTests(TestCase):
    """Страницы не отправляют пользователя на старый сайт в разделах, которые уже есть у нас."""

    def test_no_old_schedule_links(self):
        response = self.client.get(reverse('site:schedule'))
        self.assertNotContains(response, 'bgitu.ru/studentu/raspisanie')

    def test_old_news_links_point_inside(self):
        response = self.client.get(reverse('site:news'))
        self.assertNotContains(response, 'bgitu.ru/news/index.php')
