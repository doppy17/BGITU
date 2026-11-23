"""Диагностика локальной LLM — только для staff."""
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from . import local_llm


@require_GET
def ai_status(request):
    if not request.user.is_staff:
        return JsonResponse({'error': 'staff only'}, status=403)
    probe = request.GET.get('probe', '1') != '0'
    return JsonResponse(local_llm.status(probe=probe))
