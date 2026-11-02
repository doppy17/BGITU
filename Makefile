# Запуск проекта одной командой
run:
	./run.sh

# Проверки
test:
	cd "ЛК-портал" && .venv/bin/python manage.py test

check:
	cd "ЛК-портал" && .venv/bin/python manage.py check

# Пересоздать окружение с нуля (если .venv сломан)
setup:
	rm -rf "ЛК-портал/.venv"
	./run.sh

# Полный сброс демо-БД и медиа, затем запуск
reset:
	rm -f "ЛК-портал/db.sqlite3"
	rm -rf "ЛК-портал/media/works" "ЛК-портал/media/materials" "ЛК-портал/media/schedule"
	./run.sh

.PHONY: run test check setup reset
