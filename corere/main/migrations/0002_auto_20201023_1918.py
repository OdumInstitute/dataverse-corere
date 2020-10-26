# Generated by Django 2.2.15 on 2020-10-23 19:18

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0001_squashed_0003_auto_20201009_2053'),
    ]

    operations = [
        migrations.AddField(
            model_name='historicaluser',
            name='current_manuscript',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='main.Manuscript'),
        ),
        migrations.AddField(
            model_name='user',
            name='current_manuscript',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='manuscript_current_selecting_users', to='main.Manuscript'),
        ),
    ]
