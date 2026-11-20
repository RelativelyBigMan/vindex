"""PREIMAGE-SHA-256 crypto-conditions (RFC draft-thomas-crypto-conditions), hand-encoded in DER.

No extra library is needed for this one condition type:

    fulfillment = A0 <len> 80 <len(preimage)> <preimage>
    condition   = A0 <len> 80 20 <sha256(preimage)> 81 <len(cost)> <cost>     cost = len(preimage)

The fulfillment is the SECRET that releases an escrow. Never log it.
"""
import hashlib
import secrets

MAX_PREIMAGE_LENGTH = 127


def _der_length(n):
    """DER definite length: short form below 128, long form above."""
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _tlv(tag, value):
    return bytes([tag]) + _der_length(len(value)) + value


def _unsigned_int(n):
    """Minimal big-endian bytes for a non-negative integer (zero is one 00 byte)."""
    return n.to_bytes(max(1, (n.bit_length() + 7) // 8), "big")


def encode_preimage(preimage):
    """Return (condition_hex, fulfillment_hex), upper-case, for the given preimage bytes."""
    if not isinstance(preimage, (bytes, bytearray)):
        raise TypeError("preimage must be bytes")
    if len(preimage) > MAX_PREIMAGE_LENGTH:
        raise ValueError(f"preimage must be at most {MAX_PREIMAGE_LENGTH} bytes")
    preimage = bytes(preimage)
    fulfillment = _tlv(0xA0, _tlv(0x80, preimage))
    fingerprint = hashlib.sha256(preimage).digest()
    condition = _tlv(0xA0, _tlv(0x80, fingerprint) + _tlv(0x81, _unsigned_int(len(preimage))))
    return condition.hex().upper(), fulfillment.hex().upper()


def make_condition():
    """A fresh condition from a random 32-byte preimage -> (condition_hex, fulfillment_hex)."""
    return encode_preimage(secrets.token_bytes(32))
