from django.test import TestCase
from django.urls import reverse

from .models import Institute, News


class PublicSiteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = Institute.objects.create(
            name='Инженерно-экономический институт', short_name='ИЭИ', slug='iei', description='ИТ и экономика'
        )
        cls.news = News.objects.create(
            title='Новость дня', slug='news-day', category='university',
            preview_text='Анонс новости', body='Текст новости',
        )

    def test_index_renders(self):
        response = self.client.get(reverse('main:index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'БГИТУ')
        self.assertContains(response, 'Новость дня')
        self.assertContains(response, 'ИЭИ')

    def test_news_list_and_detail(self):
        self.assertEqual(self.client.get(reverse('main:news_list')).status_code, 200)
        response = self.client.get(reverse('main:news_detail', args=['news-day']))
        self.assertContains(response, 'Текст новости')

    def test_institute_and_admission(self):
        self.assertEqual(self.client.get(reverse('main:institute_detail', args=['iei'])).status_code, 200)
        self.assertEqual(self.client.get(reverse('main:admission')).status_code, 200)

    def test_shared_header_and_footer(self):
        response = self.client.get(reverse('main:index'))
        self.assertContains(response, 'site-head')       # общий верхний навбар
        self.assertContains(response, 'head-search')     # поиск в навбаре
        self.assertContains(response, 'footer__word')    # общий подвал с «БГИТУ»
        self.assertContains(response, 'trail.js')        # фон-след
