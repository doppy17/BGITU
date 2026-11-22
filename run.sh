#!/bin/bash
# Запуск ЛК БГИТУ одной командой: ./run.sh
#   - берёт следующий свободный порт (начиная с .run-port или 8030), +1 при каждом старте
#   - поднимает Django из проекта, без autoreload
#   - открывает браузер
# Аргументы: run.sh [порт] [--no-open]
set -euo pipefail

cd "$(dirname "$0")"

VENV="${LK_VENV:-$HOME/.hermes/cache/scratch/lk-run/.venv}"
PY="$VENV/bin/python"
if [ ! -x "$PY" ]; then
  echo "Не найден python в $VENV — задайте LK_VENV=/путь/к/.venv" >&2
  exit 1
fi

PORT_FILE=".run-port"
LAST=$(cat "$PORT_FILE" 2>/dev/null || echo 8029)
case "$LAST" in ''|*[!0-9]*) LAST=8029;; esac
PORT=$((LAST + 1))
OPEN=1
for a in "$@"; do
  case "$a" in
    --no-open) OPEN=0 ;;
    ''|*[!0-9]*) : ;;
    *) PORT="$a" ;;
  esac
done

# если порт занят — идём дальше
while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

echo "$PORT" > "$PORT_FILE"
echo "ЛК БГИТУ → http://127.0.0.1:$PORT  (Ctrl+C — остановить)"
[ "$OPEN" = "1" ] && (sleep 1.2; open "http://127.0.0.1:$PORT" >/dev/null 2>&1 &)

export PYTHONDONTWRITEBYTECODE=1
exec "$PY" manage.py runserver "127.0.0.1:$PORT" --noreload
