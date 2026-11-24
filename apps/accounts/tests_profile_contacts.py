"""Тесты контактов пользователя и полей студенческого билета (задача PC).

Проверяем: добавление/удаление/выбор основного контакта, отсутствие лимита
на число контактов, недоступность чужих записей, валидацию значения по типу
(телефон, почта, Telegram, VK), сохранение полей билета и рендер партиала.
"""
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .forms import ContactForm
from .models import Contact, Profile


class ContactsBase(TestCase):
    """Общая заготовка: два пользователя, у каждого свой профиль."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            'pc-student', password='demo12345', email='pc@example.ru',
            first_name='Иван', last_name='Петров',
        )
        cls.other = User.objects.create_user('pc-other', password='demo12345')
        cls.other_contact = Contact.objects.create(
            user=cls.other, kind=Contact.KIND_PHONE, value='+7 900 111-22-33',
        )

    def setUp(self):
        self.client.force_login(self.user)

    def add_contact(self, **data):
        payload = {
            'kind': Contact.KIND_PHONE,
            'value': '+7 900 000-00-00',
            'label': '',
            'next': '',
        }
        payload.update(data)
        return self.client.post(reverse('accounts:contact_add'), payload)

    def make_contacts(self, count):
        return [
            Contact.objects.create(
                user=self.user, kind=Contact.KIND_OTHER, value=f'контакт {i}', order=i,
            )
            for i in range(1, count + 1)
        ]


class ContactFormValidationTests(ContactsBase):
    """Значение проверяется по выбранному типу, пустое — не принимается."""

    def form(self, **data):
        payload = {'kind': Contact.KIND_PHONE, 'value': '', 'label': '', 'is_primary': ''}
        payload.update(data)
        return ContactForm(payload)

    def test_valid_phone_values(self):
        for value in ('+7 900 000-00-00', '8 (900) 123-45-67', '+79001234567'):
            with self.subTest(value=value):
                self.assertTrue(self.form(value=value).is_valid())

    def test_phone_rejects_letters_and_short_values(self):
        for value in ('позвонить', '+7 900', 'abc12345'):
            with self.subTest(value=value):
                form = self.form(value=value)
                self.assertFalse(form.is_valid())
                self.assertIn('value', form.errors)

    def test_email_is_validated(self):
        self.assertTrue(self.form(kind=Contact.KIND_EMAIL, value='name@example.ru').is_valid())

        form = self.form(kind=Contact.KIND_EMAIL, value='name@')
        self.assertFalse(form.is_valid())
        self.assertIn('value', form.errors)

    def test_telegram_accepts_nick_and_link(self):
        for value in ('@nickname', 'https://t.me/nickname', 't.me/nickname'):
            with self.subTest(value=value):
                self.assertTrue(self.form(kind=Contact.KIND_TELEGRAM, value=value).is_valid())

        for value in ('nickname', '@ab', '@nick name'):
            with self.subTest(value=value):
                self.assertFalse(self.form(kind=Contact.KIND_TELEGRAM, value=value).is_valid())

    def test_vk_accepts_nick_and_link(self):
        for value in ('@durov', 'https://vk.com/durov', 'vk.com/id1'):
            with self.subTest(value=value):
                self.assertTrue(self.form(kind=Contact.KIND_VK, value=value).is_valid())

        for value in ('durov', '@d', 'vk.com/дуров'):
            with self.subTest(value=value):
                self.assertFalse(self.form(kind=Contact.KIND_VK, value=value).is_valid())

    def test_empty_value_and_long_value_are_rejected(self):
        self.assertFalse(self.form(value='   ').is_valid())

        form = self.form(kind=Contact.KIND_OTHER, value='x' * 201)
        self.assertFalse(form.is_valid())
        self.assertIn('value', form.errors)

    def test_value_is_stripped(self):
        form = self.form(value='  +7 900 000-00-00  ')
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['value'], '+7 900 000-00-00')


class ContactViewTests(ContactsBase):
    """Добавление, удаление и «основной» — только для своих контактов."""

    def test_add_contact(self):
        response = self.add_contact(label='личный')

        self.assertRedirects(response, reverse('portal:profile'))
        contact = Contact.objects.get(user=self.user)
        self.assertEqual(contact.kind, Contact.KIND_PHONE)
        self.assertEqual(contact.value, '+7 900 000-00-00')
        self.assertEqual(contact.label, 'личный')
        self.assertFalse(contact.is_primary)

    def test_add_is_unlimited(self):
        for i in range(10):
            response = self.add_contact(value=f'+7 900 000-{i:02d}-00', label=f'№ {i}')
            self.assertEqual(response.status_code, 302)

        self.assertEqual(Contact.objects.filter(user=self.user).count(), 10)

    def test_add_primary_clears_previous(self):
        first = Contact.objects.create(
            user=self.user, kind=Contact.KIND_EMAIL, value='first@example.ru', is_primary=True,
        )

        self.add_contact(value='+7 900 222-33-44', is_primary='on')

        first.refresh_from_db()
        self.assertFalse(first.is_primary)
        self.assertEqual(Contact.objects.filter(user=self.user, is_primary=True).count(), 1)
        primary = Contact.objects.get(user=self.user, is_primary=True)
        self.assertEqual(primary.value, '+7 900 222-33-44')

    def test_delete_own_contact(self):
        contact = Contact.objects.create(
            user=self.user, kind=Contact.KIND_OTHER, value='del me',
        )
        response = self.client.post(reverse('accounts:contact_delete', args=[contact.pk]))

        self.assertRedirects(response, reverse('portal:profile'))
        self.assertFalse(Contact.objects.filter(pk=contact.pk).exists())

    def test_foreign_contact_is_not_found(self):
        response = self.client.post(
            reverse('accounts:contact_delete', args=[self.other_contact.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Contact.objects.filter(pk=self.other_contact.pk).exists())

    def test_foreign_contact_cannot_be_made_primary(self):
        response = self.client.post(
            reverse('accounts:contact_primary', args=[self.other_contact.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.other_contact.refresh_from_db()
        self.assertFalse(self.other_contact.is_primary)

    def test_primary_switches_between_own_contacts(self):
        first, second = self.make_contacts(2)
        Contact.objects.filter(pk=first.pk).update(is_primary=True)

        response = self.client.post(reverse('accounts:contact_primary', args=[second.pk]))

        self.assertRedirects(response, reverse('portal:profile'))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_primary)
        self.assertTrue(second.is_primary)

    def test_next_returns_to_contacts_tab(self):
        target = reverse('accounts:profile_contacts')
        response = self.add_contact(next=target)
        self.assertRedirects(response, target)

    def test_foreign_next_is_ignored(self):
        response = self.add_contact(next='https://evil.example/steal')
        self.assertRedirects(response, reverse('portal:profile'))

    def test_json_response_for_ajax(self):
        response = self.client.post(
            reverse('accounts:contact_add'),
            {'kind': Contact.KIND_EMAIL, 'value': 'ajax@example.ru'},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['contact']['value'], 'ajax@example.ru')

    def test_json_error_for_ajax_validation(self):
        response = self.client.post(
            reverse('accounts:contact_add'),
            {'kind': Contact.KIND_PHONE, 'value': 'не телефон'},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])
        self.assertTrue(response.json()['errors'])

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse('accounts:contact_add')).status_code, 405)

    def test_login_required(self):
        self.client.logout()
        for name in ('accounts:contact_add', 'accounts:contact_delete', 'accounts:contact_primary',
                     'accounts:ticket_save'):
            args = [self.other_contact.pk] if 'delete' in name or 'primary' in name else []
            with self.subTest(view=name):
                response = self.client.post(reverse(name, args=args), {})
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('accounts:login'), response['Location'])

    def test_profile_page_shows_own_contacts_only(self):
        Contact.objects.create(user=self.user, kind=Contact.KIND_EMAIL, value='mine@example.ru')

        response = self.client.get(reverse('portal:profile'))
        self.assertContains(response, 'mine@example.ru')
        self.assertNotContains(response, self.other_contact.value)

    def test_profile_page_empty_state(self):
        response = self.client.get(reverse('portal:profile'))
        self.assertContains(response, 'Контактов пока нет')

    def test_contacts_tab_page_has_form(self):
        response = self.client.get(reverse('accounts:profile_contacts'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('accounts:contact_add'))
        self.assertContains(response, 'Добавить контакт')


class TicketSaveTests(ContactsBase):
    """Поля билета сохраняются, проверяются и не мешают остальному профилю."""

    def test_ticket_fields_are_saved(self):
        response = self.client.post(reverse('accounts:ticket_save'), {
            'ticket_order': '№ 1234-с от 01.09.2025',
            'ticket_issue_date': '2025-09-01',
            'education_level': Profile.LEVEL_BACHELOR,
            'study_form': Profile.FORM_FULL_TIME,
            'direction': '09.03.02 Информационные системы и технологии',
        })

        self.assertRedirects(response, reverse('portal:profile'))
        profile = Profile.objects.get(user=self.user)
        self.assertEqual(profile.ticket_order, '№ 1234-с от 01.09.2025')
        self.assertEqual(profile.ticket_issue_date, date(2025, 9, 1))
        self.assertEqual(profile.education_level, Profile.LEVEL_BACHELOR)
        self.assertEqual(profile.study_form, Profile.FORM_FULL_TIME)
        self.assertEqual(profile.direction, '09.03.02 Информационные системы и технологии')

    def test_ticket_fields_are_optional_and_can_be_cleared(self):
        response = self.client.post(reverse('accounts:ticket_save'), {})
        self.assertRedirects(response, reverse('portal:profile'))

        profile = Profile.objects.get(user=self.user)
        self.assertEqual(profile.ticket_order, '')
        self.assertIsNone(profile.ticket_issue_date)
        self.assertEqual(profile.education_level, '')
        self.assertEqual(profile.study_form, '')
        self.assertEqual(profile.direction, '')

    def test_invalid_date_does_not_change_profile(self):
        profile = Profile.objects.get(user=self.user)
        profile.ticket_order = 'прежний приказ'
        profile.save(update_fields=['ticket_order'])

        response = self.client.post(reverse('accounts:ticket_save'), {
            'ticket_order': 'новый приказ',
            'ticket_issue_date': '31.02.2025',
        })

        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        self.assertEqual(profile.ticket_order, 'прежний приказ')
        self.assertIsNone(profile.ticket_issue_date)

    def test_unknown_education_level_is_rejected(self):
        response = self.client.post(
            reverse('accounts:ticket_save'),
            {'education_level': 'doctorate'},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])
        self.assertEqual(Profile.objects.get(user=self.user).education_level, '')

    def test_ticket_save_does_not_touch_ticket_number(self):
        profile = Profile.objects.get(user=self.user)
        profile.ticket_number = 'СТ-777777'
        profile.save(update_fields=['ticket_number'])

        self.client.post(reverse('accounts:ticket_save'), {
            'ticket_order': '№ 1', 'education_level': Profile.LEVEL_MASTER,
        })

        profile.refresh_from_db()
        self.assertEqual(profile.ticket_number, 'СТ-777777')
        self.assertEqual(profile.education_level, Profile.LEVEL_MASTER)

    def test_ticket_form_on_profile_page(self):
        response = self.client.get(reverse('portal:profile'))
        self.assertContains(response, reverse('accounts:ticket_save'))
        self.assertContains(response, 'Заполнить данные билета')
