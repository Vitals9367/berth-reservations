from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Union

from babel.dates import format_date
from dateutil.relativedelta import relativedelta
from dateutil.utils import today
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django_ilmoitin.utils import send_notification

from customers.services import ProfileService
from leases.enums import LeaseStatus
from utils.email import is_valid_email

if TYPE_CHECKING:
    from .models import BerthLease, WinterStorageLease


def calculate_season_start_date(lease_start: date = None) -> date:
    """Return the date when the summer season starts

    If the current date is after the season end date,
    it returns next year's default start date.

    Leases always start on 10.6 the earliest
    """
    today = date.today()
    default = date(day=10, month=6, year=today.year)

    if lease_start:
        return default.replace(year=lease_start.year)

    # If today is gte than the date when all the leases end,
    # return the default start date for the next year
    if today >= date(day=14, month=9, year=today.year):
        return default.replace(year=today.year + 1)

    return default


def calculate_season_end_date(lease_end: date = None) -> date:
    """Return the date when the summer season ends

    Leases always end on 14.9, the year depends on the current year
    """
    today = date.today()
    default = date(day=14, month=9, year=today.year)

    if lease_end:
        return default.replace(year=lease_end.year)

    # If today is gte than the day when all leases end,
    # return the default end date for the next year
    if today >= default:
        return default.replace(year=today.year + 1)

    # Otherwise, return the default end date for the current year
    return default


def calculate_winter_season_start_date(lease_start: date = None) -> date:
    """Return the date when the winter season starts

    If the current date is after the season end date,
    it returns next year's default start date.

    Winter Storage Leases always start on 15.9 the earliest
    """
    today = date.today()
    default = date(day=15, month=9, year=today.year)

    if lease_start:
        lease_date = default.replace(year=lease_start.year)
        # If the lease started between 1.1 and 10.6, that means
        # the season began the previous year
        if lease_start <= date(day=10, month=6, year=lease_start.year):
            lease_date -= relativedelta(years=1)
        return lease_date

    # If the current day is before the winter season ends,
    # the start day will be on the previous year
    if today <= date(day=10, month=6, year=today.year):
        default -= relativedelta(years=1)

    return default


def calculate_winter_season_end_date(lease_end: date = None) -> date:
    """Return the date when the winter season ends

    Winter Storage Leases always end on 10.6, the year depends on the current year
    """
    today = date.today()
    default = date(day=10, month=6, year=today.year)

    if lease_end:
        lease_date = default.replace(year=lease_end.year)
        # If the lease ended between 15.9 and 31.12, that means
        # the season ends the following year.
        if lease_end >= date(day=15, month=9, year=lease_end.year):
            lease_date = default.replace(year=lease_end.year + 1)
        return lease_date

    # If today is gte than the day when all leases end,
    # return the default end date for the next year
    if today > default:
        return default.replace(year=today.year + 1)

    # Otherwise, return the default end date for the current year
    return default


def calculate_berth_lease_start_date() -> date:
    """
    Return the date when the lease season should start

    If a lease object is being created before 10.6, then the dates are in the same year.
    If the object is being created between those dates, then the start date is
    the date of creation and end date is 14.9 of the same year.
    If the object is being created after 14.9, then the dates are from next year.
    """

    # Otherwise, return the latest date between the default start date or today
    return max(calculate_season_start_date(), date.today())


def calculate_berth_lease_end_date() -> date:
    """Return the date when the lease season should end

    Leases always end on 14.9, the year depends on the current year
    """
    return calculate_season_end_date()


def calculate_winter_storage_lease_start_date() -> date:
    """
    Return the date when the "winter" lease season should start

    The season should start by default on 15.9 (one day after the "summer" season ends)
    """

    # Return the earliest date between the default start date or today
    return max(calculate_winter_season_start_date(), date.today())


def calculate_winter_storage_lease_end_date() -> date:
    """Return the date when the lease season should end

    The season should end by default on 10.6 on the following year
    """
    return calculate_winter_season_end_date()


def terminate_lease(
    lease: Union[BerthLease, WinterStorageLease],
    end_date: date = None,
    profile_token: str = None,
    send_notice: bool = True,
) -> Union[BerthLease, WinterStorageLease]:
    from .models import BerthLease
    from .notifications import NotificationType

    if lease.status != LeaseStatus.PAID:
        raise ValidationError(_(f"Lease is not paid: {lease.status}"))

    lease.status = LeaseStatus.TERMINATED

    if isinstance(lease, BerthLease):
        default_date = calculate_berth_lease_start_date()
    else:  # WinterStorageLease
        default_date = calculate_winter_storage_lease_start_date()
    lease.end_date = end_date or default_date

    lease.save()

    if send_notice:
        language = (
            lease.application.language
            if lease.application
            else settings.LANGUAGES[0][0]
        )

        email = None

        if profile_token:
            profile_service = ProfileService(profile_token=profile_token)
            profile = profile_service.get_profile(lease.customer.id)
            email = profile.email

        if not email and lease.application:
            email = lease.application.email

        if not email:
            raise ValidationError(
                _("The lease has no email and no profile token was provided")
            )

        if not is_valid_email(email):
            raise ValidationError(_("Missing customer email"))

        notification_type = (
            NotificationType.BERTH_LEASE_TERMINATED_LEASE_NOTICE
            if isinstance(lease, BerthLease)
            else NotificationType.WINTER_STORAGE_LEASE_TERMINATED_LEASE_NOTICE
        )

        send_notification(
            email,
            notification_type,
            {
                "subject": notification_type.label,
                "cancelled_at": format_date(today(), locale=language),
                "lease": lease,
            },
            language=language,
        )

    return lease
