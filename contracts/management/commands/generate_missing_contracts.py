from django.core.management import BaseCommand

from contracts.services import get_contract_service
from leases.enums import LeaseStatus
from leases.models import BerthLease, WinterStorageLease


class Command(BaseCommand):
    help = "Generate contracts for leases in status DRAFTED and OFFERED, which are missing contracts"

    def handle(self, *args, **options):
        winter_storage_leases = WinterStorageLease.objects.filter(
            status__in=[LeaseStatus.DRAFTED, LeaseStatus.OFFERED], contract=None
        )
        berth_leases = BerthLease.objects.filter(
            status__in=[LeaseStatus.DRAFTED, LeaseStatus.OFFERED], contract=None
        )

        failed = []
        success_count = 0

        for winter_storage_lease in winter_storage_leases:
            try:
                get_contract_service().create_winter_storage_contract(
                    winter_storage_lease
                )
                success_count += 1
            except Exception as e:
                failed.append(
                    f"berth_lease_id: {winter_storage_lease.id}, exception: {str(e)}"
                )

        for berth_lease in berth_leases:
            try:
                get_contract_service().create_berth_contract(berth_lease)
                success_count += 1
            except Exception as e:
                failed.append(f"berth_lease_id: {berth_lease.id}, exception: {str(e)}")

        if not failed:
            self.stdout.write(
                self.style.SUCCESS(f"Done! Added contracts to {success_count} leases.")
            )
        else:
            self.stdout.write(
                self.style.ERROR(
                    "Failed to generate contracts for the following leases:"
                )
            )
            self.stdout.writelines(failed)
