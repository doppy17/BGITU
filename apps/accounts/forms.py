"""Формы личного кабинета: пользователь редактирует собственный профиль."""
import re

from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import validate_email
from django.utils import timezone

from apps.education.models import Group

from .models import Contact, Profile

# Фото профиля: не больше 5 МБ, только JPEG/PNG/WebP.
MAX_PHOTO_SIZE = 5 * 1024 * 1024
PHOTO_CONTENT_TYPES = ('image/jpeg', 'image/png', 'image/webp')
PHOTO_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp')


class ProfilePhotoInput(forms.ClearableFileInput):
    """ClearableFileInput со своей разметкой «текущее фото / удалить»."""

    template_name = 'accounts/widgets/clearable_file_input.html'


class ProfileEditForm(forms.ModelForm):
    """Поля профиля и учётной записи, которые пользователь меняет сам.

    ``User`` не является моделью формы: имя, фамилия и почта объявлены
    отдельными полями, а переносятся в пользователя через ``save_user()`` —
    вьюха делает это в одной транзакции с сохранением профиля.
    """

    last_name = forms.CharField(
        label='Фамилия', required=False, max_length=150,
        widget=forms.TextInput(attrs={'class': 'input', 'autocomplete': 'family-name'}),
    )
    first_name = forms.CharField(
        label='Имя', required=False, max_length=150,
        widget=forms.TextInput(attrs={'class': 'input', 'autocomplete': 'given-name'}),
    )
    email = forms.EmailField(
        label='Почта', required=True,
        widget=forms.EmailInput(attrs={'class': 'input', 'autocomplete': 'email'}),
        error_messages={
            'required': 'Укажите почту — на неё приходят уведомления и вход в кабинет.',
            'invalid': 'Укажите корректный адрес почты, например name@example.ru.',
        },
    )
    group = forms.ModelChoiceField(
        label='Группа',
        queryset=Group.objects.order_by('code'),
        required=False,
        empty_label='— не выбрана —',
        widget=forms.Select(attrs={'class': 'input', 'data-searchable': 'true'}),
        error_messages={'invalid_choice': 'Выберите группу из списка.'},
    )
    photo = forms.ImageField(
        label='Новое фото',
        required=False,
        widget=ProfilePhotoInput(
            attrs={
                'class': 'input profile-edit__file',
                'accept': 'image/jpeg,image/png,image/webp',
            },
        ),
        help_text='JPEG, PNG или WebP, размер — не больше 5 МБ.',
        error_messages={
            'invalid_image': 'Загрузите изображение в формате JPEG, PNG или WebP.',
        },
    )
    institute_override = forms.CharField(
        label='Институт', required=False, max_length=120,
        widget=forms.TextInput(attrs={'class': 'input'}),
        help_text='если институт не совпадает с группой',
        error_messages={'max_length': 'Название института слишком длинное (до 120 символов).'},
    )
    enrollment_year = forms.IntegerField(
        label='Год поступления', required=False,
        min_value=1990, max_value=timezone.now().year + 1,
        widget=forms.NumberInput(attrs={'class': 'input', 'inputmode': 'numeric', 'placeholder': '2024'}),
        error_messages={
            'invalid': 'Укажите год цифрами, например 2024.',
            'min_value': 'Год поступления не может быть раньше 1990.',
            'max_value': 'Год поступления не может быть позже следующего года.',
        },
    )

    class Meta:
        model = Profile
        fields = (
            'photo', 'group', 'subgroup', 'phone', 'department',
            'institute_override', 'record_book', 'enrollment_year',
        )
        widgets = {
            'subgroup': forms.Select(attrs={'class': 'input'}),
            'phone': forms.TextInput(attrs={'class': 'input', 'autocomplete': 'tel', 'placeholder': '+7 900 000-00-00'}),
            'department': forms.TextInput(attrs={'class': 'input'}),
            'record_book': forms.TextInput(attrs={'class': 'input'}),
        }
        labels = {
            'subgroup': 'Подгруппа',
            'phone': 'Телефон',
            'department': 'Кафедра',
            'record_book': 'Зачётная книжка',
        }
        help_texts = {
            'subgroup': 'Если в расписании группа делится на «а» и «б».',
            'record_book': 'Номер зачётной книжки — если он у вас есть.',
        }
        error_messages = {
            'group': {'invalid_choice': 'Выберите группу из списка.'},
            'enrollment_year': {'invalid': 'Укажите год цифрами, например 2024.'},
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user is not None:
            if not self.is_bound:
                self.fields['last_name'].initial = user.last_name
                self.fields['first_name'].initial = user.first_name
            self.fields['email'].initial = user.email

        # Поля, созданные из модели, получают тот же класс, что и объявленные.
        for name in ('subgroup', 'phone', 'department', 'institute_override', 'record_book', 'enrollment_year'):
            field = self.fields.get(name)
            if field is not None:
                classes = field.widget.attrs.get('class', '')
                if 'input' not in classes.split():
                    field.widget.attrs['class'] = (classes + ' input').strip()

        # Подсказка поля остаётся связанной с input для скринридеров.
        if self.auto_id and '%s' in self.auto_id:
            for name, field in self.fields.items():
                if field.help_text:
                    field.widget.attrs.setdefault(
                        'aria-describedby', (self.auto_id % name) + '-hint'
                    )

    def clean_photo(self):
        """Ограничения фото: размер и тип файла — до сохранения в MEDIA_ROOT."""
        photo = self.cleaned_data.get('photo')
        if not isinstance(photo, UploadedFile):
            return photo
        if photo.size > MAX_PHOTO_SIZE:
            raise forms.ValidationError(
                'Файл больше 5 МБ. Сожмите фотографию или выберите файл поменьше.'
            )
        content_type = (getattr(photo, 'content_type', '') or '').lower()
        name = (photo.name or '').lower()
        if content_type:
            if content_type not in PHOTO_CONTENT_TYPES:
                raise forms.ValidationError('Поддерживаются только JPEG, PNG и WebP.')
        elif not name.endswith(PHOTO_EXTENSIONS):
            raise forms.ValidationError('Поддерживаются только JPEG, PNG и WebP.')
        return photo

    def clean_email(self):
        """Почта — логин в кабинет, поэтому она уникальна без учёта регистра."""
        email = (self.cleaned_data.get('email') or '').strip()
        if not email:
            raise forms.ValidationError('Укажите почту — на неё приходят уведомления.')
        duplicate = User.objects.filter(email__iexact=email)
        if self.user is not None and self.user.pk:
            duplicate = duplicate.exclude(pk=self.user.pk)
        if duplicate.exists():
            raise forms.ValidationError('На эту почту уже зарегистрирован другой аккаунт.')
        return email

    def clean_last_name(self):
        return (self.cleaned_data.get('last_name') or '').strip()

    def clean_first_name(self):
        return (self.cleaned_data.get('first_name') or '').strip()

    def save_user(self):
        """Переносит ФИО и почту в ``User``; вызывается внутри транзакции вьюхи."""
        if self.user is None:
            raise ValueError('ProfileEditForm создана без user — сохранять некуда.')
        user = self.user
        user.last_name = self.cleaned_data['last_name']
        user.first_name = self.cleaned_data['first_name']
        user.email = self.cleaned_data['email']
        user.save(update_fields=['last_name', 'first_name', 'email'])
        return user


# --- Доп. контакты -----------------------------------------------------------
# Телефон: цифры, «+», пробелы, дефисы и скобки — без букв.
PHONE_RE = re.compile(r'^\+?[\d\s()\-]+$')
# Telegram: @ник или ссылка t.me/ник.
TELEGRAM_NICK_RE = re.compile(r'^@[A-Za-z0-9_]{4,32}$')
TELEGRAM_URL_RE = re.compile(r'^(?:https?://)?(?:www\.)?t(?:elegram)?\.me/[A-Za-z0-9_]{4,32}/?$', re.IGNORECASE)
# ВКонтакте: @ник или ссылка vk.com/ник.
VK_NICK_RE = re.compile(r'^@[A-Za-z0-9_.]{2,32}$')
VK_URL_RE = re.compile(r'^(?:https?://)?(?:m\.|www\.)?vk\.com/[A-Za-z0-9_.]{2,64}/?$', re.IGNORECASE)
# WhatsApp: номер в international-формате (wa.me/…) — как телефон или ссылка.
WHATSAPP_URL_RE = re.compile(r'^(?:https?://)?(?:www\.)?wa\.me/\d{5,15}/?$', re.IGNORECASE)


def normalize_phone(value):
    """Оставляет в телефоне только «+» и цифры — для сравнения и tel:-ссылок."""
    digits = re.sub(r'\D', '', value)
    return ('+' if value.strip().startswith('+') else '') + digits


class ContactForm(forms.ModelForm):
    """Контакт пользователя: тип, значение, подпись и признак «основной».

    Значение проверяется по типу: телефон — цифры и разделители, почта —
    ``EmailField``, Telegram/VK — @ник или ссылка. Число контактов не
    ограничено, поэтому уникальность значения не требуется.
    """

    class Meta:
        model = Contact
        fields = ('kind', 'value', 'label', 'is_primary')
        widgets = {
            'kind': forms.Select(attrs={'class': 'input'}),
            'value': forms.TextInput(attrs={
                'class': 'input', 'maxlength': 200, 'autocomplete': 'off',
                'placeholder': '+7 900 000-00-00, name@example.ru или @nickname',
            }),
            'label': forms.TextInput(attrs={
                'class': 'input', 'maxlength': 60, 'placeholder': 'например, «личный»',
            }),
            'is_primary': forms.CheckboxInput(attrs={'class': 'contact-form__check'}),
        }
        labels = {
            'kind': 'Тип',
            'value': 'Контакт',
            'label': 'Подпись',
            'is_primary': 'Основной контакт',
        }
        error_messages = {
            'value': {
                'required': 'Укажите значение контакта.',
                'max_length': 'Контакт слишком длинный — не больше 200 символов.',
            },
            'kind': {'invalid_choice': 'Выберите тип контакта из списка.'},
            'label': {'max_length': 'Подпись слишком длинная — не больше 60 символов.'},
        }

    def clean_value(self):
        """Проверка значения по выбранному типу; возвращает строку без пробелов по краям."""
        value = (self.cleaned_data.get('value') or '').strip()
        if not value:
            raise forms.ValidationError('Укажите значение контакта.')
        if len(value) > 200:
            raise forms.ValidationError('Контакт слишком длинный — не больше 200 символов.')

        kind = self.cleaned_data.get('kind') or Contact.KIND_PHONE
        if kind == Contact.KIND_PHONE:
            if not PHONE_RE.fullmatch(value) or len(re.sub(r'\D', '', value)) < 5:
                raise forms.ValidationError(
                    'Телефон может содержать только цифры, «+», пробелы, дефисы и скобки — '
                    'не меньше 5 цифр, например +7 900 000-00-00.'
                )
        elif kind == Contact.KIND_EMAIL:
            try:
                validate_email(value)
            except ValidationError:
                raise forms.ValidationError('Укажите корректный адрес почты, например name@example.ru.')
        elif kind == Contact.KIND_TELEGRAM:
            if not (TELEGRAM_NICK_RE.fullmatch(value) or TELEGRAM_URL_RE.fullmatch(value)):
                raise forms.ValidationError(
                    'Telegram — это @ник (от 4 символов: латиница, цифры, «_») или ссылка t.me/ник.'
                )
        elif kind == Contact.KIND_VK:
            if not (VK_NICK_RE.fullmatch(value) or VK_URL_RE.fullmatch(value)):
                raise forms.ValidationError(
                    'ВКонтакте — это @ник или ссылка vk.com/ник.'
                )
        elif kind == Contact.KIND_WHATSAPP:
            phone_ok = PHONE_RE.fullmatch(value) and len(re.sub(r'\D', '', value)) >= 5
            if not (phone_ok or WHATSAPP_URL_RE.fullmatch(value)):
                raise forms.ValidationError(
                    'WhatsApp — это номер телефона или ссылка wa.me/79990000000.'
                )
        return value

    def clean_label(self):
        return (self.cleaned_data.get('label') or '').strip()


class TicketForm(forms.ModelForm):
    """Поля студенческого билета, которые студент может заполнить сам.

    Все поля необязательны: билет заполняет деканат, но если данных нет,
    студент вправе внести их сам. Номер билета и зачётную книжку форма
    не трогает — они живут в профиле и редактируются отдельно.
    """

    class Meta:
        model = Profile
        fields = ('ticket_order', 'ticket_issue_date', 'education_level', 'study_form', 'direction')
        widgets = {
            'ticket_order': forms.TextInput(attrs={
                'class': 'input', 'maxlength': 60, 'placeholder': '№ 1234-с от 01.09.2025',
            }),
            'ticket_issue_date': forms.DateInput(
                attrs={'class': 'input', 'type': 'date'}, format='%Y-%m-%d',
            ),
            'education_level': forms.Select(attrs={'class': 'input'}),
            'study_form': forms.Select(attrs={'class': 'input'}),
            'direction': forms.TextInput(attrs={'class': 'input', 'maxlength': 160}),
        }
        labels = {
            'ticket_order': 'Приказ о зачислении',
            'ticket_issue_date': 'Дата выдачи билета',
            'education_level': 'Уровень образования',
            'study_form': 'Форма обучения',
            'direction': 'Направление подготовки',
        }
        error_messages = {
            'ticket_issue_date': {'invalid': 'Укажите дату в формате ДД.ММ.ГГГГ.'},
            'education_level': {'invalid_choice': 'Выберите уровень образования из списка.'},
            'study_form': {'invalid_choice': 'Выберите форму обучения из списка.'},
            'ticket_order': {'max_length': 'Приказ слишком длинный — не больше 60 символов.'},
            'direction': {'max_length': 'Направление слишком длинное — не больше 160 символов.'},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Пустой вариант для необязательных селектов: «— не указано —».
        for name in ('education_level', 'study_form'):
            field = self.fields.get(name)
            if field is not None:
                field.choices = [('', '— не указано —')] + [
                    choice for choice in field.choices if choice[0]
                ]
