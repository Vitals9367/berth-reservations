# Generated by Django 3.1 on 2020-11-02 13:51

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0010_add_ws_lease_sticker_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="winterstoragelease",
            name="sticker_posted",
            field=models.DateField(
                blank=True, null=True, verbose_name="sticker posted"
            ),
        ),
    ]
