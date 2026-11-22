from django.db import migrations, models


CURRICULUM = [
    # (дисциплина, с семестра, по семестр) — учебный план 09.03.02 (демо)
    ('Математический анализ', 1, 2),
    ('Линейная алгебра и аналитическая геометрия', 1, 1),
    ('Дискретная математика', 1, 1),
    ('Информатика', 1, 1),
    ('Основы программирования', 1, 2),
    ('История', 1, 1),
    ('Иностранный язык', 1, 4),
    ('Физическая культура', 1, 6),
    ('Теория вероятностей и математическая статистика', 2, 2),
    ('Физика', 2, 2),
    ('Архитектура компьютеров', 2, 2),
    ('Объектно-ориентированное программирование', 3, 3),
    ('Базы данных', 3, 4),
    ('Прикладная разработка на Python', 3, 3),
    ('JavaScript-разработка', 3, 3),
    ('Философия', 3, 3),
    ('Экономика', 3, 3),
    ('Вычислительные системы и сети', 4, 4),
    ('Web-технологии', 4, 4),
    ('Операционные системы', 4, 4),
    ('Алгоритмы и структуры данных', 4, 4),
    ('Проектирование информационных систем', 5, 5),
    ('Компьютерная графика', 5, 5),
    ('Тестирование программного обеспечения', 5, 5),
    ('Управление проектами', 5, 5),
    ('Безопасность жизнедеятельности', 5, 5),
    ('Искусственный интеллект и машинное обучение', 6, 6),
    ('Большие данные', 6, 6),
    ('Облачные технологии', 6, 6),
    ('Разработка мобильных приложений', 6, 6),
    ('Экономика предприятия', 6, 6),
    ('Системы поддержки принятия решений', 7, 7),
    ('Проектный практикум', 7, 8),
    ('Преддипломная практика', 8, 8),
    ('Выпускная квалификационная работа', 8, 8),
    ('Государственный экзамен', 8, 8),
]


def seed_curriculum(apps, schema_editor):
    Entry = apps.get_model('education', 'CurriculumEntry')
    Entry.objects.bulk_create([
        Entry(code='ist', subject=subject, semester_from=start, semester_to=end)
        for subject, start, end in CURRICULUM
    ])


def unseed_curriculum(apps, schema_editor):
    apps.get_model('education', 'CurriculumEntry').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('education', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='CurriculumEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(default='ist', max_length=16, verbose_name='направление')),
                ('subject', models.CharField(max_length=200, verbose_name='дисциплина')),
                ('semester_from', models.PositiveSmallIntegerField(default=1, verbose_name='с семестра')),
                ('semester_to', models.PositiveSmallIntegerField(default=1, verbose_name='по семестр')),
            ],
            options={
                'verbose_name': 'учебный план',
                'verbose_name_plural': 'учебный план',
                'ordering': ['semester_from', 'subject'],
            },
        ),
        migrations.RunPython(seed_curriculum, unseed_curriculum),
    ]
