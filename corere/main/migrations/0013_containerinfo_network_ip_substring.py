# Generated by Django 3.1.8 on 2021-04-08 15:06

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0012_auto_20210325_2133'),
    ]

    operations = [
        migrations.AddField(
            model_name='containerinfo',
            name='network_ip_substring',
            field=models.CharField(blank=True, max_length=12, null=True),
        ),
    ]