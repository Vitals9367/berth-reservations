# Generated by Django 3.1 on 2020-12-02 13:09

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0016_add_error_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="customer_address",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="customer_city",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="customer_email",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="customer_first_name",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="customer_last_name",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="customer_zip_code",
            field=models.TextField(blank=True, null=True),
        ),
    ]
