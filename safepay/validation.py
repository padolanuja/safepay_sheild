"""Input validation shared by all endpoints. Errors become JSON 400 responses."""
from __future__ import annotations

import re

import config

VPA_RE = re.compile(r"^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z][a-zA-Z0-9]{1,63}$")


class ValidationError(ValueError):
    pass


def validate_vpa(vpa) -> str:
    if not isinstance(vpa, str) or not VPA_RE.match(vpa.strip()):
        raise ValidationError("UPI ID must look like name@handle")
    return vpa.strip().lower()


def validate_amount(amount) -> float:
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise ValidationError("amount must be a number")
    if not (config.AMOUNT_MIN_INR <= amount <= config.AMOUNT_MAX_INR):
        raise ValidationError(f"amount must be between Rs {config.AMOUNT_MIN_INR} and Rs {config.AMOUNT_MAX_INR:,}")
    return float(amount)


def validate_bool(name: str, value) -> int:
    if isinstance(value, bool):
        return int(value)
    if value in (0, 1) and not isinstance(value, float):
        return int(value)
    raise ValidationError(f"{name} must be true/false")


def validate_enum(name: str, value, allowed) -> str:
    if value not in allowed:
        raise ValidationError(f"{name} must be one of {sorted(allowed)}")
    return value
