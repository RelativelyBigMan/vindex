"""Template context shared by every page. Public facts only: this is embedded in the HTML."""
from django.conf import settings

from ledger.client import network, network_key

from .models import Currency


def escrow_config(request):
    net = network()
    return {
        "escrow_config": {
            "network": {"id": network_key(), "name": net["name"], "is_testnet": True, "explorer": net["explorer"], "ws": net["ws"]},
            "demo_mode": settings.DEMO_MODE,
            "currencies": [
                {"code": c.code, "label": c.label, "issuer": c.issuer_address} for c in Currency.objects.order_by("code")
            ],
        }
    }
