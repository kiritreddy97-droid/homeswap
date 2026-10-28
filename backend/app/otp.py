"""OTP delivery behind an interface so an SMS/email provider can be swapped in without touching the auth flow."""

import hashlib
import hmac
import logging
import re
import secrets
from typing import Protocol

log = logging.getLogger("homeswap.otp")

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class OtpSender(Protocol):
    def send(self, identifier: str, code: str) -> None: ...


class ConsoleOtpSender:
    """Dev only: writes the code to the application log. Refused in production by Settings.validate()."""

    def send(self, identifier: str, code: str) -> None:
        log.warning("DEV OTP for %s is %s", identifier, code)


def get_sender(name: str) -> OtpSender:
    if name == "console":
        return ConsoleOtpSender()
    raise RuntimeError(f"unknown OTP_SENDER {name!r}; implement OtpSender and register it in app/otp.py")


def normalise_identifier(raw: str) -> str:
    """Email -> lowercase; phone -> E.164-ish (+ and digits). Raises ValueError for anything else."""
    raw = raw.strip()
    if _EMAIL.match(raw):
        return raw.lower()
    digits = re.sub(r"[\s\-()]", "", raw)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if re.fullmatch(r"\+[1-9]\d{7,14}", digits):
        return digits
    if re.fullmatch(r"[6-9]\d{9}", digits):  # bare Indian mobile number
        return "+91" + digits
    raise ValueError("enter a valid email or phone number")


def generate_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def hash_code(secret: str, identifier: str, code: str) -> str:
    return hmac.new(secret.encode(), f"{identifier}:{code}".encode(), hashlib.sha256).hexdigest()
