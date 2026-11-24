"""Тесты страницы редактирования собственного профиля (задача D2)."""
import io
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.education.models import Group

from .models import Profile, ProfileHistory


def image_upload(name='avatar.png', fmt='PNG', size=(8, 8), padding=0, content_type='image/png'):
    """Валидная картинка; padding добавляет «хвост» для проверки лимита размера."""
    buffer = io.BytesIO()
    Image.new('RGB', size, (60, 120, 180)).save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue() + b'\0' * padding, content_type=content_type)


class ProfileEditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group_a = Group.objects.create(
            code='ИСТ-101', program='09.03.02 ИСТ', course=1,
            institute='Инженерно-экономический институт',
        )
        cls.group_b = Group.objects.create(
            code='ИВТ-301', program='09.03.01 ИВТ', course=3,
            institute='Институт лесного комплекса',
        )
        cls.user = User.objects.create_user(
            'edit-student', password='demo12345', email='student@example.ru',
            first_name='Георгий', last_name='Портнов',
        )
        cls.other = User.objects.create_user(
            'other-student', password='demo12345', email='other@example.ru',
        )
        cls.profile = cls.user.profile
        cls.profile.group = cls.group_a
        cls.profile.subgroup = 'а'
        cls.profile.phone = '+7 900 111-22-33'
        cls.profile.department = 'Информационных технологий'
        cls.profile.record_book = 'ИСТ-101/24'
        cls.profile.save()

    def setUp(self):
        # setUpTestData назначил группу — его запись истории не относится к тесту.
        ProfileHistory.objects.all().delete()
        self.media_root = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, True)
        self.url = reverse('accounts:profile_edit')

    def login(self):
        self.client.force_login(self.user)

    def test_page_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_url_lives_under_cabinet_profile_edit(self):
        self.assertEqual(self.url, '/cabinet/profile/edit/')

    def test_get_renders_current_values(self):
        self.login()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Редактирование профиля')
        self.assertContains(response, 'ИСТ-101')
        self.assertContains(response, 'value="Портнов"')
        self.assertContains(response, 'value="student@example.ru"')
        self.assertContains(response, 'value="+7 900 111-22-33"')
        self.assertContains(response, 'data-searchable')
        self.assertContains(response, '— не выбрана —')

    def test_post_updates_user_and_profile_and_writes_history(self):
        self.login()
        response = self.client.post(self.url, {
            'last_name': 'Иванов',
            'first_name': 'Пётр',
            'email': 'petr@example.ru',
            'group': self.group_b.pk,
            'subgroup': 'б',
            'phone': '+7 900 555-44-33',
            'department': 'Высшей математики',
            'institute_override': 'Институт лесного комплекса',
            'record_book': 'ИВТ-301/24',
            'enrollment_year': '2024',
        }, follow=True)

        self.assertRedirects(response, reverse('portal:profile'))
        self.assertContains(response, 'Профиль обновлён.')

        self.user.refresh_from_db()
        self.assertEqual(self.user.last_name, 'Иванов')
        self.assertEqual(self.user.first_name, 'Пётр')
        self.assertEqual(self.user.email, 'petr@example.ru')

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.group, self.group_b)
        self.assertEqual(self.profile.subgroup, 'б')
        self.assertEqual(self.profile.phone, '+7 900 555-44-33')
        self.assertEqual(self.profile.department, 'Высшей математики')
        self.assertEqual(self.profile.institute_override, 'Институт лесного комплекса')
        self.assertEqual(self.profile.record_book, 'ИВТ-301/24')
        self.assertEqual(self.profile.enrollment_year, 2024)
        self.assertEqual(self.profile.institute, 'Институт лесного комплекса')

        history = ProfileHistory.objects.get(user=self.user, kind=ProfileHistory.KIND_GROUP)
        self.assertEqual(history.from_value, 'ИСТ-101')
        self.assertEqual(history.to_value, 'ИВТ-301')
        self.assertIn('Институт: Инженерно-экономический институт → Институт лесного комплекса', history.comment)

    def test_group_change_through_form_creates_history_even_without_other_edits(self):
        self.login()
        response = self.client.post(self.url, {
            'last_name': 'Портнов', 'first_name': 'Георгий', 'email': 'student@example.ru',
            'group': self.group_b.pk, 'subgroup': '',
            'phone': '', 'department': '', 'institute_override': '', 'record_book': '',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            ProfileHistory.objects.filter(user=self.user, kind=ProfileHistory.KIND_GROUP).count(), 1,
        )

    def test_duplicate_email_is_rejected_case_insensitively(self):
        self.login()
        response = self.client.post(self.url, {
            'last_name': 'Портнов', 'first_name': 'Георгий',
            'email': 'OTHER@EXAMPLE.RU',
            'group': self.group_a.pk, 'subgroup': 'а',
            'phone': '', 'department': '', 'institute_override': '', 'record_book': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'уже зарегистрирован другой аккаунт')
        self.assertFalse(ProfileHistory.objects.exists())

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'student@example.ru')

    def test_photo_is_saved_under_profiles(self):
        self.login()
        response = self.client.post(self.url, {
            'last_name': 'Портнов', 'first_name': 'Георгий', 'email': 'student@example.ru',
            'group': self.group_a.pk, 'subgroup': 'а',
            'phone': '', 'department': '', 'institute_override': '', 'record_book': '',
            'photo': image_upload(),
        })
        self.assertEqual(response.status_code, 302)

        self.profile.refresh_from_db()
        self.assertTrue(self.profile.photo.name.startswith('profiles/'))
        self.assertTrue(self.profile.photo.storage.exists(self.profile.photo.name))

        # После сохранения страница показывает превью по MEDIA_URL.
        page = self.client.get(self.url)
        self.assertContains(page, self.profile.photo.url)

    def test_photo_bigger_than_5mb_is_rejected(self):
        self.login()
        padding = 5 * 1024 * 1024
        response = self.client.post(self.url, {
            'last_name': 'Портнов', 'first_name': 'Георгий', 'email': 'student@example.ru',
            'group': self.group_a.pk,
            'photo': image_upload(padding=padding),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Файл больше 5 МБ')
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.photo)

    def test_photo_type_is_limited_to_jpeg_png_webp(self):
        self.login()
        response = self.client.post(self.url, {
            'last_name': 'Портнов', 'first_name': 'Георгий', 'email': 'student@example.ru',
            'group': self.group_a.pk,
            'photo': image_upload(name='avatar.gif', fmt='GIF', content_type='image/gif'),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Поддерживаются только JPEG, PNG и WebP')
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.photo)

    def test_clear_photo_checkbox_removes_it(self):
        self.profile.photo = image_upload()
        self.profile.save()
        self.login()
        response = self.client.post(self.url, {
            'last_name': 'Портнов', 'first_name': 'Георгий', 'email': 'student@example.ru',
            'group': self.group_a.pk,
            'photo-clear': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.photo)

    def test_saving_without_new_photo_keeps_existing(self):
        self.profile.photo = image_upload()
        self.profile.save()
        old_name = self.profile.photo.name
        self.login()
        response = self.client.post(self.url, {
            'last_name': 'Портнов', 'first_name': 'Георгий', 'email': 'student@example.ru',
            'group': self.group_a.pk, 'phone': '+7 900 777-88-99',
        })
        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.photo.name, old_name)
        self.assertEqual(self.profile.phone, '+7 900 777-88-99')

    def test_names_may_be_empty(self):
        self.login()
        response = self.client.post(self.url, {
            'last_name': '', 'first_name': '', 'email': 'student@example.ru',
            'group': '', 'subgroup': '', 'phone': '', 'department': '',
            'institute_override': '', 'record_book': '',
        })
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.user.last_name, '')
        self.assertEqual(self.user.first_name, '')
        self.assertIsNone(self.profile.group)

    def test_invalid_enrollment_year_is_reported_in_russian(self):
        self.login()
        response = self.client.post(self.url, {
            'last_name': 'Портнов', 'first_name': 'Георгий', 'email': 'student@example.ru',
            'group': self.group_a.pk,
            'enrollment_year': '1999',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'не может быть раньше 2000')

    def test_profile_page_has_edit_link(self):
        self.login()
        response = self.client.get(reverse('portal:profile'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('accounts:profile_edit'))
