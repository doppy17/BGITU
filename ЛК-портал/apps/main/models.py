"""Публичная часть сайта: институты и новости (перенесено из проекта в Загрузках)."""
from django.db import models
from django.utils import timezone


class Institute(models.Model):
    name = models.CharField('полное название', max_length=255)
    short_name = models.CharField('сокращение', max_length=20, help_text='Например, ИЭИ, СИ')
    slug = models.SlugField(unique=True)
    description = models.TextField('описание', blank=True)
    dean = models.CharField('директор/декан', max_length=150, blank=True)
    photo_url = models.URLField('фото (ссылка)', blank=True)

    class Meta:
        verbose_name = 'институт / факультет'
        verbose_name_plural = 'институты и факультеты'
        ordering = ['name']

    def __str__(self):
        return self.short_name


class News(models.Model):
    CATEGORY_CHOICES = [
        ('university', 'Университет'),
        ('science', 'Наука'),
        ('sport', 'Спорт'),
        ('admission', 'Абитуриентам'),
    ]

    title = models.CharField('заголовок', max_length=255)
    slug = models.SlugField(unique=True)
    category = models.CharField('категория', max_length=20, choices=CATEGORY_CHOICES, default='university')
    preview_text = models.CharField('анонс', max_length=400)
    body = models.TextField('текст')
    cover_url = models.URLField('обложка (ссылка)', blank=True)
    is_published = models.BooleanField('опубликовано', default=True)
    is_pinned = models.BooleanField('закрепить на главной', default=False)
    published_at = models.DateTimeField('дата публикации', default=timezone.now)

    class Meta:
        verbose_name = 'новость'
        verbose_name_plural = 'новости'
        ordering = ['-is_pinned', '-published_at']

    def __str__(self):
        return self.title
