"""The return bot: every 10 s, give back every deposit whose automatic return date has passed on the ledger."""
import logging
import time

from django.core.management.base import BaseCommand

from core import lifecycle
from ledger.client import explorer_tx, network

log = logging.getLogger("core.bot")


class Command(BaseCommand):
    help = "Loop bot_pass() every 10 seconds (Ctrl+C to stop). The same pass runs from POST /api/demo/run-bot/."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=10, help="Seconds between passes.")
        parser.add_argument("--once", action="store_true", help="Run a single pass and exit.")

    def handle(self, *args, **options):
        log.info("bot started on %s, one pass every %s s", network()["name"], options["interval"])
        try:
            while True:
                self.one_pass()
                if options["once"]:
                    return
                time.sleep(max(1, options["interval"]))
        except KeyboardInterrupt:
            log.info("bot stopped")

    def one_pass(self):
        try:
            results = lifecycle.bot_pass()
        except Exception as exc:  # the ledger being unreachable must not kill the loop
            log.warning("pass failed: %s", getattr(exc, "message", exc))
            return
        if not results:
            log.info("pass done: nothing to return")
        for item in results:
            if item["action"] == "error":
                log.warning("lease %s: %s", item["lease_id"], item["error"])
            else:
                link = f" {explorer_tx(item['tx_hash'])}" if item.get("tx_hash") else ""
                log.info("lease %s: %s%s", item["lease_id"], item["action"], link)
