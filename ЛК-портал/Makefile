# ЛК БГИТУ — типовые команды.
# Интерпретатор: сначала LK_VENV (рабочая копия вне iCloud), затем .venv, затем python3 —
# берётся первый, в котором есть Django. .venv в ~/Documents под iCloud ломается (Resource deadlock).
LK_VENV ?= $(HOME)/.hermes/cache/scratch/lk-run/.venv
PY ?= $(shell for p in "$(LK_VENV)/bin/python" .venv/bin/python python3 python; do \
        $$p -c 'import django' >/dev/null 2>&1 && { echo $$p; break; }; done)
PORT ?= 8010
UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.hermes/bin/uv)

.PHONY: venv install migrate seed run test check clean

venv:
	$(UV) venv --python 3.11 $(LK_VENV) || python3.11 -m venv $(LK_VENV)

install: venv
	$(UV) pip install --python $(PY) -r requirements.txt

migrate:
	$(PY) manage.py migrate

seed:
	$(PY) manage.py seed_demo

run:
	./run.sh

test:
	$(PY) manage.py test

check:
	$(PY) manage.py check

clean:
	rm -f db.sqlite3
	rm -rf media staticfiles
