# Generated by Django 3.1 on 2021-03-12 12:02

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0024_add_order_refund_model"),
    ]

    operations = [
        migrations.CreateModel(
            name="BerthSwitchOfferLogEntry",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, verbose_name="time created"
                    ),
                ),
                (
                    "modified_at",
                    models.DateTimeField(auto_now=True, verbose_name="time modified"),
                ),
                (
                    "from_status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("accepted", "Accepted"),
                            ("rejected", "Rejected"),
                            ("expired", "Expired"),
                            ("cancelled", "Cancelled"),
                        ],
                        max_length=9,
                    ),
                ),
                (
                    "to_status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("accepted", "Accepted"),
                            ("rejected", "Rejected"),
                            ("expired", "Expired"),
                            ("cancelled", "Cancelled"),
                        ],
                        max_length=9,
                    ),
                ),
                ("comment", models.TextField(blank=True, null=True)),
                (
                    "offer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="log_entries",
                        to="payments.berthswitchoffer",
                        verbose_name="order",
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "berth switch log entries",
                "abstract": False,
            },
        ),
    ]
