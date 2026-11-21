"""Xaman (formerly XUMM) REST client. Server side only: the API secret never reaches the browser.

Docs: https://docs.xaman.dev. One payload = one sign request shown to the user as a QR code or
deep link. We poll the payload by uuid; there are no webhooks, so no public callback URL is needed.
"""
import logging
from urllib.parse import quote

import requests
import segno
from django.conf import settings

from .client import network_key
from .errors import LedgerError

log = logging.getLogger(__name__)

API = "https://xumm.app/api/v1/platform"
TIMEOUT = 15  # seconds
EXPIRE_MINUTES = 5
FORCE_NETWORK = {"testnet": "TESTNET", "devnet": "DEVNET"}


def configured():
    return bool(settings.XAMAN_API_KEY and settings.XAMAN_API_SECRET)


def _headers():
    if not configured():
        raise LedgerError("xaman_not_configured", "Xaman isn't configured on this server (XAMAN_API_KEY / XAMAN_API_SECRET).")
    return {
        "X-API-Key": settings.XAMAN_API_KEY, "X-API-Secret": settings.XAMAN_API_SECRET,
        "Content-Type": "application/json", "Accept": "application/json",
    }


def _call(method, path, **kwargs):
    headers = _headers()
    try:
        answer = getattr(requests, method)(f"{API}{path}", headers=headers, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        log.warning("xaman %s %s failed: %s", method.upper(), path, exc)
        raise LedgerError("xaman_error", "Xaman can't be reached right now. Try again in a moment.") from exc
    if answer.status_code >= 400:
        log.warning("xaman %s %s answered %s: %s", method.upper(), path, answer.status_code, str(getattr(answer, "text", ""))[:300])
        raise LedgerError("xaman_error", "Xaman refused the request. Check the API key and secret, then try again.")
    try:
        data = answer.json()
    except ValueError as exc:
        raise LedgerError("xaman_error", "Xaman sent an answer we couldn't read.") from exc
    if not isinstance(data, dict):
        raise LedgerError("xaman_error", "Xaman sent an answer we couldn't read.")
    return data


def create_payload(txjson, *, submit, multisign=False, signers=None, instruction="", user_token=""):
    """Create a sign request. -> {'uuid', 'deeplink', 'pushed'}"""
    options = {"submit": bool(submit), "expire": EXPIRE_MINUTES, "force_network": FORCE_NETWORK.get(network_key(), "TESTNET")}
    if multisign:
        options["multisign"] = True
    if signers:
        options["signers"] = list(signers)
    body = {"txjson": txjson, "options": options}
    if instruction:
        body["custom_meta"] = {"instruction": instruction[:280]}
    if user_token:
        body["user_token"] = user_token
    data = _call("post", "/payload", json=body)
    if "uuid" not in data:
        raise LedgerError("xaman_error", "Xaman sent an answer we couldn't read.")
    return {"uuid": data["uuid"], "deeplink": (data.get("next") or {}).get("always", ""), "pushed": bool(data.get("pushed"))}


def get_payload(uuid):
    """-> {'status': open|signed|rejected|expired, 'account', 'hex', 'txid', 'user_token'}"""
    data = _call("get", f"/payload/{quote(str(uuid))}")
    meta = data.get("meta") or {}
    reply = data.get("response") or {}
    if meta.get("signed"):
        status = "signed"
    elif meta.get("expired"):
        status = "expired"
    elif meta.get("cancelled") or meta.get("resolved"):
        status = "rejected"
    else:
        status = "open"
    return {
        "status": status, "account": reply.get("account") or "", "hex": reply.get("hex") or "",
        "txid": reply.get("txid") or "", "user_token": (data.get("application") or {}).get("issued_user_token") or "",
    }


def cancel_payload(uuid):
    try:
        _call("delete", f"/payload/{quote(str(uuid))}")
    except LedgerError:
        pass  # already resolved or expired: nothing to cancel


def qr_data_uri(text):
    """The QR code as an SVG data URI (rendered here, so the browser never contacts Xaman)."""
    return segno.make(text, error="m").svg_data_uri(scale=6, border=2, dark="#141414", light="#ffffff")
