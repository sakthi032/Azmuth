"""PIN authentication and file-backed credential storage.

The PIN is never stored in plaintext. :func:`hash_pin` produces a
PBKDF2-HMAC-SHA256 hash in a self-describing format::

    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

:func:`verify_pin_hash` uses ``hmac.compare_digest`` so that verification
time does not depend on how many leading bytes match.

PIN authentication has HIGH assurance. It is one of the two methods that
can produce a creator Principal.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path
from typing import Final

from app.config.logging_setup import get_logger
from app.security.base import AuthenticationMethod, AuthenticationResult
from app.security.modes import AssuranceLevel

logger = get_logger("security.pin")

_ALGORITHM: Final[str] = "pbkdf2_sha256"
_DEFAULT_ITERATIONS: Final[int] = 200_000
_SALT_BYTES: Final[int] = 16


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------
def hash_pin(pin: str, *, iterations: int = _DEFAULT_ITERATIONS) -> str:
    """Return a self-describing hash string for ``pin``."""
    if not pin:
        raise ValueError("PIN must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)
    return f"{_ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_pin_hash(pin: str, encoded: str) -> bool:
    """Constant-time comparison of ``pin`` against ``encoded``."""
    try:
        algorithm, iterations_str, salt_hex, digest_hex = encoded.split("$")
    except ValueError:
        return False
    if algorithm != _ALGORITHM:
        return False
    try:
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", pin.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(candidate, expected)


# --------------------------------------------------------------------------
# Method
# --------------------------------------------------------------------------
class PINMethod(AuthenticationMethod):
    """Verify a PIN against a stored hash."""

    name = "pin"

    def __init__(self, *, hashed_pin: str | None = None, user_id: str = "creator") -> None:
        self._hashed_pin = hashed_pin
        self._user_id = user_id

    @property
    def assurance(self) -> AssuranceLevel:
        return AssuranceLevel.HIGH

    def is_available(self) -> bool:
        return bool(self._hashed_pin)

    def verify(self, **credentials: object) -> AuthenticationResult:
        pin = credentials.get("pin")
        if not isinstance(pin, str) or not pin:
            return AuthenticationResult(
                success=False,
                method=self.name,
                assurance=self.assurance,
                reason="missing PIN",
            )
        if not self._hashed_pin:
            return AuthenticationResult(
                success=False,
                method=self.name,
                assurance=self.assurance,
                reason="PIN is not configured",
            )
        if not verify_pin_hash(pin, self._hashed_pin):
            return AuthenticationResult(
                success=False,
                method=self.name,
                assurance=self.assurance,
                reason="incorrect PIN",
            )
        return AuthenticationResult(
            success=True,
            method=self.name,
            assurance=self.assurance,
            user_id=self._user_id,
        )


# --------------------------------------------------------------------------
# Credential storage
# --------------------------------------------------------------------------
class CreatorCredentialStore:
    """File-backed store for creator credentials.

    Currently stores only the PIN hash. Future credentials (voice print
    path, backup codes) can be added as new top-level keys without any
    format migration.
    """

    _FORMAT_VERSION: Final[int] = 1

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    # -- reading -----------------------------------------------------------
    def pin_hash(self) -> str | None:
        data = self._load()
        if not data:
            return None
        value = data.get("pin_hash")
        return value if isinstance(value, str) else None

    def has_pin(self) -> bool:
        return self.pin_hash() is not None

    # -- writing -----------------------------------------------------------
    def set_pin(self, pin: str) -> None:
        data = self._load() or {"version": self._FORMAT_VERSION}
        data["pin_hash"] = hash_pin(pin)
        self._save(data)
        logger.info("creator PIN set", extra={"event": "CREATOR_PIN_SET"})

    def clear(self) -> None:
        if self._path.is_file():
            self._path.unlink()

    # -- internals ---------------------------------------------------------
    def _load(self) -> dict[str, object] | None:
        if not self._path.is_file():
            return None
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, json.JSONDecodeError):
            logger.exception(
                "failed to read credential file; treating as unconfigured",
                extra={"event": "CREATOR_CREDENTIALS_READ_FAILED"},
            )
            return None
        return data if isinstance(data, dict) else None

    def _save(self, data: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )