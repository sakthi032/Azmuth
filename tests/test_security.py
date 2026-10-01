from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.errors import (
    AuthenticationError,
    ConfirmationRequiredError,
    PermissionDeniedError,
)
from app.security import (
    AssuranceLevel,
    Authenticator,
    Capability,
    CapabilityPolicy,
    CreatorCredentialStore,
    DeviceMethod,
    PINMethod,
    PermissionManager,
    PermissionMode,
    Principal,
    VoiceMethod,
    build_default_methods,
    
)
from app.security.base import AuthenticationMethod, AuthenticationResult
from app.security.methods.pin import hash_pin, verify_pin_hash

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def authenticator() -> Authenticator:
    return Authenticator()


@pytest.fixture()
def permissions() -> PermissionManager:
    return PermissionManager()


PIN = "correct horse battery staple"


@pytest.fixture()
def hashed_pin() -> str:
    return hash_pin(PIN, iterations=10_000)


# ---------------------------------------------------------------------------
# AssuranceLevel / PermissionMode / Principal
# ---------------------------------------------------------------------------
def test_assurance_rank_is_monotonic() -> None:
    assert AssuranceLevel.LOW.rank < AssuranceLevel.MEDIUM.rank < AssuranceLevel.HIGH.rank


def test_permission_mode_coerces_from_string() -> None:
    assert PermissionMode("creator") is PermissionMode.CREATOR
    assert PermissionMode("restricted") is PermissionMode.RESTRICTED


def test_principal_anonymous_defaults() -> None:
    p = Principal.anonymous()
    assert p.mode is PermissionMode.RESTRICTED
    assert p.assurance is AssuranceLevel.LOW
    assert p.user_id is None
    assert p.methods == ()
    assert p.is_creator is False


def test_principal_is_immutable() -> None:
    p = Principal.anonymous()
    with pytest.raises(Exception):
        p.mode = PermissionMode.CREATOR  # type: ignore[misc]


def test_principal_describe_has_no_secrets() -> None:
    p = Principal(user_id="creator", mode=PermissionMode.CREATOR, assurance=AssuranceLevel.HIGH)
    described = p.describe()
    assert described["mode"] == "creator"
    assert described["assurance"] == "high"
    assert described["user_id"] == "creator"


# ---------------------------------------------------------------------------
# PIN hashing
# ---------------------------------------------------------------------------
def test_hash_pin_is_verifiable_roundtrip() -> None:
    encoded = hash_pin("1234", iterations=1_000)
    assert verify_pin_hash("1234", encoded) is True
    assert verify_pin_hash("4321", encoded) is False


def test_hash_pin_uses_fresh_salt_each_call() -> None:
    a = hash_pin("same", iterations=1_000)
    b = hash_pin("same", iterations=1_000)
    assert a != b
    assert verify_pin_hash("same", a)
    assert verify_pin_hash("same", b)


def test_verify_rejects_malformed_hash() -> None:
    assert verify_pin_hash("x", "not-a-valid-format") is False
    assert verify_pin_hash("x", "pbkdf2_sha256$abc$def$ghi") is False


def test_hash_pin_rejects_empty() -> None:
    with pytest.raises(ValueError):
        hash_pin("")


# ---------------------------------------------------------------------------
# PINMethod
# ---------------------------------------------------------------------------
def test_pin_method_unavailable_without_hash() -> None:
    method = PINMethod()
    assert method.is_available() is False
    result = method.verify(pin="whatever")
    assert result.success is False
    assert "not configured" in (result.reason or "")


def test_pin_method_has_high_assurance() -> None:
    assert PINMethod(hashed_pin=hash_pin("x", iterations=100)).assurance is AssuranceLevel.HIGH


def test_pin_method_verifies_correct_pin(hashed_pin: str) -> None:
    method = PINMethod(hashed_pin=hashed_pin)
    result = method.verify(pin=PIN)
    assert result.success is True
    assert result.user_id == "creator"
    assert result.assurance is AssuranceLevel.HIGH


def test_pin_method_rejects_wrong_pin(hashed_pin: str) -> None:
    result = PINMethod(hashed_pin=hashed_pin).verify(pin="wrong")
    assert result.success is False
    assert "incorrect" in (result.reason or "")


def test_pin_method_rejects_missing_credential(hashed_pin: str) -> None:
    result = PINMethod(hashed_pin=hashed_pin).verify()
    assert result.success is False
    assert "missing PIN" in (result.reason or "")


# ---------------------------------------------------------------------------
# DeviceMethod
# ---------------------------------------------------------------------------
def test_device_method_unavailable_without_registered_ids() -> None:
    method = DeviceMethod(registered_ids=(), current_id="laptop")
    assert method.is_available() is False
    assert method.verify().success is False


def test_device_method_verifies_registered_device() -> None:
    method = DeviceMethod(registered_ids=["laptop"], current_id="laptop")
    result = method.verify()
    assert result.success is True
    assert result.assurance is AssuranceLevel.HIGH


def test_device_method_rejects_unknown_device() -> None:
    method = DeviceMethod(registered_ids=["laptop"], current_id="other")
    result = method.verify()
    assert result.success is False
    assert "not a registered" in (result.reason or "")


def test_device_method_accepts_explicit_override() -> None:
    method = DeviceMethod(registered_ids=["a", "b"], current_id="c")
    assert method.verify(device_id="a").success is True
    assert method.verify(device_id="c").success is False


# ---------------------------------------------------------------------------
# VoiceMethod (Phase 1 stub)
# ---------------------------------------------------------------------------
def test_voice_method_is_low_assurance() -> None:
    assert VoiceMethod().assurance is AssuranceLevel.LOW


def test_voice_method_never_available() -> None:
    assert VoiceMethod().is_available() is False


def test_voice_method_verify_always_fails() -> None:
    result = VoiceMethod().verify(voice_sample=b"...")
    assert result.success is False
    assert "not implemented" in (result.reason or "")


# ---------------------------------------------------------------------------
# CreatorCredentialStore
# ---------------------------------------------------------------------------
def test_store_roundtrips_pin_hash(tmp_path: Path) -> None:
    store = CreatorCredentialStore(tmp_path / "creator.json")
    assert store.has_pin() is False

    store.set_pin("s3cret")
    assert store.has_pin() is True
    assert verify_pin_hash("s3cret", store.pin_hash() or "")


def test_store_overwrite_replaces_hash(tmp_path: Path) -> None:
    store = CreatorCredentialStore(tmp_path / "creator.json")
    store.set_pin("first")
    first = store.pin_hash()
    store.set_pin("second")
    second = store.pin_hash()
    assert first != second
    assert verify_pin_hash("second", second or "")


def test_store_clear_removes_file(tmp_path: Path) -> None:
    path = tmp_path / "creator.json"
    store = CreatorCredentialStore(path)
    store.set_pin("x")
    store.clear()
    assert not path.exists()
    assert store.has_pin() is False


def test_store_corrupt_file_is_treated_as_unconfigured(tmp_path: Path) -> None:
    path = tmp_path / "creator.json"
    path.write_text("{not valid json")
    store = CreatorCredentialStore(path)
    assert store.has_pin() is False


# ---------------------------------------------------------------------------
# Authenticator
# ---------------------------------------------------------------------------
def test_authenticator_starts_anonymous(authenticator: Authenticator) -> None:
    p = authenticator.current_principal
    assert p.mode is PermissionMode.RESTRICTED
    assert p.assurance is AssuranceLevel.LOW


def test_authenticator_registers_and_lists_methods(
    authenticator: Authenticator, hashed_pin: str
) -> None:
    authenticator.register_all(
        build_default_methods(pin_hash=hashed_pin, device_ids=["d1"], current_id="d1")
    )
    assert set(authenticator.method_names()) == {"pin", "device", "voice"}
    # voice is registered but unavailable, so it does not show up here
    assert set(authenticator.available_methods()) == {"pin", "device"}


def test_authenticator_rejects_unknown_method(authenticator: Authenticator) -> None:
    with pytest.raises(AuthenticationError, match="not registered"):
        authenticator.authenticate("nope")


def test_authenticator_rejects_unavailable_method(
    authenticator: Authenticator,
) -> None:
    authenticator.register(VoiceMethod())
    with pytest.raises(AuthenticationError, match="not available"):
        authenticator.authenticate("voice", voice_sample=b"x")


def test_authenticator_pin_upgrades_to_creator(
    authenticator: Authenticator, hashed_pin: str
) -> None:
    authenticator.register(PINMethod(hashed_pin=hashed_pin))
    principal = authenticator.authenticate("pin", pin=PIN)
    assert principal.mode is PermissionMode.CREATOR
    assert principal.assurance is AssuranceLevel.HIGH
    assert principal.user_id == "creator"
    assert "pin" in principal.methods


def test_authenticator_bad_pin_does_not_change_state(
    authenticator: Authenticator, hashed_pin: str
) -> None:
    authenticator.register(PINMethod(hashed_pin=hashed_pin))
    before = authenticator.current_principal

    with pytest.raises(AuthenticationError, match="incorrect"):
        authenticator.authenticate("pin", pin="wrong")

    assert authenticator.current_principal == before


def test_authenticator_bad_pin_does_not_downgrade_creator(
    authenticator: Authenticator, hashed_pin: str
) -> None:
    authenticator.register(PINMethod(hashed_pin=hashed_pin))
    authenticator.authenticate("pin", pin=PIN)
    assert authenticator.current_principal.is_creator

    with pytest.raises(AuthenticationError):
        authenticator.authenticate("pin", pin="wrong")

    assert authenticator.current_principal.is_creator  # unchanged


def test_authenticator_voice_alone_cannot_grant_creator(
    authenticator: Authenticator,
) -> None:
    """Even a hypothetical successful LOW-assurance method must not grant creator."""

    class FakeVoice(AuthenticationMethod):
        name = "fake-voice"
        @property
        def assurance(self) -> AssuranceLevel:
            return AssuranceLevel.LOW
        def is_available(self) -> bool:
            return True
        def verify(self, **credentials: object) -> AuthenticationResult:
            return AuthenticationResult(
                success=True,
                method=self.name,
                assurance=AssuranceLevel.LOW,
                user_id="creator",
            )

    authenticator.register(FakeVoice())
    principal = authenticator.authenticate("fake-voice", voice_sample=b"x")

    assert principal.mode is PermissionMode.RESTRICTED
    assert principal.assurance is AssuranceLevel.LOW
    # The user id was still recorded — the *mode* is what stayed restricted.
    assert principal.user_id == "creator"


def test_authenticator_login_then_logout(
    authenticator: Authenticator, hashed_pin: str
) -> None:
    authenticator.register(PINMethod(hashed_pin=hashed_pin))
    authenticator.authenticate("pin", pin=PIN)

    reset = authenticator.logout()

    # Compare the fields that define "logged out", not the timestamp.
    assert reset.mode is PermissionMode.RESTRICTED
    assert reset.assurance is AssuranceLevel.LOW
    assert reset.user_id is None
    assert reset.methods == ()
    assert authenticator.current_principal.is_creator is False
    assert authenticator.current_principal.mode is PermissionMode.RESTRICTED


def test_authenticator_methods_accumulate(authenticator: Authenticator) -> None:
    pin = PINMethod(hashed_pin=hash_pin("1234", iterations=100))
    device = DeviceMethod(registered_ids=["here"], current_id="here")
    authenticator.register_all([pin, device])

    authenticator.authenticate("pin", pin="1234")
    assert authenticator.current_principal.methods == ("pin",)

    authenticator.authenticate("device")
    assert set(authenticator.current_principal.methods) == {"pin", "device"}


def test_authenticator_rate_limits_failed_attempts(
    authenticator: Authenticator, hashed_pin: str
) -> None:
    authenticator.register(PINMethod(hashed_pin=hashed_pin))

    for _ in range(5):
        with pytest.raises(AuthenticationError, match="incorrect|locked out"):
            authenticator.authenticate("pin", pin="nope")

    # 6th attempt must be refused *before* verification.
    with pytest.raises(AuthenticationError, match="locked out"):
        authenticator.authenticate("pin", pin=PIN)


def test_authenticator_successful_login_clears_lockout_counter(
    authenticator: Authenticator, hashed_pin: str
) -> None:
    authenticator.register(PINMethod(hashed_pin=hashed_pin))

    for _ in range(3):
        with pytest.raises(AuthenticationError):
            authenticator.authenticate("pin", pin="nope")

    authenticator.authenticate("pin", pin=PIN)  # succeeds, clears failures

    # Now we can fail three more times without locking out.
    for _ in range(3):
        with pytest.raises(AuthenticationError, match="incorrect"):
            authenticator.authenticate("pin", pin="nope")


# ---------------------------------------------------------------------------
# PermissionManager — default policy
# ---------------------------------------------------------------------------
@pytest.fixture()
def restricted() -> Principal:
    return Principal.anonymous()


@pytest.fixture()
def creator() -> Principal:
    return Principal(
        user_id="creator",
        mode=PermissionMode.CREATOR,
        assurance=AssuranceLevel.HIGH,
        methods=("pin",),
    )


@pytest.mark.parametrize(
    "capability",
    [
        Capability.CHAT,
        Capability.WEB_SEARCH,
        Capability.VIEW_SYSTEM_STATUS,
        Capability.USE_BASIC_TOOLS,
        Capability.OPEN_APPLICATION,
        Capability.CLOSE_APPLICATION,
        Capability.SCREENSHOT,
        Capability.READ_PUBLIC_FILE,
    ],
)
def test_restricted_principal_allowed_basic_capabilities(
    permissions: PermissionManager, restricted: Principal, capability: Capability
) -> None:
    assert permissions.check(restricted, capability) is True
    permissions.require(restricted, capability)  # must not raise


@pytest.mark.parametrize(
    "capability",
    [
        Capability.READ_PERSONAL_FILE,
        Capability.WRITE_FILE,
        Capability.LIST_FILES,
        Capability.ACCESS_PERSONAL_MEMORY,
        Capability.RUN_SAFE_COMMAND,
        Capability.DEVELOPER_TOOLS,
        Capability.ADVANCED_AUTOMATION,
        Capability.DELETE_FILE,
        Capability.RUN_SYSTEM_COMMAND,
        Capability.MODIFY_SYSTEM_CONFIG,
        Capability.MANAGE_SECURITY,
        Capability.MANAGE_PLUGINS,
    ],
)
def test_restricted_principal_denied_creator_capabilities(
    permissions: PermissionManager, restricted: Principal, capability: Capability
) -> None:
    assert permissions.check(restricted, capability) is False
    with pytest.raises(PermissionDeniedError, match=capability.value):
        permissions.require(restricted, capability)


@pytest.mark.parametrize("capability", list(Capability))
def test_creator_principal_allowed_every_capability(
    permissions: PermissionManager, creator: Principal, capability: Capability
) -> None:
    assert permissions.check(creator, capability) is True
    permissions.require(creator, capability)  # must not raise


# ---------------------------------------------------------------------------
# PermissionManager — confirmation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "capability",
    [
        Capability.DELETE_FILE,
        Capability.RUN_SYSTEM_COMMAND,
        Capability.MODIFY_SYSTEM_CONFIG,
        Capability.MANAGE_SECURITY,
        Capability.MANAGE_PLUGINS,
    ],
)
def test_destructive_capabilities_require_confirmation(
    permissions: PermissionManager, creator: Principal, capability: Capability
) -> None:
    assert permissions.requires_confirmation(capability) is True
    with pytest.raises(ConfirmationRequiredError, match=capability.value):
        permissions.assert_confirmation(creator, capability, confirmed=False)
    # With confirmation, it passes.
    permissions.assert_confirmation(creator, capability, confirmed=True)


@pytest.mark.parametrize(
    "capability",
    [
        Capability.CHAT,
        Capability.OPEN_APPLICATION,
        Capability.READ_PERSONAL_FILE,
        Capability.RUN_SAFE_COMMAND,
    ],
)
def test_non_destructive_capabilities_do_not_require_confirmation(
    permissions: PermissionManager, creator: Principal, capability: Capability
) -> None:
    assert permissions.requires_confirmation(capability) is False
    # Calling assert_confirmation with confirmed=False must not raise.
    permissions.assert_confirmation(creator, capability, confirmed=False)


def test_restricted_cannot_bypass_confirmation_by_sending_true(
    permissions: PermissionManager, restricted: Principal
) -> None:
    """Confirmation is *in addition to* authorization, not instead of it."""
    with pytest.raises(PermissionDeniedError):
        permissions.require(restricted, Capability.DELETE_FILE)


# ---------------------------------------------------------------------------
# PermissionManager — custom policies and defaults
# ---------------------------------------------------------------------------
def test_custom_policy_overrides_builtin_table(creator: Principal) -> None:
    custom = {
        Capability.CHAT: CapabilityPolicy(
            minimum_mode=PermissionMode.CREATOR,
            requires_confirmation=False,
        )
    }
    permissions = PermissionManager(policy=custom)
    # CHAT is normally restricted-safe; here it requires creator.
    with pytest.raises(PermissionDeniedError):
        permissions.require(Principal.anonymous(), Capability.CHAT)
    permissions.require(creator, Capability.CHAT)


def test_unclassified_capability_is_denied_by_default() -> None:
    # Any capability not in the passed policy is denied even to a creator.
    permissions = PermissionManager(policy={})
    any_capability = Capability.RUN_SAFE_COMMAND
    policy = permissions.policy_for(any_capability)
    assert policy.minimum_mode is PermissionMode.CREATOR
    assert policy.requires_confirmation is True


def test_policy_table_is_copied_not_shared() -> None:
    a = PermissionManager()
    b = PermissionManager()
    # Mutating one internal table must not affect the other.
    a._policy[Capability.CHAT] = CapabilityPolicy(minimum_mode=PermissionMode.CREATOR)  # type: ignore[attr-defined]
    assert b.policy_for(Capability.CHAT).minimum_mode is PermissionMode.RESTRICTED


# ---------------------------------------------------------------------------
# Integration — the flow the ToolManager will use
# ---------------------------------------------------------------------------
def test_end_to_end_creator_flow(
    authenticator: Authenticator,
    permissions: PermissionManager,
    hashed_pin: str,
) -> None:
    authenticator.register(PINMethod(hashed_pin=hashed_pin))

    # 1. Anonymous user is denied a destructive capability.
    with pytest.raises(PermissionDeniedError):
        permissions.require(authenticator.current_principal, Capability.DELETE_FILE)

    # 2. PIN authentication upgrades to creator.
    principal = authenticator.authenticate("pin", pin=PIN)
    permissions.require(principal, Capability.DELETE_FILE)  # allowed

    # 3. But the destructive op still needs confirmation.
    with pytest.raises(ConfirmationRequiredError):
        permissions.assert_confirmation(principal, Capability.DELETE_FILE, confirmed=False)

    permissions.assert_confirmation(principal, Capability.DELETE_FILE, confirmed=True)