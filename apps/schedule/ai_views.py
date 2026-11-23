"""JSON-эндпоинт ИИ-разбора для ЛК (только staff/developer)."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from .ai_review import review_import
from .models import ScheduleImport


def can_ai_review(user):
    """ИИ-разбор видят staff, superuser и роль «разработчик»."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_staff or user.is_superuser:
        return True
    role = getattr(getattr(user, 'profile', None), 'role', None)
    return bool(role and role.is_developer)


@login_required
@require_POST
def ai_review_view(request, pk):
    """Факты считаются на сервере; резюме модели не блокирует страницу — fetch."""
    if not can_ai_review(request.user):
        raise PermissionDenied
    record = get_object_or_404(ScheduleImport, pk=pk)
    try:
        review = review_import(record, use_llm=True)
    except Exception as exc:
        return JsonResponse(
            {'ok': False, 'error': f'Не удалось разобрать файл: {exc}'},
            status=500,
            json_dumps_params={'ensure_ascii': False},
        )
    review['ok'] = True
    return JsonResponse(review, json_dumps_params={'ensure_ascii': False})
