import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Union

from dateutil.utils import today
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Max, OuterRef, Q, Subquery, UniqueConstraint, Value
from django.db.models.functions import Coalesce
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from applications.enums import ApplicationAreaType
from applications.models import BerthApplication, WinterStorageApplication
from customers.models import CustomerProfile
from customers.services import ProfileService
from leases.enums import LeaseStatus
from leases.models import BerthLease, WinterStorageLease
from leases.stickers import get_next_sticker_number
from leases.utils import calculate_season_start_date
from resources.models import Berth
from utils.models import TimeStampedModel, UUIDModel
from utils.numbers import rounded as rounded_decimal

from .enums import (
    AdditionalProductType,
    LeaseOrderType,
    OfferStatus,
    OrderRefundStatus,
    OrderStatus,
    OrderType,
    PeriodType,
    PriceTier,
    PriceUnits,
    ProductServiceType,
)
from .exceptions import OrderStatusTransitionError
from .utils import (
    calculate_organization_price,
    calculate_organization_tax_percentage,
    calculate_product_partial_month_price,
    calculate_product_partial_year_price,
    calculate_product_percentage_price,
    convert_aftertax_to_pretax,
    generate_order_number,
    get_application_status,
    get_lease_status,
    rounded,
)

PLACE_PRODUCT_TAX_PERCENTAGES = [Decimal(x) for x in ("24.00",)]
ADDITIONAL_PRODUCT_TAX_PERCENTAGES = [Decimal(x) for x in ("24.00", "10.00")]

DEFAULT_TAX_PERCENTAGE = Decimal("24.0")


logger = logging.getLogger(__name__)


class AbstractBaseProduct(TimeStampedModel, UUIDModel):
    price_value = models.DecimalField(
        verbose_name=_("price"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    price_unit = models.CharField(
        choices=PriceUnits.choices, default=PriceUnits.AMOUNT, max_length=10
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class AbstractPlaceProduct(models.Model):
    tax_percentage = models.DecimalField(
        verbose_name=_("tax percentage"),
        max_digits=5,
        decimal_places=2,
        default=DEFAULT_TAX_PERCENTAGE,
        choices=[(tax, str(tax)) for tax in PLACE_PRODUCT_TAX_PERCENTAGES],
    )

    class Meta:
        abstract = True


class BerthProductManager(models.Manager):
    def get_in_range(self, width: Union[Decimal, float, int]):
        products = self.get_queryset().filter(min_width__lt=width, max_width__gte=width)
        if len(products) != 1:
            logger.error(f"Not only one berth product found: {width=}, {products=}")

        return products.first()


class BerthProduct(AbstractPlaceProduct, TimeStampedModel, UUIDModel):
    """The range boundaries are (]"""

    min_width = models.DecimalField(
        verbose_name=_("minimum width"), max_digits=5, decimal_places=2,
    )
    max_width = models.DecimalField(
        verbose_name=_("maximum width"), max_digits=5, decimal_places=2,
    )
    tier_1_price = models.DecimalField(
        verbose_name=_("tier 1 price"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    tier_2_price = models.DecimalField(
        verbose_name=_("tier 2 price"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    tier_3_price = models.DecimalField(
        verbose_name=_("tier 3 price"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    price_unit = models.CharField(
        choices=[(PriceUnits.AMOUNT, PriceUnits.AMOUNT.label)],
        default=PriceUnits.AMOUNT,
        max_length=10,
    )
    objects = BerthProductManager()

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=("min_width", "max_width",), name="unique_width_range"
            )
        ]
        ordering = ["min_width"]

    def __str__(self):
        return (
            f"[{self.min_width}-{self.max_width}] "
            f"T1:{self.tier_1_price}€, T2:{self.tier_2_price}€, T3: {self.tier_3_price}€"
        )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def display_dimensions(self):
        return f"{self.min_width}m - {self.max_width}m"

    @property
    def name(self):
        return _("Berth product")

    def price_for_tier(self, tier: PriceTier) -> Decimal:
        if tier == PriceTier.TIER_1:
            return self.tier_1_price
        elif tier == PriceTier.TIER_2:
            return self.tier_2_price
        elif tier == PriceTier.TIER_3:
            return self.tier_3_price
        raise ValueError(_("Tier not implemented"))


class WinterStorageProduct(AbstractPlaceProduct, AbstractBaseProduct):
    winter_storage_area = models.OneToOneField(
        "resources.WinterStorageArea",
        verbose_name=_("winter storage area"),
        related_name="product",
        on_delete=models.CASCADE,
    )
    price_unit = models.CharField(
        choices=[(PriceUnits.AMOUNT, PriceUnits.AMOUNT.label)],
        default=PriceUnits.AMOUNT,
        max_length=10,
    )

    def __str__(self):
        return f"{self.winter_storage_area} ({self.price_value}€)"

    @property
    def name(self):
        return _("Winter Storage product")


class AdditionalProduct(AbstractBaseProduct):
    service = models.CharField(
        choices=ProductServiceType.choices, verbose_name=_("service"), max_length=40
    )
    period = models.CharField(choices=PeriodType.choices, max_length=8)
    tax_percentage = models.DecimalField(
        verbose_name=_("tax percentage"),
        max_digits=5,
        decimal_places=2,
        default=DEFAULT_TAX_PERCENTAGE,
        choices=[(tax, str(tax)) for tax in ADDITIONAL_PRODUCT_TAX_PERCENTAGES],
    )

    @property
    def product_type(self):
        if self.service in ProductServiceType.FIXED_SERVICES():
            return AdditionalProductType.FIXED_SERVICE
        elif self.service in ProductServiceType.OPTIONAL_SERVICES():
            return AdditionalProductType.OPTIONAL_SERVICE
        return None

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["service", "period"],
                condition=Q(service__in=ProductServiceType.OPTIONAL_SERVICES()),
                name="optional_services_per_period",
            ),
        ]

    def clean(self):
        if self.service in ProductServiceType.FIXED_SERVICES():
            if self.tax_percentage != DEFAULT_TAX_PERCENTAGE:
                raise ValidationError(
                    _(f"Fixed services must have VAT of {DEFAULT_TAX_PERCENTAGE}€")
                )
            if self.period != PeriodType.SEASON:
                raise ValidationError(_("Fixed services are only valid for season"))

    def __str__(self):
        return self.name

    @property
    def name(self):
        return f"{ProductServiceType(self.service).label} - {PeriodType(self.period).label}"


class OrderManager(models.Manager):
    def berth_orders(self):
        product_ct = ContentType.objects.get_for_model(BerthProduct)
        lease_ct = ContentType.objects.get_for_model(BerthLease)

        return self._order_type_orders(product_ct, lease_ct)

    def winter_storage_orders(self):
        product_ct = ContentType.objects.get_for_model(WinterStorageProduct)
        lease_ct = ContentType.objects.get_for_model(WinterStorageLease)

        return self._order_type_orders(product_ct, lease_ct)

    def _order_type_orders(self, product_ct: ContentType, lease_ct: ContentType):
        return self.get_queryset().filter(
            Q(Q(_product_content_type=product_ct) | Q(_lease_content_type=lease_ct))
        )

    def expire_too_old_unpaid_orders(self, older_than_days, dry_run=False) -> int:
        # Check all orders that are in OFFERED status, and if there is
        # {older_than_days} full days elapsed after the order's due_date, then
        # set the order status to EXPIRED.
        # Example:
        # * There's an order with due_date=1.1.2021 and status=OFFERED.
        # * Calling expire_too_old_unpaid_orders(older_than_days=7) on 9.1.2021 will set the status to EXPIRED.
        # * But calling the function on 8.1.2021 would not change the order.

        expire_before_date = date.today() - timedelta(days=older_than_days)
        too_old_offered_orders = self.get_queryset().filter(
            status=OrderStatus.OFFERED, due_date__lt=expire_before_date,
        )
        num_expired = 0
        for order in too_old_offered_orders:
            if order.order_type == OrderType.LEASE_ORDER and not order.lease:
                logger.info(
                    f"Lease missing from lease order, skip invalid order {order}"
                )
                continue
            if order._product_object_id and not order._get_product_from_object_id():
                # _product_object_id contains an UUID to a product that no longer exists.
                # set_status() will fail in this case, and instead of EXPIRED these
                # should be set to ERROR.
                logger.info(f"Product missing from order, skip invalid order {order}")
                continue

            if not dry_run:
                order.set_status(
                    OrderStatus.EXPIRED,
                    comment=f"{_('Order expired at')} {expire_before_date}",
                )
            num_expired += 1
        return num_expired

    def get_queryset(self):
        def status_qs(statuses):
            if len(statuses) == 1:
                filter_kw = {"to_status": statuses[0]}
            elif len(statuses) > 1:
                filter_kw = {"to_status__in": statuses}
            else:
                raise ValueError("no statuses specified")
            return (
                OrderLogEntry.objects.filter(order=OuterRef("pk"), **filter_kw)
                .order_by("-created_at")
                .values("order__pk")
                .annotate(prop=Coalesce(Max("created_at"), Value(None)))
                .values("prop")
            )

        return (
            super()
            .get_queryset()
            .annotate(
                paid_at=Subquery(
                    status_qs([OrderStatus.PAID, OrderStatus.PAID_MANUALLY]),
                    output_field=models.DateTimeField(),
                ),
                rejected_at=Subquery(
                    status_qs([OrderStatus.REJECTED]),
                    output_field=models.DateTimeField(),
                ),
                cancelled_at=Subquery(
                    status_qs([OrderStatus.CANCELLED]),
                    output_field=models.DateTimeField(),
                ),
            )
        )


class Order(UUIDModel, TimeStampedModel):
    order_number = models.CharField(
        max_length=64,
        verbose_name=_("order number"),
        default=generate_order_number,
        unique=True,
        editable=False,
        db_index=True,
    )
    order_type = models.CharField(
        choices=OrderType.choices,
        verbose_name=_("order type"),
        max_length=30,
        default=OrderType.LEASE_ORDER,
    )
    customer = models.ForeignKey(
        "customers.CustomerProfile",
        verbose_name=_("customer"),
        on_delete=models.CASCADE,
        related_name="orders",
    )
    product = GenericForeignKey("_product_content_type", "_product_object_id")
    lease = GenericForeignKey("_lease_content_type", "_lease_object_id")
    status = models.CharField(
        choices=OrderStatus.choices, default=OrderStatus.DRAFTED, max_length=9
    )
    comment = models.TextField(blank=True, null=True)

    # these fields are filled from hki profile service
    customer_first_name = models.TextField(blank=True, null=True)
    customer_last_name = models.TextField(blank=True, null=True)
    customer_email = models.TextField(blank=True, null=True)
    customer_phone = models.TextField(blank=True, null=True)
    customer_address = models.TextField(blank=True, null=True)
    customer_zip_code = models.TextField(blank=True, null=True)
    customer_city = models.TextField(blank=True, null=True)

    price = models.DecimalField(
        verbose_name=_("price"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        blank=True,
    )
    tax_percentage = models.DecimalField(
        verbose_name=_("tax percentage"),
        max_digits=5,
        decimal_places=2,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    due_date = models.DateField(verbose_name=_("due date"), blank=True, null=True)

    _product_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="product",
    )
    _product_object_id = models.UUIDField(
        null=True, blank=True, verbose_name=_("product")
    )

    _lease_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lease",
    )
    _lease_object_id = models.UUIDField(null=True, blank=True, verbose_name=_("lease"))

    objects = OrderManager()

    def __str__(self):
        return f"{self.id} [{self.status}]"

    @property
    def lease_order_type(self) -> LeaseOrderType:
        if (
            not hasattr(self, "lease")
            or self.order_type == OrderType.ADDITIONAL_PRODUCT_ORDER
        ):
            return LeaseOrderType.INVALID

        # Check for application-specific fields:
        # - if it's a winter storage application:
        #   - return the type based on the area_type property
        # - if it's a berth application, check for a berth_switch
        if hasattr(self.lease, "application"):
            if isinstance(self.lease.application, WinterStorageApplication):
                return (
                    LeaseOrderType.UNMARKED_WINTER_STORAGE_ORDER
                    if self.lease.application.area_type == ApplicationAreaType.UNMARKED
                    else LeaseOrderType.WINTER_STORAGE_ORDER
                )
            elif (
                isinstance(self.lease.application, BerthApplication)
                and self.lease.application.berth_switch
            ):
                return LeaseOrderType.BERTH_SWITCH_ORDER

        # If it's a berth lease, check if it's a new lease or not
        if isinstance(self.lease, BerthLease):
            # Look for leases before the one associated to the order,
            # that belong to the same customer and berth
            has_previous_lease = (
                BerthLease.objects.filter(
                    customer=self.customer,
                    berth=self.lease.berth,
                    end_date__lte=self.lease.start_date,
                )
                .exclude(id=self.lease.id)
                .exists()
            )
            if has_previous_lease:
                return LeaseOrderType.RENEW_BERTH_ORDER
            return LeaseOrderType.NEW_BERTH_ORDER

        # Winter storage only has one type (unless it's unmarked)
        elif isinstance(self.lease, WinterStorageLease):
            return LeaseOrderType.WINTER_STORAGE_ORDER

        return LeaseOrderType.INVALID

    @property
    @rounded
    def pretax_price(self):
        return convert_aftertax_to_pretax(self.price, self.tax_percentage)

    @property
    @rounded
    def total_price(self):
        return sum([ol.price for ol in self.order_lines.all()], self.price)

    @property
    def fixed_price_total(self):
        return sum(
            [
                ol.price
                for ol in self.order_lines.filter(
                    product__service__in=ProductServiceType.FIXED_SERVICES()
                )
            ],
            self.price,
        )

    @property
    @rounded
    def total_pretax_price(self):
        return sum(
            [ol.pretax_price for ol in self.order_lines.all()], self.pretax_price
        )

    @property
    def total_tax_percentage(self):
        return (
            rounded_decimal(
                ((self.total_price - self.total_pretax_price) / self.total_pretax_price)
                * 100,
                round_to_nearest=0.05,
            )
            if self.total_pretax_price != 0
            else 0
        )

    @property
    def total_tax_value(self):
        return self.total_price - self.total_pretax_price

    def _check_valid_products(self) -> None:
        if self.product and not isinstance(
            self.product, (BerthProduct, WinterStorageProduct)
        ):
            raise ValidationError(_("You cannot assign other types of products"))

    def _check_valid_lease(self) -> None:
        # Check that only Berth or Winter leases are passed (not other models)
        if not isinstance(self.lease, (BerthLease, WinterStorageLease)):
            raise ValidationError(_("You cannot assign other types of leases"))

        # Check that the lease customer and the received customer are the same
        if self.lease.customer != self.customer:
            raise ValidationError(
                _("The lease provided belongs to a different customer")
            )

    def _check_product_and_lease(self) -> None:
        if isinstance(self.product, BerthProduct) and not isinstance(
            self.lease, BerthLease
        ):
            raise ValidationError(
                _("A BerthProduct must be associated with a BerthLease")
            )
        elif isinstance(self.product, WinterStorageProduct) and not isinstance(
            self.lease, WinterStorageLease
        ):
            raise ValidationError(
                _("A WinterStorageProduct must be associated with a WinterStorageLease")
            )

    def _check_product_or_price(self) -> None:
        if all([not self.product, not self._product_object_id, self.price is None]):
            raise ValidationError(
                _("Order must have either product object or price value")
            )

    def _check_same_product(self, old_instance) -> None:
        if (
            self.status not in OrderStatus.get_waiting_statuses()
            and old_instance.product
            and self.product != old_instance.product
        ):
            raise ValidationError(_("Cannot change the product assigned to this order"))

    def _check_same_lease(self, old_instance) -> None:
        if old_instance.lease and self.lease != old_instance.lease:
            raise ValidationError(
                _("Cannot change the lease associated with this order")
            )

    def _check_due_date(self, old_instance) -> None:
        if (
            old_instance.due_date != self.due_date
            and old_instance.status == OrderStatus.EXPIRED
        ):
            raise ValidationError(_("Cannot change due date of this order"))

    def clean(self):
        # Check that only Berth or Winter products are passed (not other models)
        self._check_valid_products()

        if self.lease:
            self._check_valid_lease()

            # Check that product and lease are from the same type
            if self.product:
                self._check_product_and_lease()

        # Check that it has either product or price
        self._check_product_or_price()

        if not self._state.adding:
            old_instance = Order.objects.get(id=self.id)

            # If the product is being changed
            self._check_same_product(old_instance)

            # If the lease is being changed
            self._check_same_lease(old_instance)

            # if due_date change is allowed
            self._check_due_date(old_instance)

        if self.status == OrderStatus.OFFERED and not self.due_date:
            raise ValidationError(_("Order cannot be offered without a due date"))

    def _assign_product_from_object_id(self):
        # If for some reason neither was found (shouldn't be the case), raise an error
        product = self._get_product_from_object_id()
        if not product:
            raise ValidationError(_("The product passed is not valid"))

        self.product = product

    def _get_product_from_object_id(self):
        # Try to get a BerthProduct (BP)
        product = BerthProduct.objects.filter(id=self._product_object_id).first()
        # If the BP was not found, try getting a WinterStorageProduct
        product = (
            product
            if product
            else WinterStorageProduct.objects.filter(id=self._product_object_id).first()
        )
        return product

    def _assign_lease_from_object_id(self):
        # Try to get a BerthLease (BL)
        lease = BerthLease.objects.filter(id=self._lease_object_id).first()
        # If the BL was not found, try getting a WinterStorageLease
        lease = (
            lease
            if lease
            else WinterStorageLease.objects.filter(id=self._lease_object_id).first()
        )
        # If for some reason neither was found, raise an error
        if not lease:
            raise ValidationError(_("The lease passed is not valid"))

        self.lease = lease

    def recalculate_price(self):
        # Setting self.price to None forces _update_price to save the recalculated price, using the
        # same logic as creating a new Order.
        if self.status in OrderStatus.get_waiting_statuses():
            self.price = None
            # Update the associated product for recomputing the price
            self._update_product()
        self._update_price()
        for order_line in self.order_lines.all():
            order_line.recalculate_price()
            order_line.save()

    def _update_product(self):
        if self.product and self.lease:
            if isinstance(self.lease, BerthLease):
                width = self.lease.berth.berth_type.width
                self.product = BerthProduct.objects.get_in_range(width=width)
            elif isinstance(self.lease, WinterStorageLease):
                self.product = WinterStorageProduct.objects.get(
                    winter_storage_area=self.lease.get_winter_storage_area()
                )

    def _update_price(self):
        price = (
            self.product.price_value
            if self.product and hasattr(self.product, "price_value")
            else 0
        )
        # Assign the tax from the product
        tax_percentage = self.product.tax_percentage

        if self.lease:
            # If the order is for a winter product with a lease, the price
            # has to be calculated based on the dimensions of the place associated
            # to the lease
            if isinstance(self.lease, WinterStorageLease) and isinstance(
                self.product, WinterStorageProduct
            ):
                price = self.product.price_value
                if self.lease.place:
                    place_sqm = (
                        self.lease.place.place_type.width
                        * self.lease.place.place_type.length
                    )
                elif self.lease.boat:
                    # If the lease is only associated to an section,
                    # calculate the price based on the boat dimensions
                    place_sqm = self.lease.boat.width * self.lease.boat.length
                else:
                    # If the lease is not associated to a boat object,
                    # take the values set by the user on the application
                    place_sqm = (
                        self.lease.application.boat_width
                        * self.lease.application.boat_length
                    )
                price = price * place_sqm
            else:
                price = self.product.price_for_tier(self.lease.berth.pier.price_tier)

        price_value = self.price or price
        tax_percentage_value = self.tax_percentage or tax_percentage

        if hasattr(self.customer, "organization"):
            organization_type = self.customer.organization.organization_type

            self.price = calculate_organization_price(price_value, organization_type)
            self.tax_percentage = calculate_organization_tax_percentage(
                tax_percentage_value, organization_type
            )
        else:
            self.price = rounded_decimal(price_value)
            self.tax_percentage = tax_percentage_value

    def save(self, *args, **kwargs):
        self.full_clean()

        # If the product is being added from the admin (only the ID is passed)
        if self._product_object_id and not self.product:
            self._assign_product_from_object_id()

        # If the lease is being added from the admin (only the ID is passed)
        if self._lease_object_id and not self.lease:
            self._assign_lease_from_object_id()

        creating = self._state.adding

        # If the product instance is being passed
        # Price has to be assigned before saving if creating
        if creating and self.product:
            self._update_price()

        super().save(*args, **kwargs)

    def set_status(self, new_status: OrderStatus, comment: str = None) -> None:
        old_status = self.status
        if new_status == old_status:
            return

        valid_status_changes = {
            OrderStatus.DRAFTED: (
                OrderStatus.OFFERED,
                OrderStatus.PAID_MANUALLY,
                OrderStatus.ERROR,
            ),
            OrderStatus.OFFERED: (
                OrderStatus.PAID,
                OrderStatus.PAID_MANUALLY,
                OrderStatus.EXPIRED,
                OrderStatus.REJECTED,
                OrderStatus.ERROR,
                OrderStatus.CANCELLED,
            ),
            OrderStatus.PAID: (OrderStatus.REFUNDED,),
            OrderStatus.PAID_MANUALLY: (OrderStatus.REFUNDED,),
            OrderStatus.ERROR: (
                OrderStatus.DRAFTED,
                OrderStatus.OFFERED,
                OrderStatus.PAID_MANUALLY,
                OrderStatus.CANCELLED,
            ),
            OrderStatus.CANCELLED: (OrderStatus.OFFERED,),
        }
        valid_new_status = valid_status_changes.get(old_status, ())

        if new_status not in valid_new_status:
            raise OrderStatusTransitionError(
                'Cannot set order {} state to "{}", it is in an invalid state "{}".'.format(
                    self.order_number, new_status, old_status
                )
            )

        self.status = new_status
        self.save(update_fields=["status"])

        if self.order_type == OrderType.LEASE_ORDER:
            self.update_lease_and_application(new_status)
            self.update_sticker_number_if_needed()

        self.create_log_entry(
            from_status=old_status, to_status=new_status, comment=comment
        )

    def create_log_entry(
        self,
        from_status: OrderStatus = None,
        to_status: OrderStatus = None,
        comment: str = "",
    ) -> None:
        OrderLogEntry.objects.create(
            order=self,
            from_status=from_status,
            to_status=to_status or self.status,
            comment=comment,
        )

    def invalidate_tokens(self):
        tokens = list(self.tokens.all())
        for token in tokens:
            token.cancelled = True
        self.tokens.bulk_update(tokens, ["cancelled"])

    def update_lease_and_application(self, new_status):
        if lease_status := get_lease_status(new_status):
            self.lease.status = lease_status

        if new_status == OrderStatus.ERROR:
            message = (
                f"{today().date()}: {_('Error with the order, check the order first')}"
            )
            if len(self.lease.comment) > 0:
                self.lease.comment += f"\n{message}"
            else:
                self.lease.comment = message

        self.lease.save(update_fields=["status", "comment"])

        if application := self.lease.application:
            if new_application_status := get_application_status(new_status):
                application.status = new_application_status
                application.save(update_fields=["status"])

    def update_sticker_number_if_needed(self):
        if (
            self.status in OrderStatus.get_paid_statuses()
            and (application := self.lease.application)
            and isinstance(self.lease, WinterStorageLease)
            and application.area_type == ApplicationAreaType.UNMARKED
        ):
            sticker_number = get_next_sticker_number(self.lease.start_date)
            self.lease.sticker_number = sticker_number
            self.lease.save(update_fields=["sticker_number"])


class OrderLine(UUIDModel, TimeStampedModel):
    order = models.ForeignKey(
        Order,
        verbose_name=_("order"),
        related_name="order_lines",
        on_delete=models.CASCADE,
    )
    product = models.ForeignKey(
        AdditionalProduct,
        verbose_name=_("product"),
        related_name="orders_lines",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    quantity = models.PositiveSmallIntegerField(
        verbose_name=_("quantity"), default=1, validators=[MinValueValidator(1)],
    )
    price = models.DecimalField(
        verbose_name=_("price"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        blank=True,
    )
    tax_percentage = models.DecimalField(
        verbose_name=_("tax percentage"),
        max_digits=5,
        decimal_places=2,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    def clean(self):
        creating = self._state.adding
        if not creating:
            old_instance = OrderLine.objects.get(id=self.id)
            # If the order is being changed
            if old_instance.order and self.order != old_instance.order:
                raise ValidationError(
                    _("Cannot change the order associated with this order line")
                )

            # If the product is being changed
            if old_instance.product != self.product:
                raise ValidationError(
                    _("Cannot change the product assigned to this order line")
                )

    def save(self, *args, **kwargs):
        self.full_clean()

        creating = self._state.adding
        if creating and self.product:
            self.recalculate_price()

        super().save(*args, **kwargs)

    def recalculate_price(self):
        price = self.product.price_value
        unit = self.product.price_unit
        if unit == PriceUnits.PERCENTAGE:
            price = (
                calculate_product_percentage_price(
                    self.order.price, self.product.price_value
                )
                if self.order.order_type == OrderType.LEASE_ORDER
                else self._calculate_percentage_price_for_additional_prod_order(
                    self.order.lease, self.product.price_value
                )
            )
        if self.order.lease:
            if self.product.period == PeriodType.MONTH:
                price = calculate_product_partial_month_price(
                    price, self.order.lease.start_date, self.order.lease.end_date,
                )
            elif self.product.period == PeriodType.YEAR:
                price = calculate_product_partial_year_price(
                    price, self.order.lease.start_date, self.order.lease.end_date,
                )
            # The price for season products should always be full
        if hasattr(self.order.customer, "organization"):
            organization_type = self.order.customer.organization.organization_type

            self.price = calculate_organization_price(price, organization_type)
            self.tax_percentage = calculate_organization_tax_percentage(
                self.product.tax_percentage, organization_type,
            )
        else:
            self.price = rounded_decimal(price)
            self.tax_percentage = self.product.tax_percentage

    @staticmethod
    def _calculate_percentage_price_for_additional_prod_order(
        lease: BerthLease, percentage
    ):
        if lease_order := lease.orders.filter(
            status__in=OrderStatus.get_paid_statuses(), order_type=OrderType.LEASE_ORDER
        ).first():
            return calculate_product_percentage_price(
                lease_order.fixed_price_total, percentage
            )
        else:
            raise ValidationError(_("Lease must have a paid order"))

    @property
    @rounded
    def pretax_price(self):
        return convert_aftertax_to_pretax(self.price, self.tax_percentage)

    @property
    def name(self):
        return f"{self.product.name}" if self.product else self.id

    def __str__(self):
        return (
            (
                f"{self.product.name} - {self.product.price_value}"
                f"{'€' if self.product.price_unit == PriceUnits.AMOUNT else '%'}"
            )
            if self.product
            else f"{_('No product')} - {self.price}€"
        )


class OrderLogEntry(UUIDModel, TimeStampedModel):
    order = models.ForeignKey(
        Order,
        verbose_name=_("order"),
        related_name="log_entries",
        on_delete=models.CASCADE,
    )
    from_status = models.CharField(choices=OrderStatus.choices, max_length=9)
    to_status = models.CharField(choices=OrderStatus.choices, max_length=9)
    comment = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name_plural = _("order log entries")

    def __str__(self):
        return (
            f"Order {self.order.id} | {self.from_status or 'N/A'} --> {self.to_status}"
        )


class OrderToken(UUIDModel, TimeStampedModel):
    order = models.ForeignKey(
        Order, verbose_name=_("order"), related_name="tokens", on_delete=models.CASCADE,
    )
    token = models.CharField(verbose_name=_("token"), max_length=64, blank=True)
    valid_until = models.DateTimeField(verbose_name=_("valid until"))
    cancelled = models.BooleanField(verbose_name=_("cancelled"), default=False)

    @property
    def is_valid(self):
        return not self.cancelled and now() < self.valid_until


class OrderRefund(UUIDModel, TimeStampedModel):
    order = models.ForeignKey(
        Order,
        verbose_name=_("order"),
        related_name="refunds",
        on_delete=models.CASCADE,
    )
    refund_id = models.CharField(verbose_name=_("refund id"), max_length=16, blank=True)
    status = models.CharField(
        choices=OrderRefundStatus.choices,
        default=OrderRefundStatus.PENDING,
        max_length=8,
    )
    amount = models.DecimalField(
        verbose_name=_("amount"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        if self.order.status not in OrderStatus.get_paid_statuses():
            raise ValidationError(_("Cannot refund orders that are not paid"))

    def set_status(self, new_status: OrderRefundStatus, comment: str = None) -> None:
        old_status = self.status
        if new_status == old_status:
            return

        valid_status_changes = {
            OrderRefundStatus.PENDING: (
                OrderRefundStatus.ACCEPTED,
                OrderRefundStatus.REJECTED,
            ),
        }
        valid_new_status = valid_status_changes.get(old_status, ())

        if new_status not in valid_new_status:
            raise OrderStatusTransitionError(
                'Cannot set refund {} state to "{}", it is in an invalid state "{}".'.format(
                    self.refund_id, new_status, old_status
                )
            )

        self.status = new_status
        self.save(update_fields=["status"])

        self.create_log_entry(
            from_status=old_status, to_status=new_status, comment=comment
        )

    def create_log_entry(
        self,
        from_status: OrderRefundStatus = None,
        to_status: OrderRefundStatus = None,
        comment: str = "",
    ) -> None:
        OrderRefundLogEntry.objects.create(
            refund=self,
            from_status=from_status,
            to_status=to_status or self.status,
            comment=comment,
        )


class OrderRefundLogEntry(UUIDModel, TimeStampedModel):
    refund = models.ForeignKey(
        OrderRefund,
        verbose_name=_("order refund"),
        related_name="log_entries",
        on_delete=models.CASCADE,
    )
    from_status = models.CharField(choices=OrderRefundStatus.choices, max_length=8)
    to_status = models.CharField(choices=OrderRefundStatus.choices, max_length=8)
    comment = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name_plural = _("refund log entries")

    def __str__(self):
        return f"Refund {self.refund.id} | {self.from_status or 'N/A'} --> {self.to_status}"


class AbstractOffer(UUIDModel, TimeStampedModel):
    offer_number = models.CharField(
        max_length=64,
        verbose_name=_("offer number"),
        default=generate_order_number,
        unique=True,
        editable=False,
        db_index=True,
    )
    customer = models.ForeignKey(
        CustomerProfile, related_name="offers", on_delete=models.CASCADE
    )
    status = models.CharField(
        choices=OfferStatus.choices, default=OfferStatus.DRAFTED, max_length=9
    )
    due_date = models.DateField(verbose_name=_("due date"), null=True, blank=True)
    # Optional fields to contact
    customer_first_name = models.TextField(blank=True, null=True)
    customer_last_name = models.TextField(blank=True, null=True)
    customer_email = models.TextField(blank=True, null=True)
    customer_phone = models.TextField(blank=True, null=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def update_from_profile(self, profile_token: str):
        profile = ProfileService(profile_token).get_profile(self.customer.id)
        self.update_from_customer_profile(profile)

    def update_from_customer_profile(self, profile):
        self.customer_first_name = profile.first_name
        self.customer_last_name = profile.last_name
        self.customer_email = profile.email
        self.customer_phone = profile.phone
        self.save()


class BerthSwitchOfferManager(models.Manager):
    def expire_too_old_offers(self, older_than_days, dry_run=False) -> int:
        # Check all orders that are in PENDING status, and if there is
        # {older_than_days} full days elapsed after the offers's due_date, then
        # set the order status to EXPIRED.
        # Example:
        # * There's an order with due_date=1.1.2021 and status=PENDING.
        # * Calling expire_too_old_offers(older_than_days=7) on 9.1.2021 will set the status to EXPIRED.
        # * But calling the function on 8.1.2021 would not change the offer.

        expire_before_date = date.today() - timedelta(days=older_than_days)
        too_old_pending_offers = self.get_queryset().filter(
            status=OfferStatus.OFFERED, due_date__lt=expire_before_date,
        )
        if not dry_run:
            for offer in too_old_pending_offers:
                offer.set_status(
                    OfferStatus.EXPIRED,
                    comment=f"{_('Offer expired at')} {expire_before_date}",
                )
        return len(too_old_pending_offers)


class BerthSwitchOffer(AbstractOffer):
    application = models.ForeignKey(
        BerthApplication, related_name="switch_offers", on_delete=models.CASCADE
    )
    lease = models.ForeignKey(
        BerthLease, related_name="switch_offers", on_delete=models.CASCADE
    )
    berth = models.ForeignKey(
        Berth, related_name="switch_offers", on_delete=models.CASCADE
    )

    objects = BerthSwitchOfferManager()

    def clean(self):
        # Validate that the offer customer is the same from the lease
        if self.customer != self.lease.customer:
            raise ValidationError(
                _("The lease has to belong to the same customer as the offer")
            )

        # Validate that the application is a switch application
        if not self.application.berth_switch:
            raise ValidationError(_("The application has to be a switch application"))

        # Validate that once the offer has been sent, it will have a due date
        # If offer is transitioned from DRAFTED directly to CANCELLED, then the due date is not set.
        if (
            self.status not in [OfferStatus.DRAFTED, OfferStatus.CANCELLED]
            and not self.due_date
        ):
            raise ValidationError(_("The offer must have a due date before sending it"))

        # Validate that the lease can only be from the current season
        if self.lease.start_date.year != calculate_season_start_date().year:
            raise ValidationError(
                _("The exchanged lease has to be from the current season")
            )

        if self.lease.status != LeaseStatus.PAID:
            raise ValidationError(_("The associated lease must be paid"))

        if not self._state.adding:
            old_instance = BerthSwitchOffer.objects.get(id=self.id)

            # if due_date change is allowed
            self._check_due_date(old_instance)

    def _check_due_date(self, old_instance) -> None:
        if (
            old_instance.due_date != self.due_date
            and old_instance.status == OfferStatus.EXPIRED
        ):
            raise ValidationError(_("Cannot change due date of this offer"))

    def set_status(self, new_status: OfferStatus, comment: str = None) -> None:
        old_status = self.status
        if new_status == old_status:
            return

        valid_status_changes = {
            OfferStatus.DRAFTED: (
                OfferStatus.OFFERED,
                OfferStatus.CANCELLED,
                OfferStatus.EXPIRED,
            ),
            OfferStatus.OFFERED: (
                OfferStatus.ACCEPTED,
                OfferStatus.REJECTED,
                OfferStatus.EXPIRED,
                OfferStatus.CANCELLED,
            ),
        }
        valid_new_status = valid_status_changes.get(old_status, ())

        if new_status not in valid_new_status:
            raise OrderStatusTransitionError(
                'Cannot set offer {} state to "{}", it is in an invalid state "{}".'.format(
                    self.id, new_status, old_status
                )
            )

        self.status = new_status
        self.save(update_fields=["status"])

        self.create_log_entry(
            from_status=old_status, to_status=new_status, comment=comment
        )

    def create_log_entry(
        self,
        from_status: OfferStatus = None,
        to_status: OfferStatus = None,
        comment: str = "",
    ) -> None:
        BerthSwitchOfferLogEntry.objects.create(
            offer=self,
            from_status=from_status,
            to_status=to_status or self.status,
            comment=comment,
        )


class AbstractOfferLogEntry(UUIDModel, TimeStampedModel):
    from_status = models.CharField(choices=OfferStatus.choices, max_length=9)
    to_status = models.CharField(choices=OfferStatus.choices, max_length=9)
    comment = models.TextField(blank=True, null=True)

    class Meta:
        abstract = True


class BerthSwitchOfferLogEntry(AbstractOfferLogEntry):
    offer = models.ForeignKey(
        BerthSwitchOffer,
        verbose_name=_("order"),
        related_name="log_entries",
        on_delete=models.CASCADE,
    )

    class Meta:
        verbose_name_plural = _("berth switch offer log entries")

    def __str__(self):
        return (
            f"Offer {self.offer.id} | {self.from_status or 'N/A'} --> {self.to_status}"
        )
