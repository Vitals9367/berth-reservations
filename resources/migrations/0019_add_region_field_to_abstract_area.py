# Generated by Django 3.1 on 2020-09-04 13:09

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("resources", "0018_update_translation_foreign_keys"),
    ]

    operations = [
        migrations.AddField(
            model_name="harbor",
            name="region",
            field=models.CharField(
                blank=True,
                choices=[("east", "East"), ("west", "West")],
                max_length=32,
                null=True,
                verbose_name="area region",
            ),
        ),
        migrations.AddField(
            model_name="winterstoragearea",
            name="region",
            field=models.CharField(
                blank=True,
                choices=[("east", "East"), ("west", "West")],
                max_length=32,
                null=True,
                verbose_name="area region",
            ),
        ),
    ]
