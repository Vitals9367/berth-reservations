# Generated by Django 2.2.6 on 2020-04-01 09:39

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("resources", "0007_add_is_accessible_to_berth"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="harbor",
            name="maximum_depth",
        ),
        migrations.RemoveField(
            model_name="harbor",
            name="maximum_length",
        ),
        migrations.RemoveField(
            model_name="harbor",
            name="maximum_width",
        ),
        migrations.RemoveField(
            model_name="winterstoragearea",
            name="max_length",
        ),
        migrations.RemoveField(
            model_name="winterstoragearea",
            name="max_width",
        ),
    ]
