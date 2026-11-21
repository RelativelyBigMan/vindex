"""XRPL network configuration and client factory.

Only Testnet and Devnet are supported. This app keeps platform wallet keys on the server,
so it must never talk to mainnet: assert_not_mainnet() runs at startup.
"""
import asyncio
import logging
from urllib.parse import urlparse

import httpx

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from xrpl.clients import JsonRpcClient

NETWORKS = {
    "testnet": {
        "name": "Testnet",
        "json_rpc": "https://s.altnet.rippletest.net:51234/",
        "ws": "wss://s.altnet.rippletest.net:51233",
        "explorer": "https://testnet.xrpl.org",
        "faucet": "https://faucet.altnet.rippletest.net/accounts",
    },
    "devnet": {
        "name": "Devnet",
        "json_rpc": "https://s.devnet.rippletest.net:51234/",
        "ws": "wss://s.devnet.rippletest.net:51233",
        "explorer": "https://devnet.xrpl.org",
        "faucet": "https://faucet.devnet.rippletest.net/accounts",
    },
}

# Hosts we are willing to talk to. An allow-list is safer than trying to list every mainnet server.
_ALLOWED_HOST_SUFFIXES = (
    "altnet.rippletest.net",
    "devnet.rippletest.net",
    "testnet.xrpl.org",
    "devnet.xrpl.org",
)


def looks_like_mainnet(url):
    """True unless the URL's host is a known Testnet/Devnet host."""
    host = (urlparse(url).hostname or "").lower()
    return not any(host == s or host.endswith("." + s) for s in _ALLOWED_HOST_SUFFIXES)


def network_key():
    return getattr(settings, "XRPL_NETWORK", "testnet")


def network():
    """The config dict of the selected network."""
    key = network_key()
    if key not in NETWORKS:
        raise ImproperlyConfigured(f"XRPL_NETWORK must be one of {', '.join(NETWORKS)}; got {key!r}.")
    return NETWORKS[key]


def assert_not_mainnet():
    """Raise at startup if the selected network is unknown or any URL is not a test network."""
    net = network()
    for field in ("json_rpc", "ws", "explorer", "faucet"):
        if looks_like_mainnet(net[field]):
            raise ImproperlyConfigured(
                f"Refusing to start: {field} URL {net[field]!r} is not an XRPL Testnet/Devnet host. "
                "Vindex holds platform wallet keys and must never run against mainnet."
            )


log = logging.getLogger(__name__)
NETWORK_ATTEMPTS = 6  # with the pauses below: rides out about 15 s without a connection


class RetryingJsonRpcClient(JsonRpcClient):
    """JsonRpcClient that retries when the connection itself fails (DNS hiccup, reset, timeout).

    Safe for every request we send: reads have no effect, and a signed transaction that is
    submitted twice still applies once (same hash; the second submit reports it as already seen).
    """

    async def _request_impl(self, request, **kwargs):
        for attempt in range(1, NETWORK_ATTEMPTS + 1):
            try:
                return await super()._request_impl(request, **kwargs)
            except httpx.TransportError as exc:
                if attempt == NETWORK_ATTEMPTS:
                    raise
                log.warning("ledger request failed (%s), attempt %s of %s", type(exc).__name__, attempt, NETWORK_ATTEMPTS)
                await asyncio.sleep(min(attempt, 4))


def get_client():
    """A synchronous JSON-RPC client for the selected network. Cheap to create; safe to use per call."""
    return RetryingJsonRpcClient(network()["json_rpc"])


def explorer_tx(tx_hash):
    return f"{network()['explorer']}/transactions/{tx_hash}"


def explorer_account(address):
    return f"{network()['explorer']}/accounts/{address}"
