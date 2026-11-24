"""Тесты блока 01 «Данные студента»: выпадающие списки и правка на месте (E3)."""
import json
import re
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.education.models import Group

from .forms import ProfileEditForm, ProfileFactsForm
from .models import Profile, ProfileHistory


class ProfileFactsFormTests(TestCase):
    """Форма блока 01: институт, группа, подгруппа, курс — списки, год — число."""

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
            'facts-student', password='demo12345', email='facts@example.ru',
        )

    def setUp(self):
        self.profile = self.user.profile
        self.profile.group = self.group_a
        self.profile.enrollment_year = 2024
        self.profile.save()
        ProfileHistory.objects.all().delete()

    def form_data(self, **overrides):
        data = {
            'institute': '',
            'group': str(self.group_a.pk),
            'subgroup': '',
            'course': '',
            'enrollment_year': '2024',
        }
        data.update(overrides)
        return data

    def test_institute_choices_are_unique_group_institutes(self):
        form = ProfileEditForm(instance=self.profile, user=self.user)
        values = [value for value, _ in form.fields['institute'].choices]
        self.assertEqual(values, [
            '',
            'Инженерно-экономический институт',
            'Институт лесного комплекса',
        ])

    def test_current_override_is_added_when_missing_from_groups(self):
        self.profile.institute_override = 'Институт, которого нет в справочнике'
        self.profile.save()
        form = ProfileEditForm(instance=self.profile, user=self.user)
        values = [value for value, _ in form.fields['institute'].choices]
        self.assertIn('Институт, которого нет в справочнике', values)
        self.assertEqual(form.fields['institute'].initial, 'Институт, которого нет в справочнике')

    def test_selects_and_number_widgets(self):
        form = ProfileFactsForm(instance=self.profile, user=self.user)
        self.assertEqual(form.fields['institute'].widget.__class__.__name__, 'Select')
        self.assertEqual(form.fields['group'].widget.__class__.__name__, 'Select')
        self.assertEqual(form.fields['subgroup'].widget.__class__.__name__, 'Select')
        self.assertEqual(form.fields['course'].widget.__class__.__name__, 'Select')
        self.assertEqual(
            [value for value, _ in form.fields['subgroup'].choices], ['', 'а', 'б'],
        )
        self.assertEqual(
            [value for value, _ in form.fields['course'].choices],
            ['', '1', '2', '3', '4', '5', '6'],
        )

    def test_enrollment_year_is_number_field_with_bounds(self):
        widget = ProfileEditForm(instance=self.profile, user=self.user).fields['enrollment_year'].widget
        self.assertEqual(widget.input_type, 'number')
        self.assertEqual(str(widget.attrs['min']), '2000')
        self.assertEqual(str(widget.attrs['max']), '2100')

    def test_course_is_limited_by_study_term(self):
        # Магистратура — 2 года, значит не больше 4 курсов.
        self.profile.education_level = Profile.LEVEL_MASTER
        self.profile.save()
        form = ProfileFactsForm(self.form_data(course='5'), instance=self.profile, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('Курс не может быть больше 4 для вашего уровня обучения.', form.errors['course'])

    def test_course_override_is_saved_and_cleared(self):
        form = ProfileFactsForm(self.form_data(course='2'), instance=self.profile, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.course_override, 2)
        self.assertEqual(self.profile.course, 2)

        form = ProfileFactsForm(self.form_data(course=''), instance=self.profile, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.course_override)
        self.assertEqual(self.profile.course, 1)

    def test_institute_override_is_saved_and_cleared(self):
        form = ProfileFactsForm(
            self.form_data(institute='Институт лесного комплекса'),
            instance=self.profile, user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.institute_override, 'Институт лесного комплекса')
        self.assertEqual(self.profile.institute, 'Институт лесного комплекса')

        form = ProfileFactsForm(self.form_data(institute=''), instance=self.profile, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.institute_override, '')
        self.assertEqual(self.profile.institute, 'Инженерно-экономический институт')

    def test_direction_follows_group_when_it_was_autofilled(self):
        self.profile.direction = self.group_a.program
        self.profile.save()
        form = ProfileFactsForm(
            self.form_data(group=str(self.group_b.pk)), instance=self.profile, user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.direction, self.group_b.program)

    def test_manual_direction_is_kept_after_group_change(self):
        self.profile.direction = 'Моё направление'
        self.profile.save()
        form = ProfileFactsForm(
            self.form_data(group=str(self.group_b.pk)), instance=self.profile, user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.direction, 'Моё направление')

    def test_enrollment_year_bounds_report_russian_errors(self):
        form = ProfileFactsForm(self.form_data(enrollment_year='1999'), instance=self.profile, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('Год поступления не может быть раньше 2000.', form.errors['enrollment_year'])

        form = ProfileFactsForm(self.form_data(enrollment_year='2101'), instance=self.profile, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('Год поступления не может быть позже 2100.', form.errors['enrollment_year'])

    def test_groups_are_selected_by_pk_and_foreign_group_changes_history(self):
        form = ProfileFactsForm(
            self.form_data(group=str(self.group_b.pk)), instance=self.profile, user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.group, self.group_b)
        history = ProfileHistory.objects.get(user=self.user)
        self.assertEqual(history.from_value, 'ИСТ-101')
        self.assertEqual(history.to_value, 'ИВТ-301')

    def test_partial_full_form_keeps_institute_and_course_overrides(self):
        """Форма без этих полей в POST (например, только фото) их не стирает."""
        self.profile.institute_override = 'Институт лесного комплекса'
        self.profile.course_override = 2
        self.profile.save()
        form = ProfileEditForm({
            'last_name': 'Портнов', 'first_name': 'Георгий', 'email': 'facts@example.ru',
            'group': str(self.group_a.pk), 'subgroup': '', 'phone': '', 'department': '',
            'record_book': '',
        }, instance=self.profile, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.institute_override, 'Институт лесного комплекса')
        self.assertEqual(self.profile.course_override, 2)


class ProfileInlinePageTests(TestCase):
    """Страница профиля: форма блока 01 и сохранение через fetch (JSON)."""

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
            'inline-student', password='demo12345', email='inline@example.ru',
            first_name='Георгий', last_name='Портнов',
        )

    def setUp(self):
        self.profile = self.user.profile
        self.profile.group = self.group_a
        self.profile.enrollment_year = 2024
        self.profile.education_level = Profile.LEVEL_BACHELOR
        self.profile.save()
        ProfileHistory.objects.all().delete()
        self.client.force_login(self.user)
        self.media_root = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, True)

    def ajax(self, **overrides):
        data = {
            'scope': 'profile-facts',
            'institute': '',
            'group': str(self.group_a.pk),
            'subgroup': '',
            'course': '',
            'enrollment_year': '2024',
        }
        data.update(overrides)
        return self.client.post(
            reverse('accounts:profile_edit'), data, HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_profile_page_has_inline_form_with_selects_and_number(self):
        response = self.client.get(reverse('portal:profile'))
        html = response.content.decode()
        self.assertContains(response, 'data-profile-facts')
        for name in ('institute', 'group', 'subgroup', 'course'):
            self.assertIn(f'<select name="{name}"', html)
        self.assertIn('<input type="number" name="enrollment_year"', html)
        self.assertNotIn('name="graduation_year"', html)
        self.assertIn('data-searchable="true"', html)
        self.assertIn('name="scope" value="profile-facts"', html)

    def test_edit_button_without_js_links_to_edit_page(self):
        html = self.client.get(reverse('portal:profile')).content.decode()
        match = re.search(r'<a[^>]*data-profile-edit-open[^>]*>', html)
        self.assertIsNotNone(match)
        self.assertIn(reverse('accounts:profile_edit'), match.group(0))

    def test_edit_page_also_uses_selects_and_number(self):
        response = self.client.get(reverse('accounts:profile_edit'))
        html = response.content.decode()
        for name in ('institute', 'group', 'subgroup', 'course'):
            self.assertIn(f'<select name="{name}"', html)
        self.assertIn('<input type="number" name="enrollment_year"', html)
        self.assertNotIn('name="institute_override"', html)

    def test_ajax_save_returns_html_and_updates_profile(self):
        response = self.ajax(
            institute='Институт лесного комплекса',
            group=str(self.group_b.pk),
            subgroup='б',
            course='4',
            enrollment_year='2023',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'].split(';')[0], 'application/json')
        payload = json.loads(response.content)
        self.assertTrue(payload['ok'])
        self.assertIn('ИВТ-301', payload['html'])
        self.assertEqual(payload['profile']['group'], 'ИВТ-301')
        self.assertEqual(payload['profile']['course'], 4)
        self.assertEqual(payload['profile']['graduation_year'], 2027)
        self.assertEqual(payload['profile']['institute'], 'Институт лесного комплекса')

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.group, self.group_b)
        self.assertEqual(self.profile.subgroup, 'б')
        self.assertEqual(self.profile.course_override, 4)
        self.assertEqual(self.profile.enrollment_year, 2023)
        self.assertEqual(self.profile.institute_override, 'Институт лесного комплекса')
        self.assertEqual(self.profile.graduation_year, 2027)

        history = ProfileHistory.objects.get(user=self.user)
        self.assertEqual(history.from_value, 'ИСТ-101')
        self.assertEqual(history.to_value, 'ИВТ-301')
        self.assertIn('Курс: 1 → 3', history.comment)

    def test_ajax_payload_keeps_full_profile_html_section(self):
        payload = json.loads(self.ajax(group=str(self.group_b.pk)).content)
        html = payload['html']
        self.assertIn('data-profile-facts', html)
        self.assertIn('id="profileFacts"', html)
        self.assertIn('ИВТ-301', html)
        self.assertEqual(payload['profile']['direction'], '09.03.01 ИВТ')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.direction, '09.03.01 ИВТ')

    def test_ajax_validation_error_returns_fields_and_keeps_data(self):
        self.profile.education_level = Profile.LEVEL_MASTER
        self.profile.save()
        response = self.ajax(course='5')
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.content)
        self.assertFalse(payload['ok'])
        self.assertIn('Курс не может быть больше 4 для вашего уровня обучения.', payload['fields']['course'])
        self.assertEqual(payload['message'], 'Курс не может быть больше 4 для вашего уровня обучения.')

        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.course_override)

    def test_ajax_does_not_touch_user_account_fields(self):
        before = (self.user.email, self.user.last_name, self.user.first_name)
        self.ajax(subgroup='а', enrollment_year='2025')
        self.user.refresh_from_db()
        self.assertEqual((self.user.email, self.user.last_name, self.user.first_name), before)

    def test_inline_post_without_xhr_falls_back_to_profile_redirect(self):
        response = self.client.post(reverse('accounts:profile_edit'), {
            'scope': 'profile-facts',
            'institute': '',
            'group': str(self.group_b.pk),
            'subgroup': 'а',
            'course': '2',
            'enrollment_year': '2022',
        })
        self.assertRedirects(response, reverse('portal:profile'))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.group, self.group_b)
        self.assertEqual(self.profile.course_override, 2)

    def test_legacy_institute_override_post_is_accepted(self):
        """Старые формы присылали institute_override текстом — не теряем значение."""
        response = self.client.post(reverse('accounts:profile_edit'), {
            'last_name': 'Портнов', 'first_name': 'Георгий', 'email': 'inline@example.ru',
            'group': self.group_a.pk, 'subgroup': '',
            'institute_override': 'Институт лесного комплекса',
            'course': '3', 'enrollment_year': '2024',
        })
        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.institute_override, 'Институт лесного комплекса')
        self.assertEqual(self.profile.course_override, 3)
