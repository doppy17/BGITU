"""Демо-данные публичной части: институты и новости (из проекта в Загрузках)."""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.main.models import Institute, News


class Command(BaseCommand):
    help = 'Заполняет институты и новости для публичной части сайта'

    def handle(self, *args, **options):
        Institute.objects.filter(slug__in=['iit', 'stroy', 'lesprom', 'econom']).delete()

        Institute.objects.get_or_create(
            slug='iei',
            defaults={
                'name': 'Инженерно-экономический институт',
                'short_name': 'ИЭИ',
                'description': 'Объединяет информационные технологии и экономику: '
                               'разработчики, специалисты по информационной безопасности, '
                               'экономисты и менеджеры.',
            },
        )
        Institute.objects.get_or_create(
            slug='li',
            defaults={
                'name': 'Институт лесного комплекса, ландшафтной архитектуры, транспорта и экологии',
                'short_name': 'ЛИ',
                'description': 'Лесное хозяйство, ландшафтная архитектура, транспорт и экология.',
            },
        )
        Institute.objects.get_or_create(
            slug='si',
            defaults={
                'name': 'Строительный институт',
                'short_name': 'СИ',
                'description': 'Строительство и архитектура: подготовка инженеров '
                               'промышленного и гражданского строительства.',
            },
        )

        News.objects.get_or_create(
            slug='priemnaya-kampaniya-2026',
            defaults={
                'title': 'Стартовала приёмная кампания 2026 года',
                'category': 'admission',
                'preview_text': 'Подать документы можно очно и через портал Госуслуги, '
                                'разбираем сроки и порядок зачисления.',
                'body': 'Приёмная комиссия начала работу с 20 июня. Абитуриенты могут подать '
                        'документы лично, по почте или через Госуслуги. Приём на бюджетные '
                        'места по большинству направлений продлится до 25 июля.',
                'is_pinned': True,
                'published_at': timezone.now(),
            },
        )
        News.objects.get_or_create(
            slug='studenty-iei-pobedili-v-hakatone',
            defaults={
                'title': 'Студенты ИЭИ заняли первое место на региональном хакатоне',
                'category': 'science',
                'preview_text': 'Команда второго курса разработала систему учёта '
                                'посещаемости за 24 часа.',
                'body': 'Команда из четырёх студентов второго курса ИЭИ представила прототип '
                        'системы учёта посещаемости на базе QR-кодов и заняла первое место '
                        'среди 18 команд из вузов региона.',
                'published_at': timezone.now(),
            },
        )
        self.stdout.write(self.style.SUCCESS('Публичная часть: институты и новости готовы'))
