"""Вход, роли, заявки на роли, управление пользователями."""
import hashlib
import re

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.views import PasswordChangeView
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Count, Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.education.models import Group

from .forms import ContactForm, ProfileEditForm, ProfileFactsForm, TicketForm
from .models import Contact, Profile, Role, RoleRequest
from .permissions import get_role, role_can, role_required

def username_from_email(email):
    """Логин выводится из почты — отдельный логин пользователь не придумывает."""
    candidate = email.strip().lower()
    try:
        from django.contrib.auth.validators import UnicodeUsernameValidator
        UnicodeUsernameValidator()(candidate)
    except ValidationError:
        digest = hashlib.sha256(candidate.encode()).hexdigest()[:10]
        candidate = 'user-' + digest
    if len(candidate) > 150:
        digest = hashlib.sha256(candidate.encode()).hexdigest()[:10]
        candidate = candidate[:139] + '-' + digest
    return candidate


def unique_username(base):
    """Логин из почты должен быть уникальным: при совпадении добавляем -2, -3…"""
    candidate = base
    suffix = 1
    while User.objects.filter(username__iexact=candidate).exists():
        suffix += 1
        tail = f'-{suffix}'
        candidate = f'{base[:150 - len(tail)]}{tail}'
    return candidate


def login_view(request):
    if request.user.is_authenticated:
        return redirect('portal:dashboard')
    if request.method == 'POST':
        identifier = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = None
        if '@' in identifier:
            found = User.objects.filter(email__iexact=identifier).first()
            if found:
                user = authenticate(request, username=found.username, password=password)
        if user is None:
            user = authenticate(request, username=identifier, password=password)
        if user is None:
            messages.error(request, 'Неверная почта/логин или пароль.')
        else:
            login(request, user)
            next_url = request.POST.get('next') or request.GET.get('next') or '/cabinet/'
            if not next_url.startswith('/') or next_url.startswith('//'):
                next_url = '/cabinet/'
            return redirect(next_url)
    return render(request, 'accounts/login.html', {'next': request.GET.get('next', '')})


def register_view(request):
    """Самостоятельная регистрация: профиль создаётся сразу, роль — «Студент».

    Группу при регистрации не спрашиваем: её назначают позже в кабинете,
    поэтому profile.group остаётся пустой. Присланное поле group игнорируем.
    """
    if request.user.is_authenticated:
        return redirect('portal:dashboard')

    if request.method == 'POST':
        data = {
            field: request.POST.get(field, '').strip()
            for field in ('last_name', 'first_name', 'email')
        }
        password1 = request.POST.get('password1', '')
        password2 = request.POST.get('password2', '')
        errors = []

        # Имя и фамилия необязательны — их можно добавить позже в профиле.
        try:
            validate_email(data['email'])
        except ValidationError:
            errors.append('Укажите корректный e-mail.')
        else:
            if User.objects.filter(email__iexact=data['email']).exists():
                errors.append('На этот e-mail уже зарегистрирован кабинет.')

        group = None  # группу на регистрации не указывают — POST-поле group игнорируем

        if len(password1) < 8:
            errors.append('Пароль должен быть не короче 8 символов.')
        elif password1 != password2:
            errors.append('Пароли не совпадают.')
        else:
            try:
                validate_password(password1)
            except ValidationError as error:
                errors.extend(error.messages)

        if not errors:
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=unique_username(username_from_email(data['email'])),
                        email=data['email'],
                        password=password1,
                        first_name=data['first_name'],
                        last_name=data['last_name'],
                    )
                    # Профиль создаётся всегда — даже если сигнал почему-то не сработал.
                    profile, _ = Profile.objects.get_or_create(user=user)
                    profile.role = Role.objects.filter(code='student').first()
                    profile.group = group
                    profile.save(update_fields=['role', 'group'])
            except IntegrityError:
                errors.append('Не удалось создать аккаунт: такой e-mail уже используется.')
            else:
                login(request, user)
                messages.success(request, 'Аккаунт создан.')
                return redirect('portal:dashboard')

        return render(request, 'accounts/register.html', {
            'form_data': data,
            'errors': errors,
        })

    return render(request, 'accounts/register.html', {'form_data': {}, 'errors': []})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, 'Вы вышли из кабинета.')
    return redirect('accounts:login')


@login_required
def roles_view(request):
    roles = Role.objects.annotate(users_count=Count('profiles'))
    profile = getattr(request.user, 'profile', None)
    my_requests = RoleRequest.objects.filter(user=request.user).select_related('role')[:10]
    pending = RoleRequest.objects.filter(status=RoleRequest.STATUS_PENDING).count() if role_can(
        request.user, 'can_approve_roles'
    ) else 0
    return render(request, 'accounts/roles.html', {
        'roles': roles,
        'profile': profile,
        'my_requests': my_requests,
        'pending_count': pending,
        'can_manage_roles': role_can(request.user, 'can_manage_roles'),
        'can_approve_roles': role_can(request.user, 'can_approve_roles'),
    })


@login_required
@require_POST
def role_request_create(request, role_id):
    role = get_object_or_404(Role, pk=role_id)
    profile = getattr(request.user, 'profile', None)
    if profile and profile.role_id == role.id:
        messages.info(request, f'Роль «{role.name}» уже назначена.')
        return redirect('accounts:roles')
    if RoleRequest.objects.filter(user=request.user, role=role, status=RoleRequest.STATUS_PENDING).exists():
        messages.info(request, f'Заявка на роль «{role.name}» уже на рассмотрении.')
        return redirect('accounts:roles')
    RoleRequest.objects.create(
        user=request.user,
        role=role,
        comment=request.POST.get('comment', '').strip()[:1000],
    )
    messages.success(request, f'Заявка на роль «{role.name}» отправлена.')
    return redirect('accounts:roles')


@login_required
def role_requests_view(request):
    can_review = role_can(request.user, 'can_approve_roles')
    if can_review:
        requests_qs = RoleRequest.objects.select_related('user', 'role', 'reviewed_by')
    else:
        requests_qs = RoleRequest.objects.filter(user=request.user).select_related('role')
    return render(request, 'accounts/role_requests.html', {
        'requests': requests_qs,
        'can_review': can_review,
        'pending_count': requests_qs.filter(status=RoleRequest.STATUS_PENDING).count(),
    })


@login_required
@require_POST
def role_request_review(request, request_id, action):
    if not role_can(request.user, 'can_approve_roles'):
        messages.error(request, 'Недостаточно прав для рассмотрения заявок.')
        return redirect('accounts:role_requests')
    role_request = get_object_or_404(RoleRequest.objects.select_related('role', 'user'), pk=request_id)
    if role_request.status != RoleRequest.STATUS_PENDING:
        messages.info(request, 'Заявка уже рассмотрена.')
        return redirect('accounts:role_requests')
    if role_request.role.is_developer and not role_can(request.user, 'is_developer'):
        messages.error(request, 'Роль разработчика может выдавать только разработчик.')
        return redirect('accounts:role_requests')
    if action not in ('approve', 'reject'):
        messages.error(request, 'Неизвестное действие.')
        return redirect('accounts:role_requests')

    role_request.status = RoleRequest.STATUS_APPROVED if action == 'approve' else RoleRequest.STATUS_REJECTED
    role_request.reviewed_at = timezone.now()
    role_request.reviewed_by = request.user
    role_request.review_comment = request.POST.get('review_comment', '').strip()[:1000]
    role_request.save()

    if action == 'approve':
        profile, _ = Profile.objects.get_or_create(user=role_request.user)
        profile.role = role_request.role
        profile.save(update_fields=['role'])
        messages.success(request, f'Роль «{role_request.role.name}» выдана пользователю {role_request.user}.')
    else:
        messages.info(request, 'Заявка отклонена.')
    return redirect('accounts:role_requests')


@login_required
@role_required('can_manage_roles')
def role_create(request):
    is_developer = role_can(request.user, 'is_developer')
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().lower()
        description = request.POST.get('description', '').strip()
        errors = []
        if len(name) < 2:
            errors.append('Укажите название роли.')
        if not re.fullmatch(r'[a-z][a-z0-9_]{2,39}', code):
            errors.append('Код: латиница, цифры и подчёркивание, от 3 до 40 символов.')
        if Role.objects.filter(code=code).exists():
            errors.append('Роль с таким кодом уже есть.')
        flags = {}
        for field in (
            'can_manage_users', 'can_manage_roles', 'can_manage_schedule', 'can_create_courses',
            'can_grade', 'can_upload_works', 'can_view_all', 'is_developer',
        ):
            flags[field] = request.POST.get(field) == 'on'
        if flags['is_developer'] and not is_developer:
            errors.append('Только разработчик может создать роль с полным доступом.')
        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, 'accounts/role_form.html', {
                'form_data': request.POST,
                'is_developer': is_developer,
            })
        Role.objects.create(name=name, code=code, description=description, is_system=False, **flags)
        messages.success(request, f'Роль «{name}» создана.')
        return redirect('accounts:roles')
    return render(request, 'accounts/role_form.html', {'is_developer': is_developer})


@login_required
@role_required('can_manage_users')
def manage_students(request):
    query = request.GET.get('q', '').strip()
    profiles = Profile.objects.select_related('user', 'role', 'group').order_by('group__code', 'user__last_name')
    if query:
        profiles = profiles.filter(user__username__icontains=query) | profiles.filter(
            user__last_name__icontains=query
        ) | profiles.filter(user__first_name__icontains=query)
        profiles = profiles.distinct()
    return render(request, 'accounts/students.html', {
        'profiles': profiles,
        'groups': Group.objects.all(),
        'roles': Role.objects.all(),
        'query': query,
        'is_developer': role_can(request.user, 'is_developer'),
    })


@login_required
@role_required('can_manage_users')
@require_POST
def manage_student_update(request, user_id):
    profile = get_object_or_404(Profile.objects.select_related('user', 'role'), user__id=user_id)
    role_id = request.POST.get('role')
    group_id = request.POST.get('group') or None
    record_book = request.POST.get('record_book', '').strip()[:32]

    role = Role.objects.filter(pk=role_id).first() if role_id else None
    if role is None:
        messages.error(request, 'Выберите роль из списка.')
        return redirect('accounts:students')
    if role.is_developer and not role_can(request.user, 'is_developer'):
        messages.error(request, 'Роль разработчика может назначать только разработчик.')
        return redirect('accounts:students')
    group = Group.objects.filter(pk=group_id).first() if group_id else None
    profile.role = role
    profile.group = group
    profile.record_book = record_book
    profile.save(update_fields=['role', 'group', 'record_book'])
    messages.success(request, f'Профиль {profile.display_name} обновлён.')
    return redirect('accounts:students')


# Допустимые темы оформления сайта.
THEMES = ('paper', 'surik', 'latun', 'brass')

# --- Профиль: блок 01 «Данные студента» редактируется прямо на странице -----
INLINE_PROFILE_SCOPE = 'profile-facts'


def profile_facts_payload(profile):
    """Данные блока 01 для JSON-ответа — с учётом вычисленных свойств."""
    return {
        'institute': profile.institute,
        'institute_override': profile.institute_override,
        'group': profile.group.code if profile.group_id else '',
        'group_id': profile.group_id,
        'subgroup': profile.subgroup,
        'course': profile.course,
        'enrollment_year': profile.enrollment_year,
        'graduation_year': profile.graduation_year,
        'direction': profile.direction_display,
    }


def render_profile_facts(request, profile):
    """HTML блока 01 в режиме просмотра — им JS заменяет блок после сохранения."""
    return render_to_string(
        'portal/_profile_facts.html',
        {
            'profile': profile,
            'p': profile,
            'facts_form': ProfileFactsForm(instance=profile, user=request.user),
        },
        request=request,
    )


@login_required
def profile_edit(request):
    """Редактирование собственного профиля: фото, группа и контакты.

    Пользователь меняет только свои данные; ФИО и почта живут в ``User``,
    остальное — в ``Profile``. Обе записи сохраняются в одной транзакции,
    чтобы не осталось полу-обновлённого профиля. Смена группы попадает в
    ``ProfileHistory`` через сигнал ``pre_save`` (apps/accounts/signals.py).

    Блок 01 профиля редактируется на месте: та же вьюха принимает POST с
    ``scope=profile-facts`` (только пять полей) и на AJAX отвечает JSON с
    готовым HTML блока, а без JS — обычным переходом в профиль.
    """
    profile, _ = Profile.objects.get_or_create(user=request.user)
    inline = (request.POST.get('scope') or '').strip() == INLINE_PROFILE_SCOPE
    if request.method == 'POST':
        form_class = ProfileFactsForm if inline else ProfileEditForm
        form = form_class(request.POST, request.FILES, instance=profile, user=request.user)
        if form.is_valid():
            with transaction.atomic():
                if not inline:
                    form.save_user()
                profile = form.save()
            message = 'Данные студента обновлены.' if inline else 'Профиль обновлён.'
            if inline:
                if wants_json(request):
                    return JsonResponse({
                        'ok': True,
                        'message': message,
                        'profile': profile_facts_payload(profile),
                        'html': render_profile_facts(request, profile),
                    })
                messages.success(request, message)
                return redirect('portal:profile')
            messages.success(request, message)
            return redirect('portal:profile')

        errors = form_error_messages(form)
        if inline:
            # AJAX получает ошибки по полям и остаётся в режиме правки;
            # без JS (fallback) — обычный переход с сообщениями.
            if wants_json(request):
                return JsonResponse({
                    'ok': False,
                    'message': errors[0] if errors else 'Не удалось сохранить данные.',
                    'errors': errors,
                    'fields': {
                        name: [str(error) for error in field_errors]
                        for name, field_errors in form.errors.items()
                    },
                }, status=400)
            for error in errors:
                messages.error(request, error)
            return redirect('portal:profile')
    else:
        form = ProfileEditForm(instance=profile, user=request.user)
    return render(request, 'accounts/profile_edit.html', {'form': form, 'profile': profile})


@login_required
@require_POST
def theme_set(request):
    """Сохраняет выбранную тему в профиле — переносится между устройствами."""
    theme = (request.POST.get('theme') or '').strip()
    if theme not in THEMES:
        return JsonResponse({'ok': False, 'error': 'unknown theme'}, status=400)
    profile = getattr(request.user, 'profile', None)
    if profile is None:
        return JsonResponse({'ok': False, 'error': 'no profile'}, status=400)
    profile.theme = theme
    profile.save(update_fields=['theme'])
    response = JsonResponse({'ok': True, 'theme': theme})
    response.set_cookie('bgitu-theme', theme, max_age=365 * 24 * 3600, samesite='Lax')
    return response


# --- Вкладки профиля: «Доп. контакты» и «Безопасность» ----------------------

@login_required
def profile_contacts(request):
    """Вкладка «Доп. контакты»: телефон и почта для чтения.

    Значения меняются только на форме редактирования профиля
    (``accounts:profile_edit``), здесь они показываются как есть.
    """
    profile, _ = Profile.objects.get_or_create(user=request.user)
    return render(request, 'accounts/profile_contacts.html', {'profile': profile})


class ProfilePasswordChangeForm(PasswordChangeForm):
    """Пароль профиля: валидаторы Django и оформление полей кабинета."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'input'


class ProfileSecurityView(PasswordChangeView):
    """Вкладка «Безопасность»: смена своего пароля и последний вход.

    ``PasswordChangeView`` уже закрыт ``login_required`` и берёт пользователя
    только из ``request.user``, поэтому чужой пароль сменить нельзя. После
    успешной смены сессия остаётся живой (``update_session_auth_hash`` внутри
    базовой вьюхи), а остальные сессии пользователя разлогиниваются.
    """

    template_name = 'accounts/profile_security.html'
    form_class = ProfilePasswordChangeForm
    success_url = reverse_lazy('portal:profile')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Пароль изменён')
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['profile'] = getattr(self.request.user, 'profile', None)
        return context


# --- Контакты пользователя и студенческий билет -----------------------------
# Контакты живут в модели ``Contact``: их число не ограничено, один из них
# может быть основным. Вьюхи работают только со своими записями — чужие
# контакты не находятся (queryset фильтруется по ``request.user``), поэтому
# попытка удалить или сделать основной чужую запись даёт 404.

def wants_json(request):
    """Ответ JSON — когда запрос пришёл из JS (X-Requested-With) или просит JSON."""
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    return 'application/json' in request.headers.get('Accept', '')


def redirect_back(request, fallback='portal:profile'):
    """Возврат на страницу, откуда пришла форма (``next``), иначе — в профиль."""
    target = (request.POST.get('next') or '').strip()
    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(target)
    return redirect(fallback)


def contact_payload(contact):
    """Данные контакта для JSON-ответа — те же поля, что рисует партиал."""
    return {
        'id': contact.pk,
        'kind': contact.kind,
        'kind_label': contact.get_kind_display(),
        'value': contact.value,
        'label': contact.label,
        'is_primary': contact.is_primary,
        'order': contact.order,
    }


def form_error_messages(form):
    """Плоский список текстов ошибок формы — для messages и JSON."""
    errors = []
    for field_errors in form.errors.values():
        errors.extend(str(error) for error in field_errors)
    return errors


@login_required
@require_POST
def contact_add(request):
    """Добавляет контакт текущего пользователя; число контактов не ограничено."""
    form = ContactForm(request.POST)
    if not form.is_valid():
        errors = form_error_messages(form)
        if wants_json(request):
            return JsonResponse(
                {'ok': False, 'errors': errors, 'fields': form.errors.get_json_data()},
                status=400,
            )
        for error in errors:
            messages.error(request, error)
        return redirect_back(request)

    with transaction.atomic():
        # Основной контакт у пользователя один: снимаем флаг у прежнего.
        if form.cleaned_data['is_primary']:
            Contact.objects.filter(user=request.user, is_primary=True).update(is_primary=False)
        last_order = Contact.objects.filter(user=request.user).aggregate(
            last=Max('order')
        )['last'] or 0
        contact = form.save(commit=False)
        contact.user = request.user
        contact.order = last_order + 1
        contact.save()

    if wants_json(request):
        return JsonResponse({'ok': True, 'contact': contact_payload(contact)}, status=201)
    messages.success(request, f'Контакт «{contact.get_kind_display()}» добавлен.')
    return redirect_back(request)


@login_required
@require_POST
def contact_delete(request, pk):
    """Удаляет свой контакт; чужой контакт не находится — 404."""
    contact = get_object_or_404(Contact, pk=pk, user=request.user)
    label = contact.get_kind_display()
    contact.delete()
    if wants_json(request):
        return JsonResponse({'ok': True, 'deleted': pk})
    messages.success(request, f'Контакт «{label}» удалён.')
    return redirect_back(request)


@login_required
@require_POST
def contact_primary(request, pk):
    """Делает контакт основным, снимая признак с остальных (в транзакции)."""
    contact = get_object_or_404(Contact, pk=pk, user=request.user)
    with transaction.atomic():
        Contact.objects.filter(user=request.user, is_primary=True).exclude(
            pk=contact.pk
        ).update(is_primary=False)
        if not contact.is_primary:
            contact.is_primary = True
            contact.save(update_fields=['is_primary'])

    if wants_json(request):
        return JsonResponse({'ok': True, 'contact': contact_payload(contact)})
    messages.success(request, f'Контакт «{contact.get_kind_display()}» теперь основной.')
    return redirect_back(request)


@login_required
@require_POST
def ticket_save(request):
    """Сохраняет поля студенческого билета, которые студент заполняет сам.

    Билет — необязательные данные: пустая форма допустима и просто очищает
    поля. Номер билета и зачётную книжку вьюха не трогает.
    """
    profile, _ = Profile.objects.get_or_create(user=request.user)
    form = TicketForm(request.POST, instance=profile)
    if not form.is_valid():
        errors = form_error_messages(form)
        if wants_json(request):
            return JsonResponse(
                {'ok': False, 'errors': errors, 'fields': form.errors.get_json_data()},
                status=400,
            )
        for error in errors:
            messages.error(request, error)
        return redirect_back(request)

    form.save()
    if wants_json(request):
        return JsonResponse({'ok': True, 'ticket': {
            'ticket_order': profile.ticket_order,
            'ticket_issue_date': profile.ticket_issue_date.isoformat() if profile.ticket_issue_date else '',
            'education_level': profile.education_level,
            'study_form': profile.study_form,
            'direction': profile.direction,
        }})
    messages.success(request, 'Данные студенческого билета сохранены.')
    return redirect_back(request)
