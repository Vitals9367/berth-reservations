# Generated by Django 2.2.6 on 2020-06-25 09:42

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0003_add_order_models"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="order",
            constraint=models.UniqueConstraint(
                fields=("_lease_content_type", "_lease_object_id"), name="unique_lease"
            ),
        ),
    ]
