# Generated by Django 2.2.6 on 2019-10-29 12:28

from django.db import migrations, models
import django.db.models.deletion
import enumfields.fields
import reservations.enums


class Migration(migrations.Migration):

    dependencies = [("reservations", "0013_copy_templates")]

    operations = [
        migrations.AlterField(
            model_name="berthswitch",
            name="reason",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="reservations.BerthSwitchReason",
                verbose_name="berth switch reason",
            ),
        ),
        migrations.AlterField(
            model_name="winterstoragereservation",
            name="storage_method",
            field=enumfields.fields.EnumField(
                enum=reservations.enums.WinterStorageMethod,
                max_length=60,
                verbose_name="storage method",
            ),
        ),
    ]
