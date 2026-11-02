VENV ?= .venv
PY ?= $(VENV)/bin/python
PORT ?= 8010
UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.hermes/bin/uv)

.PHONY: venv install migrate seed run test check clean

venv:
	$(UV) venv --python 3.11 $(VENV) || python3.11 -m venv $(VENV)

install: venv
	$(UV) pip install --python $(PY) -r requirements.txt

migrate:
	$(PY) manage.py migrate

seed:
	$(PY) manage.py seed_demo

run:
	$(PY) manage.py runserver 127.0.0.1:$(PORT)

test:
	$(PY) manage.py test

check:
	$(PY) manage.py check

clean:
	rm -f db.sqlite3
	rm -rf media staticfiles
