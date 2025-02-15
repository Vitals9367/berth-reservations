import uuid
from random import randint
from unittest import mock

import pytest
from babel.dates import format_date
from dateutil.relativedelta import relativedelta
from dateutil.utils import today
from django.core import mail
from freezegun import freeze_time

from applications.enums import ApplicationStatus
from applications.schema import BerthApplicationNode, WinterStorageApplicationNode
from applications.tests.factories import (
    BerthApplicationFactory,
    WinterStorageApplicationFactory,
)
from berth_reservations.tests.utils import (
    assert_doesnt_exist,
    assert_in_errors,
    assert_not_enough_permissions,
)
from contracts.models import BerthContract, WinterStorageContract
from contracts.schema.types import BerthContractNode
from contracts.tests.factories import BerthContractFactory
from customers.schema import BoatNode, ProfileNode
from customers.tests.conftest import mocked_response_profile
from customers.tests.factories import BoatFactory
from payments.enums import OrderStatus
from payments.models import BerthProduct, Order
from payments.schema import BerthProductNode
from payments.tests.factories import BerthProductFactory, WinterStorageProductFactory
from payments.tests.utils import get_berth_lease_pricing_category
from resources.enums import BerthMooringType
from resources.schema import BerthNode, WinterStoragePlaceNode, WinterStorageSectionNode
from resources.tests.factories import BerthFactory, BoatTypeFactory
from utils.numbers import rounded
from utils.relay import to_global_id

from ..enums import LeaseStatus
from ..models import Berth, BerthLease, WinterStorageLease, WinterStoragePlace
from ..schema import BerthLeaseNode, WinterStorageLeaseNode
from ..utils import (
    calculate_berth_lease_end_date,
    calculate_berth_lease_start_date,
    calculate_winter_storage_lease_end_date,
    calculate_winter_storage_lease_start_date,
)
from .factories import BerthLeaseFactory, WinterStorageLeaseFactory
from .utils import create_berth_products, create_winter_storage_product

CREATE_BERTH_LEASE_MUTATION = """
mutation CreateBerthLease($input: CreateBerthLeaseMutationInput!) {
    createBerthLease(input:$input){
        berthLease {
            id
            startDate
            endDate
            customer {
              id
            }
            boat {
              id
            }
            status
            comment
            berth {
                id
            }
            application {
                id
                status
            }
        }
    }
}
"""


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
@freeze_time("2020-01-01T08:00:00Z")
def test_create_berth_lease(api_client, berth, customer_profile):
    berth_application = BerthApplicationFactory(customer=customer_profile)
    BoatTypeFactory(id=berth_application.boat.boat_type.id)
    create_berth_products(berth)

    variables = {
        "applicationId": to_global_id(BerthApplicationNode, berth_application.id),
        "berthId": to_global_id(BerthNode, berth.id),
    }

    assert BerthLease.objects.count() == 0
    assert customer_profile.boats.count() == 1

    executed = api_client.execute(CREATE_BERTH_LEASE_MUTATION, input=variables)

    assert BerthLease.objects.count() == 1
    assert customer_profile.boats.count() == 1

    boat = customer_profile.boats.first()
    assert boat.owner == berth_application.customer

    assert executed["data"]["createBerthLease"]["berthLease"].pop("id") is not None
    assert executed["data"]["createBerthLease"]["berthLease"] == {
        "status": "DRAFTED",
        "startDate": "2020-06-10",
        "endDate": "2020-09-14",
        "comment": "",
        "boat": {"id": to_global_id(BoatNode, boat.id)},
        "customer": {"id": to_global_id(ProfileNode, berth_application.customer.id)},
        "application": {
            "id": variables.get("applicationId"),
            "status": ApplicationStatus.OFFER_GENERATED.name,
        },
        "berth": {"id": variables.get("berthId")},
    }


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
@freeze_time("2020-01-01T08:00:00Z")
def test_create_berth_lease_all_arguments(api_client, berth, customer_profile):
    berth_application = BerthApplicationFactory(customer=customer_profile)
    create_berth_products(berth)

    variables = {
        "applicationId": to_global_id(BerthApplicationNode, berth_application.id),
        "berthId": to_global_id(BerthNode, berth.id),
        "boatId": to_global_id(BoatNode, berth_application.boat.id),
        "startDate": "2020-03-01",
        "endDate": "2020-12-31",
        "comment": "Very wow, such comment",
    }

    assert BerthLease.objects.count() == 0

    executed = api_client.execute(CREATE_BERTH_LEASE_MUTATION, input=variables)

    assert BerthLease.objects.count() == 1

    assert executed["data"]["createBerthLease"]["berthLease"].pop("id") is not None
    assert executed["data"]["createBerthLease"]["berthLease"] == {
        "status": "DRAFTED",
        "startDate": variables.get("startDate"),
        "endDate": variables.get("endDate"),
        "comment": variables.get("comment"),
        "boat": {"id": variables.get("boatId")},
        "customer": {"id": to_global_id(ProfileNode, berth_application.customer.id)},
        "application": {
            "id": variables.get("applicationId"),
            "status": ApplicationStatus.OFFER_GENERATED.name,
        },
        "berth": {"id": variables.get("berthId")},
    }


@pytest.mark.parametrize(
    "api_client",
    ["api_client", "user", "harbor_services", "berth_supervisor"],
    indirect=True,
)
def test_create_berth_lease_not_enough_permissions(
    api_client, berth_application, berth
):
    variables = {
        "applicationId": to_global_id(BerthApplicationNode, berth_application.id),
        "berthId": to_global_id(BerthNode, berth.id),
    }

    executed = api_client.execute(CREATE_BERTH_LEASE_MUTATION, input=variables)

    assert_not_enough_permissions(executed)


def test_create_berth_lease_application_doesnt_exist(superuser_api_client, berth):
    variables = {
        "applicationId": to_global_id(BerthApplicationNode, randint(0, 999)),
        "berthId": to_global_id(BerthNode, berth.id),
    }

    executed = superuser_api_client.execute(
        CREATE_BERTH_LEASE_MUTATION,
        input=variables,
    )

    assert_doesnt_exist("BerthApplication", executed)


def test_create_berth_lease_berth_doesnt_exist(
    superuser_api_client, berth_application, customer_profile
):
    BoatTypeFactory(id=berth_application.boat_type.id)
    berth_application = BerthApplicationFactory(customer=customer_profile)
    variables = {
        "applicationId": to_global_id(BerthApplicationNode, berth_application.id),
        "berthId": to_global_id(BerthNode, uuid.uuid4()),
    }

    executed = superuser_api_client.execute(
        CREATE_BERTH_LEASE_MUTATION,
        input=variables,
    )

    assert_doesnt_exist("Berth", executed)


def test_create_berth_lease_application_id_missing(superuser_api_client):
    variables = {
        "berthId": to_global_id(BerthNode, uuid.uuid4()),
    }

    executed = superuser_api_client.execute(
        CREATE_BERTH_LEASE_MUTATION,
        input=variables,
    )

    assert_in_errors("Must specify either application or customer", executed)


def test_create_berth_lease_berth_id_missing(superuser_api_client, customer_profile):
    variables = {
        "customerId": to_global_id(ProfileNode, customer_profile.id),
        "berthId": to_global_id(BerthApplicationNode, randint(0, 999)),
    }

    executed = superuser_api_client.execute(
        CREATE_BERTH_LEASE_MUTATION,
        input=variables,
    )
    assert_in_errors("Must receive a BerthNode", executed)


def test_create_berth_lease_application_without_customer(
    superuser_api_client, berth_application, berth
):
    berth_application.customer = None
    berth_application.save()

    variables = {
        "applicationId": to_global_id(BerthApplicationNode, berth_application.id),
        "berthId": to_global_id(BerthNode, berth.id),
    }

    executed = superuser_api_client.execute(
        CREATE_BERTH_LEASE_MUTATION,
        input=variables,
    )

    assert_in_errors(
        "Application must be connected to an existing customer first", executed
    )


def test_create_berth_lease_application_already_has_lease(
    superuser_api_client,
    berth_application,
    berth,
    customer_profile,
):
    berth_application = BerthApplicationFactory(customer=customer_profile)
    BoatTypeFactory(id=berth_application.boat.boat_type.id)
    BerthLeaseFactory(application=berth_application)

    variables = {
        "applicationId": to_global_id(BerthApplicationNode, berth_application.id),
        "berthId": to_global_id(BerthNode, berth.id),
    }

    executed = superuser_api_client.execute(
        CREATE_BERTH_LEASE_MUTATION,
        input=variables,
    )

    assert_in_errors("Berth lease with this Application already exists", executed)


CREATE_BERTH_LEASE_WITH_ORDER_MUTATION = """
mutation CreateBerthLease($input: CreateBerthLeaseMutationInput!) {
    createBerthLease(input:$input){
        berthLease {
            id
            berth {
                id
            }
            order {
                id
                price
                status
                customer {
                    id
                }
                product {
                    ... on BerthProductNode {
                        id
                    }
                    ... on WinterStorageProductNode {
                        id
                    }
                }
            }
        }
    }
}
"""


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
@freeze_time("2020-01-01T08:00:00Z")
def test_create_berth_lease_with_order(api_client, customer_profile):
    berth_application = BerthApplicationFactory(customer=customer_profile)
    BoatTypeFactory(id=berth_application.boat_type.id)
    berth = BerthFactory(berth_type__mooring_type=BerthMooringType.QUAYSIDE_MOORING)
    min_width = berth.berth_type.width - 1
    max_width = berth.berth_type.width + 1
    berth_product = BerthProductFactory(min_width=min_width, max_width=max_width)
    expected_product = BerthProduct.objects.get_in_range(berth.berth_type.width)

    variables = {
        "applicationId": to_global_id(BerthApplicationNode, berth_application.id),
        "berthId": to_global_id(BerthNode, berth.id),
    }

    assert BerthLease.objects.count() == 0
    assert Order.objects.count() == 0

    executed = api_client.execute(
        CREATE_BERTH_LEASE_WITH_ORDER_MUTATION, input=variables
    )

    assert BerthLease.objects.count() == 1
    assert Order.objects.count() == 1

    assert executed["data"]["createBerthLease"]["berthLease"].pop("id") is not None
    assert (
        executed["data"]["createBerthLease"]["berthLease"]["order"].pop("id")
        is not None
    )
    assert executed["data"]["createBerthLease"]["berthLease"] == {
        "berth": {"id": variables["berthId"]},
        "order": {
            "price": str(berth_product.price_for_tier(tier=berth.pier.price_tier)),
            "status": "DRAFTED",
            "customer": {"id": to_global_id(ProfileNode, customer_profile.id)},
            "product": {"id": to_global_id(BerthProductNode, expected_product.id)},
        },
    }


CREATE_BERTH_LEASE_WITHOUT_APPLICATION_MUTATION = """
mutation CreateBerthLease($input: CreateBerthLeaseMutationInput!) {
    createBerthLease(input:$input){
        berthLease {
            id
            status
            application {
                id
            }
            customer {
                id
            }
            berth {
                id
            }
            order {
                id
                price
                status
                customer {
                    id
                }
            }
        }
    }
}
"""


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
@freeze_time("2020-01-01T08:00:00Z")
def test_create_berth_lease_without_application(api_client, berth, customer_profile):
    create_berth_products(berth)

    variables = {
        "customerId": to_global_id(ProfileNode, customer_profile.id),
        "berthId": to_global_id(BerthNode, berth.id),
    }

    assert BerthLease.objects.count() == 0
    assert Order.objects.count() == 0

    executed = api_client.execute(
        CREATE_BERTH_LEASE_WITHOUT_APPLICATION_MUTATION, input=variables
    )

    assert BerthLease.objects.count() == 1
    assert Order.objects.count() == 1
    expected_product = BerthProduct.objects.get_in_range(
        berth.berth_type.width,
        get_berth_lease_pricing_category(BerthLease.objects.first()),
    )

    assert executed["data"]["createBerthLease"]["berthLease"].pop("id") is not None
    assert (
        executed["data"]["createBerthLease"]["berthLease"]["order"].pop("id")
        is not None
    )
    assert executed["data"]["createBerthLease"]["berthLease"] == {
        "status": "DRAFTED",
        "application": None,
        "customer": {"id": to_global_id(ProfileNode, customer_profile.id)},
        "berth": {"id": variables["berthId"]},
        "order": {
            "price": str(expected_product.price_for_tier(tier=berth.pier.price_tier)),
            "status": "DRAFTED",
            "customer": {"id": to_global_id(ProfileNode, customer_profile.id)},
        },
    }


def test_create_berth_lease_application_and_customer_mutually_exclusive(
    superuser_api_client, berth, customer_profile
):
    variables = {
        "applicationId": to_global_id(BerthApplicationNode, randint(0, 999)),
        "berthId": to_global_id(BerthNode, berth.id),
        "customerId": to_global_id(ProfileNode, customer_profile.id),
    }

    executed = superuser_api_client.execute(
        CREATE_BERTH_LEASE_MUTATION,
        input=variables,
    )

    assert_in_errors("Can not specify both application and customer", executed)


DELETE_BERTH_LEASE_MUTATION = """
mutation DELETE_DRAFTED_LEASE($input: DeleteBerthLeaseMutationInput!) {
    deleteBerthLease(input: $input) {
        __typename
    }
}
"""


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_delete_berth_lease_drafted(berth_lease, berth_application, api_client):
    variables = {"id": to_global_id(BerthLeaseNode, berth_lease.id)}
    berth_lease.application = berth_application
    berth_lease.save()

    assert BerthLease.objects.count() == 1

    api_client.execute(
        DELETE_BERTH_LEASE_MUTATION,
        input=variables,
    )

    assert BerthLease.objects.count() == 0
    assert berth_application.status == ApplicationStatus.PENDING


def test_delete_berth_lease_not_drafted(berth_lease, superuser_api_client):
    berth_lease.status = LeaseStatus.OFFERED
    berth_lease.save()

    variables = {"id": to_global_id(BerthLeaseNode, berth_lease.id)}

    assert BerthLease.objects.count() == 1

    executed = superuser_api_client.execute(
        DELETE_BERTH_LEASE_MUTATION,
        input=variables,
    )

    assert BerthLease.objects.count() == 1
    assert_in_errors(
        f"Lease object is not DRAFTED anymore: {LeaseStatus.OFFERED}", executed
    )


@pytest.mark.parametrize(
    "api_client",
    ["api_client", "user", "harbor_services", "berth_supervisor"],
    indirect=True,
)
def test_delete_berth_lease_not_enough_permissions(api_client, berth_lease):
    variables = {
        "id": to_global_id(BerthLeaseNode, berth_lease.id),
    }

    assert BerthLease.objects.count() == 1

    executed = api_client.execute(DELETE_BERTH_LEASE_MUTATION, input=variables)

    assert BerthLease.objects.count() == 1
    assert_not_enough_permissions(executed)


def test_delete_berth_lease_inexistent_lease(superuser_api_client):
    variables = {
        "id": to_global_id(BerthLeaseNode, uuid.uuid4()),
    }

    executed = superuser_api_client.execute(
        DELETE_BERTH_LEASE_MUTATION,
        input=variables,
    )

    assert_doesnt_exist("BerthLease", executed)


UPDATE_BERTH_LEASE_MUTATION = """
mutation UpdateBerthLease($input: UpdateBerthLeaseMutationInput!) {
    updateBerthLease(input:$input){
        berthLease {
            id
            startDate
            endDate
            comment
            boat {
                id
            }
            application {
                id
                customer {
                    id
                }
            }
        }
    }
}
"""


@freeze_time("2020-01-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_update_berth_lease_all_fields(api_client, berth_lease, customer_profile):
    berth_application = BerthApplicationFactory(
        customer=customer_profile, boat=BoatFactory(owner=customer_profile)
    )
    berth_lease_id = to_global_id(BerthLeaseNode, berth_lease.id)
    application_id = to_global_id(BerthApplicationNode, berth_application.id)
    boat_id = to_global_id(BoatNode, berth_application.boat.id)

    start_date = today()
    end_date = start_date + relativedelta(months=3)

    variables = {
        "id": berth_lease_id,
        "startDate": start_date,
        "endDate": end_date,
        "comment": "",
        "boatId": boat_id,
        "applicationId": application_id,
    }

    executed = api_client.execute(UPDATE_BERTH_LEASE_MUTATION, input=variables)
    assert executed["data"]["updateBerthLease"]["berthLease"] == {
        "id": berth_lease_id,
        "startDate": str(variables["startDate"].date()),
        "endDate": str(variables["endDate"].date()),
        "comment": variables["comment"],
        "boat": {"id": boat_id},
        "application": {
            "id": application_id,
            "customer": {
                "id": to_global_id(ProfileNode, berth_application.customer.id),
            },
        },
    }


@freeze_time("2020-01-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_update_berth_lease_remove_application(
    api_client, berth_lease, berth_application
):
    berth_lease_id = to_global_id(BerthLeaseNode, berth_lease.id)
    boat_id = to_global_id(BoatNode, berth_lease.boat.id)
    berth_lease.application = berth_application
    berth_lease.save()

    variables = {
        "id": berth_lease_id,
        "applicationId": None,
    }

    executed = api_client.execute(UPDATE_BERTH_LEASE_MUTATION, input=variables)
    assert executed["data"]["updateBerthLease"]["berthLease"] == {
        "id": berth_lease_id,
        "startDate": str(berth_lease.start_date),
        "endDate": str(berth_lease.end_date),
        "comment": berth_lease.comment,
        "boat": {"id": boat_id},
        "application": None,
    }


def test_update_berth_lease_application_doesnt_exist(superuser_api_client, berth_lease):
    variables = {
        "id": to_global_id(BerthLeaseNode, berth_lease.id),
        "applicationId": to_global_id(BerthApplicationNode, randint(0, 999)),
    }

    executed = superuser_api_client.execute(
        UPDATE_BERTH_LEASE_MUTATION,
        input=variables,
    )

    assert_doesnt_exist("BerthApplication", executed)


def test_update_berth_lease_application_without_customer(
    superuser_api_client, berth_lease, berth_application
):
    berth_application.customer = None
    berth_application.save()

    variables = {
        "id": to_global_id(BerthLeaseNode, berth_lease.id),
        "applicationId": to_global_id(BerthApplicationNode, berth_application.id),
    }

    executed = superuser_api_client.execute(
        UPDATE_BERTH_LEASE_MUTATION,
        input=variables,
    )

    assert_in_errors(
        "Application must be connected to an existing customer first", executed
    )


def test_update_berth_lease_application_already_has_lease(
    superuser_api_client,
    berth_lease,
    customer_profile,
):
    berth_application = BerthApplicationFactory(customer=customer_profile)
    BerthLeaseFactory(application=berth_application)

    variables = {
        "id": to_global_id(BerthLeaseNode, berth_lease.id),
        "applicationId": to_global_id(BerthApplicationNode, berth_application.id),
    }

    executed = superuser_api_client.execute(
        UPDATE_BERTH_LEASE_MUTATION,
        input=variables,
    )

    assert_in_errors("Berth lease with this Application already exists", executed)


SWITCH_BERTH_MUTATION = """
mutation SwitchBerth($input: SwitchBerthMutationInput!) {
    switchBerth(input:$input){
        oldBerthLease {
            id
            startDate
            endDate
            comment
            boat {
                id
            }
            application {
                id
                customer {
                    id
                }
            }
            contract {
                id
            }
            status
        }
        newBerthLease {
            id
            startDate
            endDate
            comment
            boat {
                id
            }
            application {
                id
                customer {
                    id
                }
            }
            contract {
                id
            }
            status
        }
    }
}
"""


@freeze_time("2020-07-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_switch_berth(api_client, berth):
    berth_lease = BerthLeaseFactory(
        start_date=calculate_berth_lease_start_date(),
        end_date=calculate_berth_lease_end_date(),
        application=BerthApplicationFactory(email="foo@email.com", language="fi"),
        status=LeaseStatus.PAID,
    )
    berth_lease.contract = BerthContractFactory()
    berth_lease.contract.save()

    old_lease_id = to_global_id(BerthLeaseNode, berth_lease.id)
    new_berth_id = to_global_id(BerthNode, berth.id)

    variables = {
        "oldLeaseId": old_lease_id,
        "newBerthId": new_berth_id,
    }

    executed = api_client.execute(SWITCH_BERTH_MUTATION, input=variables)
    assert executed["data"]["switchBerth"]["oldBerthLease"] == {
        "id": old_lease_id,
        "startDate": str(berth_lease.start_date),
        "endDate": str(today().date()),
        "comment": f"{berth_lease.comment}\nLease terminated due to berth switch",
        "boat": {"id": to_global_id(BoatNode, berth_lease.boat_id)},
        "application": {
            "customer": None,
            "id": to_global_id(BerthApplicationNode, berth_lease.application_id),
        },
        "contract": None,
        "status": LeaseStatus.TERMINATED.name,
    }

    comment = (
        f"Lease created from a berth switch\n"
        f"Previous berth info:\n"
        f"Harbor name: {berth_lease.berth.pier.harbor.name}\n"
        f"Pier ID: {berth_lease.berth.pier.identifier}\n"
        f"Berth number: {berth_lease.berth.number}\n"
    )
    new_lease = BerthLease.objects.get(berth=berth)
    new_lease_id = to_global_id(BerthLeaseNode, new_lease.id)
    assert executed["data"]["switchBerth"]["newBerthLease"] == {
        "id": new_lease_id,
        "startDate": str(today().date()),
        "endDate": str(berth_lease.end_date),
        "comment": comment,
        "boat": {"id": to_global_id(BoatNode, berth_lease.boat_id)},
        "application": None,
        "contract": {"id": to_global_id(BerthContractNode, berth_lease.contract.id)},
        "status": LeaseStatus.PAID.name,
    }


@freeze_time("2020-07-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_switch_berth_with_switch_date(api_client, berth):
    berth_lease = BerthLeaseFactory(
        start_date=calculate_berth_lease_start_date(),
        end_date=calculate_berth_lease_end_date(),
        application=BerthApplicationFactory(email="foo@email.com", language="fi"),
        status=LeaseStatus.PAID,
    )
    berth_lease.contract = BerthContractFactory()
    berth_lease.contract.save()

    old_lease_id = to_global_id(BerthLeaseNode, berth_lease.id)
    new_berth_id = to_global_id(BerthNode, berth.id)

    switch_date = today().date() + relativedelta(months=1)

    variables = {
        "oldLeaseId": old_lease_id,
        "newBerthId": new_berth_id,
        "switchDate": switch_date,
    }

    executed = api_client.execute(SWITCH_BERTH_MUTATION, input=variables)
    assert executed["data"]["switchBerth"]["oldBerthLease"] == {
        "id": old_lease_id,
        "startDate": str(berth_lease.start_date),
        "endDate": str(switch_date),
        "comment": f"{berth_lease.comment}\nLease terminated due to berth switch",
        "boat": {"id": to_global_id(BoatNode, berth_lease.boat_id)},
        "application": {
            "customer": None,
            "id": to_global_id(BerthApplicationNode, berth_lease.application_id),
        },
        "contract": None,
        "status": LeaseStatus.TERMINATED.name,
    }

    comment = (
        f"Lease created from a berth switch\n"
        f"Previous berth info:\n"
        f"Harbor name: {berth_lease.berth.pier.harbor.name}\n"
        f"Pier ID: {berth_lease.berth.pier.identifier}\n"
        f"Berth number: {berth_lease.berth.number}\n"
    )
    new_lease = BerthLease.objects.get(berth=berth)
    new_lease_id = to_global_id(BerthLeaseNode, new_lease.id)
    assert executed["data"]["switchBerth"]["newBerthLease"] == {
        "id": new_lease_id,
        "startDate": str(switch_date),
        "endDate": str(berth_lease.end_date),
        "comment": comment,
        "boat": {"id": to_global_id(BoatNode, berth_lease.boat_id)},
        "application": None,
        "contract": {"id": to_global_id(BerthContractNode, berth_lease.contract.id)},
        "status": LeaseStatus.PAID.name,
    }


@freeze_time("2020-07-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_switch_berth_without_contract(api_client, berth):
    berth_lease = BerthLeaseFactory(
        start_date=calculate_berth_lease_start_date(),
        end_date=calculate_berth_lease_end_date(),
        application=BerthApplicationFactory(email="foo@email.com", language="fi"),
        status=LeaseStatus.PAID,
    )
    berth_lease.contract = None
    berth_lease.save()

    old_lease_id = to_global_id(BerthLeaseNode, berth_lease.id)

    new_berth_id = to_global_id(BerthNode, berth.id)

    variables = {
        "oldLeaseId": old_lease_id,
        "newBerthId": new_berth_id,
    }

    executed = api_client.execute(SWITCH_BERTH_MUTATION, input=variables)
    assert executed["data"]["switchBerth"]["oldBerthLease"] == {
        "id": old_lease_id,
        "startDate": str(berth_lease.start_date),
        "endDate": str(today().date()),
        "comment": f"{berth_lease.comment}\nLease terminated due to berth switch",
        "boat": {"id": to_global_id(BoatNode, berth_lease.boat_id)},
        "application": {
            "customer": None,
            "id": to_global_id(BerthApplicationNode, berth_lease.application_id),
        },
        "contract": None,
        "status": LeaseStatus.TERMINATED.name,
    }

    comment = (
        f"Lease created from a berth switch\n"
        f"Previous berth info:\n"
        f"Harbor name: {berth_lease.berth.pier.harbor.name}\n"
        f"Pier ID: {berth_lease.berth.pier.identifier}\n"
        f"Berth number: {berth_lease.berth.number}\n"
    )
    new_lease = BerthLease.objects.get(berth=berth)
    new_lease_id = to_global_id(BerthLeaseNode, new_lease.id)
    assert executed["data"]["switchBerth"]["newBerthLease"] == {
        "id": new_lease_id,
        "startDate": str(today().date()),
        "endDate": str(berth_lease.end_date),
        "comment": comment,
        "boat": {"id": to_global_id(BoatNode, berth_lease.boat_id)},
        "application": None,
        "contract": None,
        "status": LeaseStatus.PAID.name,
    }


@freeze_time("2020-07-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_switch_berth_with_berth_already_has_lease(api_client):
    old_lease = BerthLeaseFactory(
        start_date=calculate_berth_lease_start_date(),
        end_date=calculate_berth_lease_end_date(),
        application=BerthApplicationFactory(email="foo@email.com", language="fi"),
        status=LeaseStatus.PAID,
    )
    old_lease.contract = BerthContractFactory()
    old_lease.contract.save()

    old_lease_id = to_global_id(BerthLeaseNode, old_lease.id)

    existing_lease = BerthLeaseFactory(
        start_date=calculate_berth_lease_start_date(),
        end_date=calculate_berth_lease_end_date(),
    )

    new_berth_id = to_global_id(BerthNode, existing_lease.berth.id)

    variables = {
        "oldLeaseId": old_lease_id,
        "newBerthId": new_berth_id,
    }

    executed = api_client.execute(SWITCH_BERTH_MUTATION, input=variables)

    assert_in_errors("Berth already has a lease", executed)


@freeze_time("2020-07-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_switch_berth_non_paid_status(api_client, berth):
    berth_lease = BerthLeaseFactory(
        start_date=calculate_berth_lease_start_date(),
        end_date=calculate_berth_lease_end_date(),
        application=BerthApplicationFactory(email="foo@email.com", language="fi"),
        status=LeaseStatus.OFFERED,
    )
    berth_lease.contract = BerthContractFactory()
    berth_lease.contract.save()

    old_lease_id = to_global_id(BerthLeaseNode, berth_lease.id)

    new_berth_id = to_global_id(BerthNode, berth.id)

    variables = {
        "oldLeaseId": old_lease_id,
        "newBerthId": new_berth_id,
    }

    executed = api_client.execute(SWITCH_BERTH_MUTATION, input=variables)

    assert_in_errors(f"Lease is not paid: {berth_lease.status}", executed)


@freeze_time("2020-07-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_switch_berth_non_active(api_client, berth):
    berth_lease = BerthLeaseFactory(
        start_date=today() - relativedelta(months=12),
        end_date=today() - relativedelta(months=10),
        application=BerthApplicationFactory(email="foo@email.com", language="fi"),
        status=LeaseStatus.PAID,
    )
    berth_lease.contract = BerthContractFactory()
    berth_lease.contract.save()

    old_lease_id = to_global_id(BerthLeaseNode, berth_lease.id)

    new_berth_id = to_global_id(BerthNode, berth.id)

    variables = {
        "oldLeaseId": old_lease_id,
        "newBerthId": new_berth_id,
    }

    executed = api_client.execute(SWITCH_BERTH_MUTATION, input=variables)

    assert_in_errors("Berth lease is not active", executed)


@freeze_time("2020-07-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_switch_berth_non_existing_berth_id(api_client):
    old_lease_id = to_global_id(BerthLeaseNode, uuid.uuid4())

    new_berth_id = to_global_id(BerthNode, uuid.uuid4())

    variables = {
        "oldLeaseId": old_lease_id,
        "newBerthId": new_berth_id,
    }

    executed = api_client.execute(SWITCH_BERTH_MUTATION, input=variables)

    assert_in_errors("BerthLease matching query does not exist.", executed)


@freeze_time("2020-07-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["api_client", "user", "harbor_services", "berth_supervisor"],
    indirect=True,
)
def test_switch_berth_not_enough_permissions(api_client):
    old_lease_id = to_global_id(BerthLeaseNode, uuid.uuid4())
    new_berth_id = to_global_id(BerthNode, uuid.uuid4())

    variables = {
        "oldLeaseId": old_lease_id,
        "newBerthId": new_berth_id,
    }

    executed = api_client.execute(SWITCH_BERTH_MUTATION, input=variables)

    assert_not_enough_permissions(executed)


@freeze_time("2020-08-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_switch_berth_with_switch_date_over_6_months_in_the_past(api_client):
    old_lease_id = to_global_id(BerthLeaseNode, uuid.uuid4())
    new_berth_id = to_global_id(BerthNode, uuid.uuid4())

    switch_date = today().date() - relativedelta(months=7)

    variables = {
        "oldLeaseId": old_lease_id,
        "newBerthId": new_berth_id,
        "switchDate": switch_date,
    }

    executed = api_client.execute(SWITCH_BERTH_MUTATION, input=variables)

    assert_in_errors("Switch date is more than 6 months in the past", executed)


CREATE_WINTER_STORAGE_LEASE_MUTATION = """
mutation CreateWinterStorageLease($input: CreateWinterStorageLeaseMutationInput!) {
    createWinterStorageLease(input:$input){
        winterStorageLease {
            id
            startDate
            endDate
            customer {
              id
            }
            boat {
              id
            }
            status
            comment
            place {
                id
            }
            section {
                id
            }
            application {
                id
                status
            }
            order {
                id
            }
        }
    }
}
"""


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_create_winter_storage_lease(
    api_client, winter_storage_place, customer_profile
):
    winter_storage_application = WinterStorageApplicationFactory(
        customer=customer_profile
    )
    BoatTypeFactory(id=winter_storage_application.boat_type.id)
    create_winter_storage_product(winter_storage_place.winter_storage_section.area)

    variables = {
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
        "placeId": to_global_id(WinterStoragePlaceNode, winter_storage_place.id),
    }

    assert WinterStorageLease.objects.count() == 0
    assert customer_profile.boats.count() == 1

    executed = api_client.execute(CREATE_WINTER_STORAGE_LEASE_MUTATION, input=variables)

    assert WinterStorageLease.objects.count() == 1
    assert customer_profile.boats.count() == 1

    boat = customer_profile.boats.first()

    assert (
        executed["data"]["createWinterStorageLease"]["winterStorageLease"].pop("id")
        is not None
    )
    assert (
        executed["data"]["createWinterStorageLease"]["winterStorageLease"].pop("order")
        is not None
    )
    assert executed["data"]["createWinterStorageLease"]["winterStorageLease"] == {
        "status": "DRAFTED",
        "startDate": str(calculate_winter_storage_lease_start_date()),
        "endDate": str(calculate_winter_storage_lease_end_date()),
        "comment": "",
        "boat": {"id": to_global_id(BoatNode, boat.id)},
        "customer": {
            "id": to_global_id(ProfileNode, winter_storage_application.customer.id)
        },
        "application": {
            "id": variables.get("applicationId"),
            "status": ApplicationStatus.OFFER_GENERATED.name,
        },
        "place": {"id": variables.get("placeId")},
        "section": None,
    }


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_create_winter_storage_lease_with_section(
    api_client, winter_storage_section, boat
):
    create_winter_storage_product(winter_storage_section.area)
    winter_storage_application = WinterStorageApplicationFactory(customer=boat.owner)

    variables = {
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
        "sectionId": to_global_id(WinterStorageSectionNode, winter_storage_section.id),
        "boatId": to_global_id(BoatNode, boat.id),
    }

    assert WinterStorageLease.objects.count() == 0

    executed = api_client.execute(CREATE_WINTER_STORAGE_LEASE_MUTATION, input=variables)

    assert WinterStorageLease.objects.count() == 1

    assert (
        executed["data"]["createWinterStorageLease"]["winterStorageLease"].pop("id")
        is not None
    )
    assert (
        executed["data"]["createWinterStorageLease"]["winterStorageLease"].pop("order")
        is not None
    )
    assert executed["data"]["createWinterStorageLease"]["winterStorageLease"] == {
        "status": "DRAFTED",
        "startDate": str(calculate_winter_storage_lease_start_date()),
        "endDate": str(calculate_winter_storage_lease_end_date()),
        "comment": "",
        "boat": {"id": variables["boatId"]},
        "customer": {
            "id": to_global_id(ProfileNode, winter_storage_application.customer.id)
        },
        "application": {
            "id": variables.get("applicationId"),
            "status": ApplicationStatus.OFFER_GENERATED.name,
        },
        "place": None,
        "section": {"id": variables.get("sectionId")},
    }


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_create_winter_storage_lease_without_application(
    api_client, winter_storage_place, customer_profile
):
    create_winter_storage_product(winter_storage_place.winter_storage_section.area)

    variables = {
        "customerId": to_global_id(ProfileNode, customer_profile.id),
        "placeId": to_global_id(WinterStoragePlaceNode, winter_storage_place.id),
    }

    assert WinterStorageLease.objects.count() == 0

    executed = api_client.execute(CREATE_WINTER_STORAGE_LEASE_MUTATION, input=variables)

    assert WinterStorageLease.objects.count() == 1

    assert (
        executed["data"]["createWinterStorageLease"]["winterStorageLease"].pop("id")
        is not None
    )
    assert (
        executed["data"]["createWinterStorageLease"]["winterStorageLease"].pop("order")
        is not None
    )

    assert executed["data"]["createWinterStorageLease"]["winterStorageLease"] == {
        "status": "DRAFTED",
        "startDate": str(calculate_winter_storage_lease_start_date()),
        "endDate": str(calculate_winter_storage_lease_end_date()),
        "comment": "",
        "boat": None,
        "customer": {"id": to_global_id(ProfileNode, customer_profile.id)},
        "application": None,
        "place": {"id": variables.get("placeId")},
        "section": None,
    }


@pytest.mark.parametrize(
    "api_client",
    ["api_client", "user", "harbor_services", "berth_supervisor"],
    indirect=True,
)
def test_create_winter_storage_lease_not_enough_permissions(
    api_client, winter_storage_application, winter_storage_place
):
    variables = {
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
        "placeId": to_global_id(WinterStoragePlaceNode, winter_storage_place.id),
    }

    executed = api_client.execute(CREATE_WINTER_STORAGE_LEASE_MUTATION, input=variables)

    assert_not_enough_permissions(executed)


def test_create_winter_storage_lease_application_doesnt_exist(
    superuser_api_client, winter_storage_place
):
    variables = {
        "applicationId": to_global_id(WinterStorageApplicationNode, randint(0, 999)),
        "placeId": to_global_id(BerthNode, winter_storage_place.id),
    }

    executed = superuser_api_client.execute(
        CREATE_WINTER_STORAGE_LEASE_MUTATION,
        input=variables,
    )

    assert_doesnt_exist("WinterStorageApplication", executed)


def test_create_winter_storage_lease_winter_storage_place_doesnt_exist(
    superuser_api_client, customer_profile
):
    winter_storage_application = WinterStorageApplicationFactory(
        customer=customer_profile
    )
    variables = {
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
        "placeId": to_global_id(WinterStoragePlaceNode, uuid.uuid4()),
    }

    executed = superuser_api_client.execute(
        CREATE_WINTER_STORAGE_LEASE_MUTATION,
        input=variables,
    )

    assert_doesnt_exist("WinterStoragePlace", executed)


def test_create_winter_storage_lease_application_without_customer(
    superuser_api_client, winter_storage_application, winter_storage_place
):
    winter_storage_application.customer = None
    winter_storage_application.save()

    variables = {
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
        "placeId": to_global_id(BerthNode, winter_storage_place.id),
    }

    executed = superuser_api_client.execute(
        CREATE_WINTER_STORAGE_LEASE_MUTATION,
        input=variables,
    )

    assert_in_errors(
        "Application must be connected to an existing customer first", executed
    )


def test_create_winter_storage_lease_application_already_has_lease(
    superuser_api_client,
    winter_storage_place,
    customer_profile,
):
    winter_storage_application = WinterStorageApplicationFactory(
        customer=customer_profile
    )
    BoatTypeFactory(id=winter_storage_application.boat_type.id)
    WinterStorageLeaseFactory(application=winter_storage_application)

    variables = {
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
        "placeId": to_global_id(WinterStoragePlaceNode, winter_storage_place.id),
    }

    executed = superuser_api_client.execute(
        CREATE_WINTER_STORAGE_LEASE_MUTATION,
        input=variables,
    )

    assert_in_errors(
        "Winter storage lease with this Application already exists", executed
    )


def test_create_winter_storage_lease_both_place_and_section(
    superuser_api_client,
    winter_storage_place,
    winter_storage_section,
    customer_profile,
):
    winter_storage_application = WinterStorageApplicationFactory(
        customer=customer_profile
    )

    variables = {
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
        "placeId": to_global_id(WinterStoragePlaceNode, winter_storage_place.id),
        "sectionId": to_global_id(WinterStorageSectionNode, winter_storage_section.id),
    }

    executed = superuser_api_client.execute(
        CREATE_WINTER_STORAGE_LEASE_MUTATION,
        input=variables,
    )

    assert_in_errors("Cannot receive both Winter Storage Place and Section", executed)


def test_create_winter_storage_lease_no_place_or_section(
    superuser_api_client,
    customer_profile,
):
    winter_storage_application = WinterStorageApplicationFactory(
        customer=customer_profile
    )

    variables = {
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
    }

    executed = superuser_api_client.execute(
        CREATE_WINTER_STORAGE_LEASE_MUTATION,
        input=variables,
    )

    assert_in_errors("Either Winter Storage Place or Section are required", executed)


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_create_winter_storage_lease_no_customer_or_application(
    api_client, winter_storage_place
):
    create_winter_storage_product(winter_storage_place.winter_storage_section.area)

    variables = {
        "placeId": to_global_id(WinterStoragePlaceNode, winter_storage_place.id),
    }

    assert WinterStorageLease.objects.count() == 0

    executed = api_client.execute(CREATE_WINTER_STORAGE_LEASE_MUTATION, input=variables)

    assert_in_errors(
        "Must specify either application or customer when creating a new berth lease",
        executed,
    )


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_create_winter_storage_lease_no_application_or_place(
    api_client, winter_storage_place, customer_profile
):
    create_winter_storage_product(winter_storage_place.winter_storage_section.area)

    variables = {"customerId": to_global_id(ProfileNode, customer_profile.id)}

    assert WinterStorageLease.objects.count() == 0

    executed = api_client.execute(CREATE_WINTER_STORAGE_LEASE_MUTATION, input=variables)

    assert_in_errors(
        "Winter Storage leases without a Place require an Application.", executed
    )


CREATE_WINTER_STORAGE_LEASE_WITH_ORDER_MUTATION = """
mutation CreateWinterStorageLease($input: CreateWinterStorageLeaseMutationInput!) {
    createWinterStorageLease(input:$input){
        winterStorageLease {
            id
            place {
                id
            }
            order {
                id
                price
                status
                customer {
                    id
                }
                product {
                    ... on WinterStorageProductNode {
                        priceUnit
                        priceValue
                    }
                }
            }
        }
    }
}
"""


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
@freeze_time("2020-06-11T08:00:00Z")
def test_create_winter_storage_lease_with_order(
    api_client, winter_storage_place, customer_profile
):
    winter_storage_application = WinterStorageApplicationFactory(
        customer=customer_profile
    )
    BoatTypeFactory(id=winter_storage_application.boat_type.id)

    product = WinterStorageProductFactory(
        winter_storage_area=winter_storage_place.winter_storage_section.area
    )

    variables = {
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
        "placeId": to_global_id(WinterStoragePlaceNode, winter_storage_place.id),
    }

    assert WinterStorageLease.objects.count() == 0
    assert Order.objects.count() == 0

    executed = api_client.execute(
        CREATE_WINTER_STORAGE_LEASE_WITH_ORDER_MUTATION, input=variables
    )

    assert WinterStorageLease.objects.count() == 1
    assert Order.objects.count() == 1
    sqm = winter_storage_place.place_type.width * winter_storage_place.place_type.length
    expected_price = product.price_value
    expected_price = rounded(expected_price * sqm, decimals=2, as_string=True)

    assert (
        executed["data"]["createWinterStorageLease"]["winterStorageLease"].pop("id")
        is not None
    )
    assert (
        executed["data"]["createWinterStorageLease"]["winterStorageLease"]["order"].pop(
            "id"
        )
        is not None
    )
    assert executed["data"]["createWinterStorageLease"]["winterStorageLease"] == {
        "place": {"id": variables["placeId"]},
        "order": {
            "price": expected_price,
            "status": "DRAFTED",
            "customer": {"id": to_global_id(ProfileNode, customer_profile.id)},
            "product": {
                "priceUnit": product.price_unit.name,
                "priceValue": str(product.price_value),
            },
        },
    }


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
@freeze_time("2020-01-01T08:00:00Z")
def test_create_winter_storage_lease_with_order_no_product(
    api_client, winter_storage_place, customer_profile
):
    winter_storage_application = WinterStorageApplicationFactory(
        customer=customer_profile
    )
    BoatTypeFactory(id=winter_storage_application.boat_type.id)

    variables = {
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
        "placeId": to_global_id(WinterStoragePlaceNode, winter_storage_place.id),
    }

    assert WinterStorageLease.objects.count() == 0
    assert Order.objects.count() == 0

    executed = api_client.execute(
        CREATE_WINTER_STORAGE_LEASE_WITH_ORDER_MUTATION, input=variables
    )

    assert WinterStorageLease.objects.count() == 0
    assert Order.objects.count() == 0
    assert_doesnt_exist("WinterStorageProduct", executed)


DELETE_WINTER_STORAGE_LEASE_MUTATION = """
mutation DELETE_DRAFTED_LEASE($input: DeleteWinterStorageLeaseMutationInput!) {
    deleteWinterStorageLease(input: $input) {
        __typename
    }
}
"""


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_delete_winter_storage_lease_drafted(
    winter_storage_lease, winter_storage_application, api_client
):
    variables = {"id": to_global_id(WinterStorageLeaseNode, winter_storage_lease.id)}
    winter_storage_lease.application = winter_storage_application
    winter_storage_lease.save()

    assert WinterStorageLease.objects.count() == 1

    api_client.execute(
        DELETE_WINTER_STORAGE_LEASE_MUTATION,
        input=variables,
    )

    assert WinterStorageLease.objects.count() == 0
    assert winter_storage_application.status == ApplicationStatus.PENDING


def test_delete_winter_storage_lease_not_drafted(
    winter_storage_lease, superuser_api_client
):
    winter_storage_lease.status = LeaseStatus.OFFERED
    winter_storage_lease.save()

    variables = {"id": to_global_id(WinterStorageLeaseNode, winter_storage_lease.id)}

    assert WinterStorageLease.objects.count() == 1

    executed = superuser_api_client.execute(
        DELETE_WINTER_STORAGE_LEASE_MUTATION,
        input=variables,
    )

    assert WinterStorageLease.objects.count() == 1
    assert_in_errors(
        f"Lease object is not DRAFTED anymore: {LeaseStatus.OFFERED}", executed
    )


@pytest.mark.parametrize(
    "api_client",
    ["api_client", "user", "harbor_services", "berth_supervisor"],
    indirect=True,
)
def test_delete_winter_storage_lease_not_enough_permissions(
    api_client, winter_storage_lease
):
    variables = {
        "id": to_global_id(BerthLeaseNode, winter_storage_lease.id),
    }

    assert WinterStorageLease.objects.count() == 1

    executed = api_client.execute(DELETE_WINTER_STORAGE_LEASE_MUTATION, input=variables)

    assert WinterStorageLease.objects.count() == 1
    assert_not_enough_permissions(executed)


def test_delete_winter_storage_lease_inexistent_lease(superuser_api_client):
    variables = {
        "id": to_global_id(WinterStorageLeaseNode, uuid.uuid4()),
    }

    executed = superuser_api_client.execute(
        DELETE_WINTER_STORAGE_LEASE_MUTATION,
        input=variables,
    )

    assert_doesnt_exist("WinterStorageLease", executed)


UPDATE_WINTER_STORAGE_LEASE_MUTATION = """
mutation UpdateWinterStorageLease($input: UpdateWinterStorageLeaseMutationInput!) {
    updateWinterStorageLease(input:$input){
        winterStorageLease {
            id
            startDate
            endDate
            comment
            boat {
                id
            }
            application {
                id
                customer {
                    id
                }
            }
        }
    }
}
"""


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_update_winter_storage_lease_all_fields(
    api_client, winter_storage_lease, customer_profile
):
    winter_storage_application = WinterStorageApplicationFactory(
        customer=winter_storage_lease.customer, boat=winter_storage_lease.boat
    )
    lease_id = to_global_id(WinterStorageLeaseNode, winter_storage_lease.id)
    application_id = to_global_id(
        WinterStorageApplicationNode, winter_storage_application.id
    )
    boat_id = to_global_id(BoatNode, winter_storage_application.boat.id)

    start_date = today()
    end_date = start_date + relativedelta(months=3)

    variables = {
        "id": lease_id,
        "startDate": start_date,
        "endDate": end_date,
        "comment": "",
        "boatId": boat_id,
        "applicationId": application_id,
    }

    executed = api_client.execute(UPDATE_WINTER_STORAGE_LEASE_MUTATION, input=variables)
    assert executed["data"]["updateWinterStorageLease"]["winterStorageLease"] == {
        "id": lease_id,
        "startDate": str(variables["startDate"].date()),
        "endDate": str(variables["endDate"].date()),
        "comment": variables["comment"],
        "boat": {"id": boat_id},
        "application": {
            "id": application_id,
            "customer": {
                "id": to_global_id(ProfileNode, winter_storage_application.customer.id),
            },
        },
    }


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_update_winter_storage_lease_remove_application(
    api_client, winter_storage_lease, winter_storage_application
):
    lease_id = to_global_id(WinterStorageLeaseNode, winter_storage_lease.id)
    boat_id = to_global_id(BoatNode, winter_storage_lease.boat.id)
    winter_storage_lease.application = winter_storage_application
    winter_storage_lease.save()

    variables = {
        "id": lease_id,
        "applicationId": None,
    }

    executed = api_client.execute(UPDATE_WINTER_STORAGE_LEASE_MUTATION, input=variables)
    assert executed["data"]["updateWinterStorageLease"]["winterStorageLease"] == {
        "id": lease_id,
        "startDate": str(winter_storage_lease.start_date),
        "endDate": str(winter_storage_lease.end_date),
        "comment": winter_storage_lease.comment,
        "boat": {"id": boat_id},
        "application": None,
    }


def test_update_winter_storage_lease_application_doesnt_exist(
    superuser_api_client, winter_storage_lease
):
    variables = {
        "id": to_global_id(WinterStorageLeaseNode, winter_storage_lease.id),
        "applicationId": to_global_id(WinterStorageApplicationNode, randint(0, 999)),
    }

    executed = superuser_api_client.execute(
        UPDATE_WINTER_STORAGE_LEASE_MUTATION,
        input=variables,
    )

    assert_doesnt_exist("WinterStorageApplication", executed)


def test_update_winter_storage_lease_application_without_customer(
    superuser_api_client, winter_storage_lease
):
    winter_storage_application = WinterStorageApplicationFactory()

    variables = {
        "id": to_global_id(WinterStorageLeaseNode, winter_storage_lease.id),
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
    }

    executed = superuser_api_client.execute(
        UPDATE_WINTER_STORAGE_LEASE_MUTATION,
        input=variables,
    )

    assert_in_errors(
        "Application must be connected to an existing customer first", executed
    )


def test_update_winter_storage_lease_application_already_has_lease(
    superuser_api_client,
    winter_storage_lease,
    customer_profile,
):
    winter_storage_application = WinterStorageApplicationFactory(
        customer=winter_storage_lease.customer, boat=winter_storage_lease.boat
    )
    WinterStorageLeaseFactory(application=winter_storage_application)

    variables = {
        "id": to_global_id(WinterStorageLeaseNode, winter_storage_lease.id),
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
    }

    executed = superuser_api_client.execute(
        UPDATE_WINTER_STORAGE_LEASE_MUTATION,
        input=variables,
    )

    assert_in_errors(
        "Winter storage lease with this Application already exists", executed
    )


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
@freeze_time("2020-01-01T08:00:00Z")
def test_create_berth_lease_for_non_billable_customer(
    api_client, berth, non_billable_customer
):
    berth_application = BerthApplicationFactory(customer=non_billable_customer)
    BoatTypeFactory(id=berth_application.boat.boat_type.id)

    create_berth_products(berth)

    variables = {
        "applicationId": to_global_id(BerthApplicationNode, berth_application.id),
        "berthId": to_global_id(BerthNode, berth.id),
    }

    executed = api_client.execute(
        CREATE_BERTH_LEASE_WITH_ORDER_MUTATION, input=variables
    )

    expected_product = BerthProduct.objects.get_in_range(
        berth.berth_type.width,
        get_berth_lease_pricing_category(BerthLease.objects.first()),
    )

    assert executed["data"]["createBerthLease"]["berthLease"].pop("id") is not None
    assert (
        executed["data"]["createBerthLease"]["berthLease"]["order"].pop("id")
        is not None
    )
    assert executed["data"]["createBerthLease"]["berthLease"] == {
        "berth": {"id": variables["berthId"]},
        "order": {
            "price": "0.00",
            "status": "PAID_MANUALLY",
            "customer": {"id": to_global_id(ProfileNode, non_billable_customer.id)},
            "product": {"id": to_global_id(BerthProductNode, expected_product.id)},
        },
    }


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
@freeze_time("2020-06-11T08:00:00Z")
def test_create_winter_storage_lease_for_non_billable_customer(
    api_client,
    winter_storage_application,
    winter_storage_place,
    customer_profile,
    non_billable_customer,
):
    BoatTypeFactory(id=winter_storage_application.boat_type.id)
    winter_storage_application = WinterStorageApplicationFactory(
        customer=non_billable_customer
    )
    product = WinterStorageProductFactory(
        winter_storage_area=winter_storage_place.winter_storage_section.area
    )

    variables = {
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
        "placeId": to_global_id(WinterStoragePlaceNode, winter_storage_place.id),
    }

    executed = api_client.execute(
        CREATE_WINTER_STORAGE_LEASE_WITH_ORDER_MUTATION, input=variables
    )

    assert (
        executed["data"]["createWinterStorageLease"]["winterStorageLease"].pop("id")
        is not None
    )
    assert (
        executed["data"]["createWinterStorageLease"]["winterStorageLease"]["order"].pop(
            "id"
        )
        is not None
    )
    assert executed["data"]["createWinterStorageLease"]["winterStorageLease"] == {
        "place": {"id": variables["placeId"]},
        "order": {
            "price": "0.00",
            "status": "PAID_MANUALLY",
            "customer": {"id": to_global_id(ProfileNode, non_billable_customer.id)},
            "product": {
                "priceUnit": product.price_unit.name,
                "priceValue": str(product.price_value),
            },
        },
    }


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
@freeze_time("2020-01-01T08:00:00Z")
def test_create_berth_lease_creates_contract(api_client, berth, customer_profile):
    create_berth_products(berth)
    berth_application = BerthApplicationFactory(
        customer=customer_profile,
    )

    variables = {
        "applicationId": to_global_id(BerthApplicationNode, berth_application.id),
        "berthId": to_global_id(BerthNode, berth.id),
        "boatId": to_global_id(BoatNode, berth_application.boat.id),
        "startDate": "2020-03-01",
        "endDate": "2020-12-31",
        "comment": "Very wow, such comment",
    }

    assert BerthLease.objects.count() == 0

    api_client.execute(CREATE_BERTH_LEASE_MUTATION, input=variables)

    assert BerthLease.objects.count() == 1

    lease = BerthLease.objects.all()[:1].get()
    contract = lease.contract

    assert isinstance(contract, BerthContract)


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
@freeze_time("2020-01-01T08:00:00Z")
def test_create_berth_lease_no_contract_for_non_billable_customer(
    api_client, berth, boat, non_billable_customer
):
    boat = BoatFactory(owner=non_billable_customer)
    berth_application = BerthApplicationFactory(
        customer=non_billable_customer, boat=boat
    )
    create_berth_products(berth)

    variables = {
        "applicationId": to_global_id(BerthApplicationNode, berth_application.id),
        "berthId": to_global_id(BerthNode, berth.id),
        "boatId": to_global_id(BoatNode, boat.id),
        "startDate": "2020-03-01",
        "endDate": "2020-12-31",
        "comment": "Very wow, such comment",
    }

    assert BerthLease.objects.count() == 0

    api_client.execute(CREATE_BERTH_LEASE_MUTATION, input=variables)

    assert BerthLease.objects.count() == 1

    lease = BerthLease.objects.first()

    assert not hasattr(lease, "contract")


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_create_winter_storage_lease_creates_contract(
    api_client, winter_storage_place, customer_profile
):
    winter_storage_application = WinterStorageApplicationFactory(
        customer=customer_profile
    )
    BoatTypeFactory(id=winter_storage_application.boat_type.id)
    WinterStorageProductFactory(
        winter_storage_area=winter_storage_place.winter_storage_section.area
    )

    variables = {
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
        "placeId": to_global_id(WinterStoragePlaceNode, winter_storage_place.id),
    }

    assert WinterStorageLease.objects.count() == 0

    api_client.execute(CREATE_WINTER_STORAGE_LEASE_MUTATION, input=variables)

    assert WinterStorageLease.objects.count() == 1

    lease = WinterStorageLease.objects.all()[:1].get()
    contract = lease.contract

    assert isinstance(contract, WinterStorageContract)


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_create_winter_storage_lease_no_contract_for_non_billable_customer(
    api_client, winter_storage_place, non_billable_customer
):
    winter_storage_application = WinterStorageApplicationFactory(
        customer=non_billable_customer
    )
    BoatTypeFactory(id=winter_storage_application.boat_type.id)
    WinterStorageProductFactory(
        winter_storage_area=winter_storage_place.winter_storage_section.area
    )

    variables = {
        "applicationId": to_global_id(
            WinterStorageApplicationNode, winter_storage_application.id
        ),
        "placeId": to_global_id(WinterStoragePlaceNode, winter_storage_place.id),
    }

    assert WinterStorageLease.objects.count() == 0

    api_client.execute(CREATE_WINTER_STORAGE_LEASE_MUTATION, input=variables)

    assert WinterStorageLease.objects.count() == 1

    lease = WinterStorageLease.objects.first()

    assert not hasattr(lease, "contract")


TERMINATE_BERTH_LEASE_MUTATION = """
mutation TERMINATE_BERTH_LEASE($input: TerminateBerthLeaseMutationInput!) {
    terminateBerthLease(input: $input) {
        berthLease {
            status
            endDate
        }
    }
}
"""


@pytest.mark.parametrize(
    "time,expected_end_date",
    [("2020-07-01T08:00:00Z", "2020-07-01"), ("2020-10-01T08:00:00Z", "2020-09-14")],
)
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
@pytest.mark.parametrize(
    "lease_status",
    [LeaseStatus.PAID, LeaseStatus.ERROR, LeaseStatus.OFFERED],
)
def test_terminate_berth_lease_with_application(
    api_client,
    lease_status,
    notification_template_berth_lease_terminated,
    time,
    expected_end_date,
):
    with freeze_time(time):
        berth_lease = BerthLeaseFactory(
            start_date="2020-06-10",
            end_date="2020-09-14",
            status=lease_status,
            application=BerthApplicationFactory(email="foo@email.com", language="fi"),
        )

        variables = {
            "id": to_global_id(BerthLeaseNode, berth_lease.id),
        }

        executed = api_client.execute(TERMINATE_BERTH_LEASE_MUTATION, input=variables)

        assert executed["data"]["terminateBerthLease"]["berthLease"] == {
            "status": LeaseStatus.TERMINATED.name,
            "endDate": str(expected_end_date),
        }
        assert len(mail.outbox) == 1
        assert mail.outbox[0].subject == "test berth lease rejected subject"
        assert (
            mail.outbox[0].body
            == f"test berth lease terminated {format_date(today().date(), locale='fi')} {berth_lease.id}"
        )
        assert mail.outbox[0].to == ["foo@email.com"]

        assert mail.outbox[0].alternatives == [
            (
                f"<b>test berth lease terminated</b> "
                f"{format_date(today().date(), locale='fi')} {berth_lease.id}",
                "text/html",
            )
        ]


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_terminate_berth_lease_with_offered_order(
    api_client, offered_berth_order, notification_template_berth_lease_terminated
):
    variables = {
        "id": to_global_id(BerthLeaseNode, offered_berth_order.lease.id),
    }

    executed = api_client.execute(TERMINATE_BERTH_LEASE_MUTATION, input=variables)
    assert (
        executed["data"]["terminateBerthLease"]["berthLease"]["status"]
        == LeaseStatus.TERMINATED.name
    )
    offered_berth_order.refresh_from_db()
    offered_berth_order.lease.refresh_from_db()
    assert offered_berth_order.lease.status == LeaseStatus.TERMINATED
    assert offered_berth_order.status == OrderStatus.CANCELLED
    assert len(mail.outbox) == 1


@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_terminate_berth_lease_with_drafted_order(
    api_client, drafted_berth_order, notification_template_berth_lease_terminated
):
    variables = {
        "id": to_global_id(BerthLeaseNode, drafted_berth_order.lease.id),
    }

    executed = api_client.execute(TERMINATE_BERTH_LEASE_MUTATION, input=variables)

    drafted_berth_order.refresh_from_db()
    drafted_berth_order.lease.refresh_from_db()
    assert drafted_berth_order.lease.status == LeaseStatus.DRAFTED
    assert drafted_berth_order.status == OrderStatus.DRAFTED
    assert_in_errors(
        "Only leases in paid, error or offered status can be terminated, current status is drafted",
        executed,
    )


@freeze_time("2020-07-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_terminate_berth_lease_without_application(
    api_client, notification_template_berth_lease_terminated
):
    berth_lease = BerthLeaseFactory(
        start_date=today() - relativedelta(weeks=1),
        end_date=today() + relativedelta(weeks=1),
        status=LeaseStatus.PAID,
        application=None,
    )

    end_date = today().date()
    variables = {
        "id": to_global_id(BerthLeaseNode, berth_lease.id),
        "profileToken": "profile_token",
    }
    data = {
        "id": to_global_id(ProfileNode, berth_lease.customer.id),
        "primary_email": {"email": "foo@email.com"},
        "primary_phone": {},
    }

    with mock.patch(
        "customers.services.profile.requests.post",
        side_effect=mocked_response_profile(count=0, data=data, use_edges=False),
    ):
        executed = api_client.execute(TERMINATE_BERTH_LEASE_MUTATION, input=variables)

    assert executed["data"]["terminateBerthLease"]["berthLease"] == {
        "status": LeaseStatus.TERMINATED.name,
        "endDate": str(end_date),
    }
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "test berth lease rejected subject"
    assert (
        mail.outbox[0].body
        == f"test berth lease terminated {format_date(end_date, locale='fi')} {berth_lease.id}"
    )
    assert mail.outbox[0].to == ["foo@email.com"]

    assert mail.outbox[0].alternatives == [
        (
            f"<b>test berth lease terminated</b> "
            f"{format_date(end_date, locale='fi')} {berth_lease.id}",
            "text/html",
        )
    ]


@freeze_time("2020-07-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_terminate_berth_lease_with_end_date(api_client):
    berth_lease = BerthLeaseFactory(
        start_date=today() - relativedelta(weeks=1),
        end_date=today() + relativedelta(weeks=1),
        status=LeaseStatus.PAID,
        application=BerthApplicationFactory(email="foo@email.com", language="fi"),
    )

    variables = {
        "id": to_global_id(BerthLeaseNode, berth_lease.id),
        "endDate": today() + relativedelta(days=1),
    }

    executed = api_client.execute(TERMINATE_BERTH_LEASE_MUTATION, input=variables)

    assert executed["data"]["terminateBerthLease"]["berthLease"] == {
        "status": LeaseStatus.TERMINATED.name,
        "endDate": str(variables["endDate"].date()),
    }


@pytest.mark.parametrize(
    "api_client",
    ["api_client", "user", "harbor_services", "berth_supervisor"],
    indirect=True,
)
def test_terminate_berth_lease_not_enough_permissions(api_client, berth_lease):
    variables = {
        "id": to_global_id(BerthLeaseNode, berth_lease.id),
    }

    executed = api_client.execute(TERMINATE_BERTH_LEASE_MUTATION, input=variables)

    assert_not_enough_permissions(executed)


def test_terminate_berth_lease_doesnt_exist(superuser_api_client):
    variables = {
        "id": to_global_id(BerthLeaseNode, uuid.uuid4()),
    }

    executed = superuser_api_client.execute(
        TERMINATE_BERTH_LEASE_MUTATION,
        input=variables,
    )

    assert_doesnt_exist("BerthLease", executed)


@freeze_time("2020-07-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_terminate_berth_lease_no_email_no_token(api_client):
    berth_lease = BerthLeaseFactory(
        start_date=today() - relativedelta(weeks=1),
        end_date=today() + relativedelta(weeks=1),
        status=LeaseStatus.PAID,
        application=None,
    )

    variables = {
        "id": to_global_id(BerthLeaseNode, berth_lease.id),
        "endDate": today() + relativedelta(days=1),
    }

    executed = api_client.execute(TERMINATE_BERTH_LEASE_MUTATION, input=variables)
    assert_in_errors(
        "The lease has no email and no profile token was provided", executed
    )


TERMINATE_BERTH_LEASE_MUTATION_W_START_DATE = """
mutation TERMINATE_BERTH_LEASE($input: TerminateBerthLeaseMutationInput!) {
    terminateBerthLease(input: $input) {
        berthLease {
            status
            endDate
            startDate
        }
    }
}
"""


@freeze_time("2020-12-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_terminate_berth_lease_before_season(api_client):
    start_date = calculate_berth_lease_start_date()
    end_date = calculate_berth_lease_end_date()

    berth_lease = BerthLeaseFactory(
        start_date=start_date,
        end_date=end_date,
        status=LeaseStatus.PAID,
        application=BerthApplicationFactory(email="foo@email.com", language="fi"),
    )

    # Have to query to get the annotated value `is_available`.
    assert not Berth.objects.get(id=berth_lease.berth_id).is_available

    variables = {
        "id": to_global_id(BerthLeaseNode, berth_lease.id),
    }

    executed = api_client.execute(
        TERMINATE_BERTH_LEASE_MUTATION_W_START_DATE, input=variables
    )

    assert executed["data"]["terminateBerthLease"]["berthLease"] == {
        "status": LeaseStatus.TERMINATED.name,
        "startDate": str(start_date),
        "endDate": str(start_date),
    }
    assert Berth.objects.get(id=berth_lease.berth_id).is_available


TERMINATE_WINTER_STORAGE_LEASE_MUTATION = """
mutation TERMINATE_WINTER_STORAGE_LEASE_MUTATION($input: TerminateWinterStorageLeaseMutationInput!) {
    terminateWinterStorageLease(input: $input) {
        winterStorageLease {
            status
            endDate
        }
    }
}
"""


@freeze_time("2020-12-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_terminate_ws_lease_with_application(
    api_client, notification_template_ws_lease_terminated
):
    ws_lease = WinterStorageLeaseFactory(
        start_date=today() - relativedelta(weeks=1),
        end_date=today() + relativedelta(weeks=1),
        status=LeaseStatus.PAID,
        application=WinterStorageApplicationFactory(
            email="foo@email.com", language="fi"
        ),
    )

    end_date = today().date()
    variables = {
        "id": to_global_id(WinterStorageLeaseNode, ws_lease.id),
    }

    executed = api_client.execute(
        TERMINATE_WINTER_STORAGE_LEASE_MUTATION, input=variables
    )

    assert executed["data"]["terminateWinterStorageLease"]["winterStorageLease"] == {
        "status": LeaseStatus.TERMINATED.name,
        "endDate": str(end_date),
    }
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "test ws lease rejected subject"
    assert (
        mail.outbox[0].body
        == f"test ws lease terminated {format_date(end_date, locale='fi')} {ws_lease.id}"
    )
    assert mail.outbox[0].to == ["foo@email.com"]

    assert mail.outbox[0].alternatives == [
        (
            f"<b>test ws lease terminated</b> "
            f"{format_date(end_date, locale='fi')} {ws_lease.id}",
            "text/html",
        )
    ]


@freeze_time("2020-12-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_terminate_ws_lease_without_application(
    api_client, notification_template_ws_lease_terminated
):
    ws_lease = WinterStorageLeaseFactory(
        start_date=today() - relativedelta(weeks=1),
        end_date=today() + relativedelta(weeks=1),
        status=LeaseStatus.PAID,
        application=None,
    )

    end_date = today().date()
    variables = {
        "id": to_global_id(WinterStorageLeaseNode, ws_lease.id),
        "profileToken": "profile_token",
    }
    data = {
        "id": to_global_id(ProfileNode, ws_lease.customer.id),
        "primary_email": {"email": "foo@email.com"},
        "primary_phone": {},
    }

    with mock.patch(
        "customers.services.profile.requests.post",
        side_effect=mocked_response_profile(count=0, data=data, use_edges=False),
    ):
        executed = api_client.execute(
            TERMINATE_WINTER_STORAGE_LEASE_MUTATION, input=variables
        )

    assert executed["data"]["terminateWinterStorageLease"]["winterStorageLease"] == {
        "status": LeaseStatus.TERMINATED.name,
        "endDate": str(end_date),
    }
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "test ws lease rejected subject"
    assert (
        mail.outbox[0].body
        == f"test ws lease terminated {format_date(end_date, locale='fi')} {ws_lease.id}"
    )
    assert mail.outbox[0].to == ["foo@email.com"]

    assert mail.outbox[0].alternatives == [
        (
            f"<b>test ws lease terminated</b> "
            f"{format_date(end_date, locale='fi')} {ws_lease.id}",
            "text/html",
        )
    ]


@freeze_time("2020-12-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_terminate_ws_lease_with_end_date(api_client):
    ws_lease = WinterStorageLeaseFactory(
        start_date=today() - relativedelta(weeks=1),
        end_date=today() + relativedelta(weeks=1),
        status=LeaseStatus.PAID,
        application=WinterStorageApplicationFactory(
            email="foo@email.com", language="fi"
        ),
    )

    variables = {
        "id": to_global_id(WinterStorageLeaseNode, ws_lease.id),
        "endDate": today() + relativedelta(days=1),
    }

    executed = api_client.execute(
        TERMINATE_WINTER_STORAGE_LEASE_MUTATION, input=variables
    )

    assert executed["data"]["terminateWinterStorageLease"]["winterStorageLease"] == {
        "status": LeaseStatus.TERMINATED.name,
        "endDate": str(variables["endDate"].date()),
    }


@pytest.mark.parametrize(
    "api_client",
    ["api_client", "user", "harbor_services", "berth_supervisor"],
    indirect=True,
)
def test_terminate_ws_lease_not_enough_permissions(api_client, winter_storage_lease):
    variables = {
        "id": to_global_id(WinterStorageLeaseNode, winter_storage_lease.id),
    }

    executed = api_client.execute(
        TERMINATE_WINTER_STORAGE_LEASE_MUTATION, input=variables
    )

    assert_not_enough_permissions(executed)


def test_terminate_ws_lease_doesnt_exist(superuser_api_client):
    variables = {
        "id": to_global_id(WinterStorageLeaseNode, uuid.uuid4()),
    }

    executed = superuser_api_client.execute(
        TERMINATE_WINTER_STORAGE_LEASE_MUTATION,
        input=variables,
    )

    assert_doesnt_exist("WinterStorageLease", executed)


@freeze_time("2020-12-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_terminate_ws_lease_no_email_no_token(api_client):
    ws_lease = WinterStorageLeaseFactory(
        start_date=today() - relativedelta(weeks=1),
        end_date=today() + relativedelta(weeks=1),
        status=LeaseStatus.PAID,
        application=None,
    )

    variables = {
        "id": to_global_id(WinterStorageLeaseNode, ws_lease.id),
        "endDate": today() + relativedelta(days=1),
    }

    executed = api_client.execute(
        TERMINATE_WINTER_STORAGE_LEASE_MUTATION, input=variables
    )
    assert_in_errors(
        "The lease has no email and no profile token was provided", executed
    )


TERMINATE_WINTER_STORAGE_LEASE_MUTATION_W_START_DATE = """
mutation TERMINATE_WINTER_STORAGE_LEASE_MUTATION($input: TerminateWinterStorageLeaseMutationInput!) {
    terminateWinterStorageLease(input: $input) {
        winterStorageLease {
            status
            startDate
            endDate
        }
    }
}
"""


@freeze_time("2020-07-01T08:00:00Z")
@pytest.mark.parametrize(
    "api_client",
    ["berth_services", "berth_handler"],
    indirect=True,
)
def test_terminate_ws_lease_before_season(api_client):
    start_date = calculate_winter_storage_lease_start_date()
    end_date = calculate_winter_storage_lease_end_date()

    ws_lease = WinterStorageLeaseFactory(
        start_date=start_date,
        end_date=end_date,
        status=LeaseStatus.PAID,
        application=WinterStorageApplicationFactory(
            email="foo@email.com", language="fi"
        ),
    )

    # Have to query to get the annotated value `is_available`.
    assert not WinterStoragePlace.objects.get(id=ws_lease.place_id).is_available

    variables = {
        "id": to_global_id(WinterStorageLeaseNode, ws_lease.id),
    }

    executed = api_client.execute(
        TERMINATE_WINTER_STORAGE_LEASE_MUTATION_W_START_DATE, input=variables
    )

    assert executed["data"]["terminateWinterStorageLease"]["winterStorageLease"] == {
        "status": LeaseStatus.TERMINATED.name,
        "startDate": str(start_date),
        "endDate": str(start_date),
    }
    assert WinterStoragePlace.objects.get(id=ws_lease.place_id).is_available
