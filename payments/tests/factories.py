import random
from decimal import Decimal

import factory

from berth_reservations.tests.factories import CustomerProfileFactory
from leases.tests.factories import BerthLeaseFactory, WinterStorageLeaseFactory
from resources.tests.factories import HarborFactory, WinterStorageAreaFactory

from ..enums import OrderStatus, PeriodType, PriceUnits, ProductServiceType
from ..models import (
    AbstractBaseProduct,
    ADDITIONAL_PRODUCT_TAX_PERCENTAGES,
    AdditionalProduct,
    BerthPriceGroup,
    BerthProduct,
    DEFAULT_TAX_PERCENTAGE,
    Order,
    OrderLine,
    OrderLogEntry,
    PLACE_PRODUCT_TAX_PERCENTAGES,
    WinterStorageProduct,
)
from .utils import random_bool, random_price


class AbstractBaseProductFactory(factory.django.DjangoModelFactory):
    price_value = factory.LazyFunction(
        lambda: round(Decimal(random.uniform(1, 999)), 2)
    )
    price_unit = factory.Faker("random_element", elements=list(PriceUnits))

    class Meta:
        model = AbstractBaseProduct


class BerthPriceGroupFactory(factory.django.DjangoModelFactory):
    name = factory.Faker("word")

    class Meta:
        model = BerthPriceGroup


class AbstractPlaceProductFactory(AbstractBaseProductFactory):
    price_unit = factory.LazyFunction(lambda: PriceUnits.AMOUNT)
    tax_percentage = factory.Faker(
        "random_element", elements=PLACE_PRODUCT_TAX_PERCENTAGES
    )


class BerthProductFactory(AbstractPlaceProductFactory):
    price_group = factory.SubFactory(BerthPriceGroupFactory)
    harbor = factory.SubFactory(HarborFactory)

    class Meta:
        model = BerthProduct


class WinterStorageProductFactory(AbstractPlaceProductFactory):
    winter_storage_area = factory.SubFactory(WinterStorageAreaFactory)

    class Meta:
        model = WinterStorageProduct


class AdditionalProductFactory(AbstractBaseProductFactory):
    service = factory.Faker("random_element", elements=list(ProductServiceType))
    period = factory.LazyFunction(lambda: PeriodType.SEASON)
    tax_percentage = factory.LazyFunction(lambda: DEFAULT_TAX_PERCENTAGE)

    # Because of the FIXED_SERVICE restrictions (only allowed to have 24% VAT)
    # the actual assignment of a random Tax value is done once the service has
    # been assigned to the model.
    @factory.post_generation
    def service_taxes(self, created, extracted, **kwargs):
        if self.price_unit == PriceUnits.PERCENTAGE:
            self.price_value = random_price(1, 100, decimals=0)

        if self.service.is_fixed_service():
            self.tax_percentage = DEFAULT_TAX_PERCENTAGE
        else:
            self.tax_percentage = random.choice(ADDITIONAL_PRODUCT_TAX_PERCENTAGES)

    class Meta:
        model = AdditionalProduct


class OrderFactory(factory.django.DjangoModelFactory):
    customer = factory.SubFactory(CustomerProfileFactory)
    product = factory.LazyFunction(
        lambda: BerthProductFactory()
        if random_bool()
        else WinterStorageProductFactory()
    )
    price = None
    tax_percentage = None
    lease = None
    status = factory.Faker("random_element", elements=list(OrderStatus))
    comment = factory.Faker("sentence")

    @factory.post_generation
    def lease(self, created, extracted, **kwargs):
        if extracted:
            self.lease = extracted
        elif isinstance(self.product, BerthProduct):
            self.lease = BerthLeaseFactory(customer=self.customer)
        else:
            self.lease = WinterStorageLeaseFactory(customer=self.customer)

    class Meta:
        model = Order


class OrderLineFactory(factory.django.DjangoModelFactory):
    order = factory.SubFactory(OrderFactory)
    product = factory.SubFactory(AdditionalProductFactory)

    class Meta:
        model = OrderLine


class OrderLogEntryFactory(factory.django.DjangoModelFactory):
    order = factory.SubFactory(OrderFactory)
    status = factory.Faker("random_element", elements=list(OrderStatus))
    comment = factory.Faker("sentence")

    class Meta:
        model = OrderLogEntry
