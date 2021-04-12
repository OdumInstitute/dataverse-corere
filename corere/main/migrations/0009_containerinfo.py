# Generated by Django 3.1.7 on 2021-03-18 17:20

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0008_historicalverificationmetadata_historicalverificationmetadataaudit_historicalverificationmetadatabad'),
    ]

    operations = [
        migrations.CreateModel(
            name='ContainerInfo',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image_id', models.CharField(blank=True, max_length=64, null=True)),
                ('image_token', models.CharField(blank=True, max_length=64, null=True)),
                ('container_id', models.CharField(blank=True, max_length=64, null=True)),
                ('container_ip', models.CharField(blank=True, max_length=24, null=True)),
                ('container_port', models.CharField(blank=True, max_length=5, null=True)),
                ('submission_version', models.IntegerField()),
                ('manuscript', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='manuscript_containerinfo', to='main.manuscript')),
            ],
        ),
    ]