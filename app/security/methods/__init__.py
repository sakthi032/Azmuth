"""Concrete authentication methods and the default method set."""

from __future__ import annotations

from collections.abc import Iterable

from app.security.base import AuthenticationMethod
from app.security.methods.device import DeviceMethod, current_device_id
from app.security.methods.pin import (
    PINMethod,
    CreatorCredentialStore,
    hash_pin,
    verify_pin_hash,
)
from app.security.methods.voice import VoiceMethod


def build_default_methods(
    *,
    pin_hash: str | None = None,
    device_ids: Iterable[str] = (),
    current_id: str | None = None,
) -> tuple[AuthenticationMethod, ...]:
    """Return the standard set of authentication methods.

    Every method is constructed even when unavailable — that way the
    Authenticator can report *which* methods exist and *why* each is or is
    not usable, instead of silently pretending they were never registered.
    """
    return (
        PINMethod(hashed_pin=pin_hash),
        DeviceMethod(registered_ids=device_ids, current_id=current_id),
        VoiceMethod(),
    )


__all__ = [
    "CreatorCredentialStore",
    "DeviceMethod",
    "PINMethod",
    "VoiceMethod",
    "build_default_methods",
    "current_device_id",
    "hash_pin",
    "verify_pin_hash",
]