# Generated by Django 2.2.6 on 2020-01-08 11:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("resources", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="berth",
            name="comment",
            field=models.TextField(blank=True, verbose_name="comment"),
        ),
    ]
