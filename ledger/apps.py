from django.apps import AppConfig


class LedgerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ledger"

    def ready(self):
        # Refuse to start if anything points at mainnet: this app holds platform wallet keys.
        from .client import assert_not_mainnet

        assert_not_mainnet()
