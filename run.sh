#!/usr/bin/env bash
# Запуск ЛК БГИТУ одной командой: ./run.sh [--port 8010] [--no-seed]
# Сам создаёт окружение, ставит зависимости, применяет миграции и поднимает сервер.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$ROOT/ЛК-портал"
PORT="${PORT:-8010}"
SEED=1
SETUP_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --no-seed) SEED=0; shift ;;
    --setup-only) SETUP_ONLY=1; shift ;;
    -h|--help) echo "usage: ./run.sh [--port N] [--no-seed] [--setup-only]"; exit 0 ;;
    *) echo "неизвестный аргумент: $1"; exit 2 ;;
  esac
done

PY="$APP/.venv/bin/python"
UV="$(command -v uv 2>/dev/null || true)"
[ -z "$UV" ] && [ -x "$HOME/.hermes/bin/uv" ] && UV="$HOME/.hermes/bin/uv"

# порт занят прошлым запуском — берём следующий свободный
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  for p in $(seq $((PORT + 1)) $((PORT + 9))); do
    if ! lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "→ порт $PORT занят, беру $p"
      PORT="$p"; break
    fi
  done
fi

# 1. окружение
if [ ! -x "$PY" ]; then
  echo "→ создаю .venv (Python 3.11)…"
  if [ -n "$UV" ]; then
    "$UV" venv --python 3.11 "$APP/.venv"
  else
    python3.11 -m venv "$APP/.venv"
  fi
fi

# 2. зависимости
if ! "$PY" -c "import django" >/dev/null 2>&1; then
  echo "→ ставлю зависимости из requirements.txt…"
  if [ -n "$UV" ]; then
    "$UV" pip install --python "$PY" -r "$APP/requirements.txt"
  else
    "$PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
    "$PY" -m pip install -r "$APP/requirements.txt"
  fi
fi

cd "$APP"

# 3. база
FRESH=0
[ -f db.sqlite3 ] || FRESH=1
"$PY" manage.py migrate --noinput >/dev/null
if [ "$SEED" -eq 1 ] && [ "$FRESH" -eq 1 ]; then
  echo "→ заполняю демо-данными (роли, пользователи, курсы, расписание)…"
  "$PY" manage.py seed_demo
fi

if [ "$SETUP_ONLY" -eq 1 ]; then
  echo "→ окружение готово (запуск: ./run.sh)"
  exit 0
fi

echo "→ портал: http://127.0.0.1:$PORT/   (демо-пароль: demo12345)"
exec "$PY" manage.py runserver "127.0.0.1:$PORT"
