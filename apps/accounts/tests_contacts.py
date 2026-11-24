"""Тесты контактов пользователя и расчётов для студенческого билета.

Задача PA: модель ``Contact`` (безлимитные контакты, один основной),
год окончания обучения и подписи блока билета.
"""
from django.contrib import admin
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Q, UniqueConstraint
from django.test import TestCase

from apps.education.models import Group

from .models import Contact, Profile


class ContactModelTests(TestCase):
    """Контакты: безлимит, порядок, тип по умолчанию."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            'contacts-student', password='demo12345',
            email='contacts@example.ru', first_name='Иван', last_name='Тестов',
        )

    def test_ten_contacts_for_one_user(self):
        for index in range(10):
            Contact.objects.create(
                user=self.user,
                kind=Contact.KIND_PHONE if index % 2 else Contact.KIND_EMAIL,
                value=f'contact-{index}@example.ru',
                order=index,
            )

        self.assertEqual(self.user.contacts.count(), 10)
        self.assertEqual(Contact.objects.filter(user=self.user).count(), 10)

    def test_defaults_and_created_at(self):
        contact = Contact.objects.create(user=self.user, value='+7 900 000-00-00')

        self.assertEqual(contact.kind, Contact.KIND_PHONE)
        self.assertEqual(contact.label, '')
        self.assertFalse(contact.is_primary)
        self.assertEqual(contact.order, 0)
        self.assertIsNotNone(contact.created_at)
        self.assertEqual(contact.get_kind_display(), 'телефон')

    def test_ordering_by_order_then_id(self):
        third = Contact.objects.create(user=self.user, value='третий', order=2)
        second = Contact.objects.create(user=self.user, value='второй', order=1)
        first = Contact.objects.create(user=self.user, value='первый', order=1)

        values = list(Contact.objects.filter(user=self.user).values_list('pk', flat=True))
        self.assertEqual(values, [second.pk, first.pk, third.pk])

    def test_str_contains_user_kind_and_value(self):
        contact = Contact.objects.create(
            user=self.user, kind=Contact.KIND_TELEGRAM,
            value='@testov', label='личный', is_primary=True,
        )
        text = str(contact)
        self.assertIn('Telegram', text)
        self.assertIn('@testov', text)
        self.assertIn('личный', text)


class ContactPrimaryTests(TestCase):
    """У пользователя не больше одного основного контакта."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('contacts-primary', password='demo12345')
        cls.other = User.objects.create_user('contacts-other', password='demo12345')

    def test_first_primary_is_allowed(self):
        contact = Contact.objects.create(user=self.user, value='+7 900 111-22-33', is_primary=True)
        self.assertTrue(Contact.objects.get(pk=contact.pk).is_primary)

    def test_second_primary_raises_integrity_error(self):
        Contact.objects.create(user=self.user, value='+7 900 111-22-33', is_primary=True)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Contact.objects.create(user=self.user, value='+7 900 444-55-66', is_primary=True)

        self.assertEqual(Contact.objects.filter(user=self.user, is_primary=True).count(), 1)

    def test_making_existing_contact_primary_raises(self):
        Contact.objects.create(user=self.user, value='+7 900 111-22-33', is_primary=True)
        secondary = Contact.objects.create(user=self.user, value='+7 900 444-55-66')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                secondary.is_primary = True
                secondary.save(update_fields=['is_primary'])

    def test_primary_is_per_user(self):
        Contact.objects.create(user=self.user, value='+7 900 111-22-33', is_primary=True)
        Contact.objects.create(user=self.other, value='+7 900 444-55-66', is_primary=True)

        self.assertEqual(Contact.objects.filter(is_primary=True).count(), 2)

    def test_many_non_primary_contacts_allowed(self):
        for index in range(5):
            Contact.objects.create(user=self.user, value=f'extra-{index}', is_primary=False)

        self.assertEqual(Contact.objects.filter(user=self.user, is_primary=False).count(), 5)

    def test_meta_has_partial_unique_constraint(self):
        constraints = [
            constraint for constraint in Contact._meta.constraints
            if isinstance(constraint, UniqueConstraint)
        ]
        self.assertEqual(len(constraints), 1)

        constraint = constraints[0]
        self.assertEqual(constraint.fields, ('user',))
        self.assertEqual(constraint.condition, Q(is_primary=True))
        self.assertEqual(constraint.name, 'uniq_primary_contact_per_user')


class ContactCascadeTests(TestCase):
    """Удаление пользователя удаляет его контакты."""

    def test_contacts_are_deleted_with_user(self):
        user = User.objects.create_user('contacts-cascade', password='demo12345')
        Contact.objects.create(user=user, value='+7 900 111-22-33', is_primary=True)
        Contact.objects.create(user=user, value='cascade@example.ru', kind=Contact.KIND_EMAIL)

        user.delete()

        self.assertEqual(Contact.objects.count(), 0)


class ProfileGraduationTests(TestCase):
    """Год окончания: срок уровня + год поступления; без года — None."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            'graduation-student', password='demo12345',
            first_name='Анна', last_name='Сидорова',
        )
        cls.profile = cls.user.profile

    def test_terms_for_each_level(self):
        cases = [
            (Profile.LEVEL_BACHELOR, 4),
            (Profile.LEVEL_SPECIALIST, 5),
            (Profile.LEVEL_MASTER, 2),
            (Profile.LEVEL_POSTGRADUATE, 3),
        ]
        profile = self.profile
        profile.enrollment_year = 2025

        for level, term in cases:
            with self.subTest(level=level):
                profile.education_level = level
                self.assertEqual(profile.study_term_years, term)
                self.assertEqual(profile.graduation_year, 2025 + term)

    def test_blank_level_uses_four_years(self):
        self.profile.education_level = ''
        self.profile.enrollment_year = 2025

        self.assertEqual(self.profile.study_term_years, 4)
        self.assertEqual(self.profile.graduation_year, 2029)

    def test_unknown_level_uses_default(self):
        self.profile.education_level = 'unknown-level'
        self.profile.enrollment_year = 2024

        self.assertEqual(self.profile.graduation_year, 2028)

    def test_empty_enrollment_year_gives_none(self):
        self.profile.enrollment_year = None
        self.profile.education_level = Profile.LEVEL_MASTER

        self.assertIsNone(self.profile.graduation_year)
        self.assertEqual(self.profile.study_years_label, '')

    def test_study_years_label_uses_en_dash(self):
        self.profile.enrollment_year = 2025
        self.profile.education_level = Profile.LEVEL_BACHELOR

        self.assertEqual(self.profile.study_years_label, '2025–2029')

    def test_graduation_year_is_persisted_fields_only(self):
        """Свойство не требует сохранения — считается из полей профиля."""
        self.profile.enrollment_year = 2023
        self.profile.education_level = Profile.LEVEL_SPECIALIST
        self.profile.save(update_fields=['enrollment_year', 'education_level'])

        self.assertEqual(self.profile.graduation_year, 2028)
        self.assertEqual(self.profile.study_years_label, '2023–2028')


class ProfileTicketTests(TestCase):
    """Подписи и значения блока студенческого билета."""

    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(
            code='ИСТ-201', program='09.03.02 Информационные системы и технологии',
            course=2, institute='Инженерно-экономический институт',
        )
        cls.user = User.objects.create_user(
            'ticket-student', password='demo12345',
            first_name='Георгий', last_name='Портнов',
        )
        cls.profile = cls.user.profile

    def test_ticket_direction_prefers_own_value(self):
        self.profile.direction = '09.04.02 Магистерская программа'
        self.profile.group = self.group

        self.assertEqual(self.profile.ticket_direction, '09.04.02 Магистерская программа')

    def test_ticket_direction_falls_back_to_group_program(self):
        self.profile.direction = ''
        self.profile.group = self.group

        self.assertEqual(self.profile.ticket_direction, self.group.program)

    def test_ticket_direction_is_empty_without_direction_and_group(self):
        self.profile.direction = ''
        self.profile.group = None

        self.assertEqual(self.profile.ticket_direction, '')

    def test_ticket_level_label(self):
        self.profile.education_level = Profile.LEVEL_BACHELOR
        self.assertEqual(self.profile.ticket_level_label, 'бакалавриат')

        self.profile.education_level = Profile.LEVEL_SPECIALIST
        self.assertEqual(self.profile.ticket_level_label, 'специалитет')

        self.profile.education_level = Profile.LEVEL_MASTER
        self.assertEqual(self.profile.ticket_level_label, 'магистратура')

        self.profile.education_level = Profile.LEVEL_POSTGRADUATE
        self.assertEqual(self.profile.ticket_level_label, 'аспирантура')

    def test_ticket_form_label(self):
        self.profile.study_form = Profile.FORM_FULL_TIME
        self.assertEqual(self.profile.ticket_form_label, 'очная')

        self.profile.study_form = Profile.FORM_PART_TIME
        self.assertEqual(self.profile.ticket_form_label, 'очно-заочная')

        self.profile.study_form = Profile.FORM_CORRESPONDENCE
        self.assertEqual(self.profile.ticket_form_label, 'заочная')

    def test_ticket_labels_fallback_when_blank(self):
        self.profile.education_level = ''
        self.profile.study_form = ''

        self.assertEqual(self.profile.ticket_level_label, '—')
        self.assertEqual(self.profile.ticket_form_label, '—')

    def test_ticket_number_or_record(self):
        self.profile.ticket_number = 'СТ-123456'
        self.profile.record_book = 'ИСТ-201/25'
        self.assertEqual(self.profile.ticket_number_or_record, 'СТ-123456')

        self.profile.ticket_number = ''
        self.assertEqual(self.profile.ticket_number_or_record, 'ИСТ-201/25')

        self.profile.record_book = ''
        self.assertEqual(self.profile.ticket_number_or_record, '')


class ContactAdminTests(TestCase):
    """Contact зарегистрирован в админке с поиском и фильтрами."""

    def test_contact_is_registered(self):
        self.assertIn(Contact, admin.site._registry)

    def test_admin_list_display(self):
        model_admin = admin.site._registry[Contact]
        self.assertEqual(
            model_admin.list_display,
            ('user', 'kind', 'value', 'label', 'is_primary', 'order', 'created_at'),
        )

    def test_admin_list_filter(self):
        model_admin = admin.site._registry[Contact]
        self.assertEqual(model_admin.list_filter, ('kind', 'is_primary'))

    def test_admin_search_fields(self):
        model_admin = admin.site._registry[Contact]
        for field in ('user__username', 'user__email', 'value', 'label'):
            self.assertIn(field, model_admin.search_fields)
