"""Настройки для Render: Postgres из DATABASE_URL, статика через WhiteNoise.

Запуск: DJANGO_SETTINGS_MODULE=config.settings.render gunicorn config.wsgi:application
"""
import os

import dj_database_url

from .prod import *  # noqa: F401,F403

# --- Хосты и доверенные источники -------------------------------------------
_hosts = [h.strip() for h in os.environ.get('DJANGO_ALLOWED_HOSTS', '').split(',') if h.strip()]
_render_host = os.environ.get('RENDER_EXTERNAL_HOSTNAME', '').strip()
ALLOWED_HOSTS = sorted({*_hosts, *([_render_host] if _render_host else []), '.onrender.com'})
CSRF_TRUSTED_ORIGINS = [
    f'https://{h}' for h in ALLOWED_HOSTS if h and not h.startswith('.')
] + ['https://*.onrender.com']

# Render терминирует TLS на прокси: без этого Django думает, что запрос http,
# и SECURE_SSL_REDIRECT из prod.py уводит в бесконечный редирект.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# --- База данных -------------------------------------------------------------
# Render подставляет строку подключения managed-Postgres в DATABASE_URL.
# Локально без DATABASE_URL остаётся SQLite из base.py — удобно для проверок.
_database_url = os.environ.get('DATABASE_URL', '').strip()
if _database_url:
    DATABASES = {
        'default': dj_database_url.parse(
            _database_url,
            conn_max_age=600,
            conn_health_checks=True,
        )
    }

# --- Статика -----------------------------------------------------------------
MIDDLEWARE = list(MIDDLEWARE)
if 'whitenoise.middleware.WhiteNoiseMiddleware' not in MIDDLEWARE:
    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')

STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'
WHITENOISE_MAX_AGE = 31536000 if not DEBUG else 0

# --- Загрузки ----------------------------------------------------------------
# Диск на Render эфемерный: файлы работ живут до следующего деплоя.
MEDIA_ROOT = os.environ.get('DJANGO_MEDIA_ROOT', str(BASE_DIR / 'media'))

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': 'INFO'},
}
