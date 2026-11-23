#!/usr/bin/env bash
# Сборка на Render: зависимости → статика → миграции → демо-данные.
set -euo pipefail

pip install --no-cache-dir -r requirements.txt

python manage.py collectstatic --noinput
python manage.py migrate --noinput

# Идемпотентно: создаёт роли, пользователей, курсы и расписание, если их ещё нет.
python manage.py seed_demo
