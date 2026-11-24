"""Профиль студента: фото, год поступления, кафедра, курс и история перемещений."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_alter_profile_theme'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='course_override',
            field=models.PositiveSmallIntegerField(
                blank=True, null=True, verbose_name='курс (если не совпадает с группой)'
            ),
        ),
        migrations.AddField(
            model_name='profile',
            name='department',
            field=models.CharField(blank=True, max_length=160, verbose_name='кафедра'),
        ),
        migrations.AddField(
            model_name='profile',
            name='enrollment_year',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='год поступления'),
        ),
        migrations.AddField(
            model_name='profile',
            name='photo',
            field=models.ImageField(blank=True, upload_to='profiles/', verbose_name='фото'),
        ),
        migrations.CreateModel(
            name='ProfileHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='создана')),
                (
                    'kind',
                    models.CharField(
                        choices=[
                            ('group', 'группа'),
                            ('institute', 'институт'),
                            ('program', 'направление'),
                            ('course', 'курс'),
                        ],
                        default='group',
                        max_length=16,
                        verbose_name='тип изменения',
                    ),
                ),
                ('from_value', models.CharField(blank=True, default='', max_length=160, verbose_name='было')),
                ('to_value', models.CharField(blank=True, default='', max_length=160, verbose_name='стало')),
                ('comment', models.TextField(blank=True, verbose_name='комментарий')),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='profile_history',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='пользователь',
                    ),
                ),
            ],
            options={
                'verbose_name': 'история профиля',
                'verbose_name_plural': 'история профиля',
                'ordering': ['-created_at', '-pk'],
            },
        ),
    ]
