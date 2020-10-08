# Generated by Django 3.1 on 2020-10-08 06:52

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0010_add_ordertoken_cancelled"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ordertoken",
            name="order",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tokens",
                to="payments.order",
                verbose_name="order",
            ),
        ),
    ]
