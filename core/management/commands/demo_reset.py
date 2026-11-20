"""Start the demo with empty lists: archive every lease. Nothing on the ledger is touched."""
from django.core.management.base import BaseCommand

from core.models import Lease


class Command(BaseCommand):
    help = "Archive all leases so the lists are empty. The ledger is untouched; the bot still returns archived deposits."

    def handle(self, *args, **options):
        count = Lease.objects.filter(archived=False).update(archived=True)
        self.stdout.write(self.style.SUCCESS(f"Archived {count} lease(s). The ledger was not touched."))
