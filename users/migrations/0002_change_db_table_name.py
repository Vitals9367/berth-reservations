# Generated by Django 2.2.6 on 2019-10-16 07:09

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [("users", "0001_initial")]

    operations = [
        migrations.AlterModelOptions(
            name="user",
            options={"verbose_name": "user", "verbose_name_plural": "users"},
        ),
        migrations.AlterModelTable(name="user", table=None),
    ]
