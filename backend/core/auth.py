"""Nickname + PIN accounts. No email, no real name. PINs are stored only as salted PBKDF2 hashes."""

import hashlib
import hmac
import os
import re

HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
PIN_RE = re.compile(r"^\d{4,6}$")
ITERATIONS = 200_000


def hash_pin(pin: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, stored: str | None) -> bool:
    try:
        _, iterations, salt, expected = (stored or "").split("$")
        digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), expected)
