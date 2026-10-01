from __future__ import annotations

import pytest

from app.core.errors import ProviderNotFoundError
from app.core.registry import Registry


class Widget:
    def __init__(self, label: str = "default") -> None:
        self.label = label


def test_register_and_create_singleton() -> None:
    r: Registry[Widget] = Registry("widget")
    r.register("w", Widget)
    assert r.create("w") is r.create("w")


def test_non_singleton_creates_new_instances() -> None:
    r: Registry[Widget] = Registry("widget")
    r.register("w", Widget, singleton=False)
    assert r.create("w") is not r.create("w")


def test_kwargs_bypass_singleton_cache() -> None:
    r: Registry[Widget] = Registry("widget")
    r.register("w", Widget)

    first = r.create("w")
    configured = r.create("w", label="custom")

    assert first.label == "default"
    assert configured.label == "custom"
    assert configured is not first


def test_missing_provider_raises_clear_error() -> None:
    r: Registry[Widget] = Registry("widget")
    with pytest.raises(ProviderNotFoundError, match="widget provider 'missing' is not registered"):
        r.create("missing")


def test_duplicate_registration_is_rejected() -> None:
    r: Registry[Widget] = Registry("widget")
    r.register("w", Widget)
    with pytest.raises(ValueError, match="already registered"):
        r.register("w", Widget)


def test_replace_allows_overriding() -> None:
    r: Registry[Widget] = Registry("widget")
    r.register("w", lambda: Widget("old"))
    r.register("w", lambda: Widget("new"), replace=True)
    assert r.create("w").label == "new"


def test_names_are_case_insensitive_and_sorted() -> None:
    r: Registry[Widget] = Registry("widget")
    r.register("Zeta", Widget)
    r.register("alpha", Widget)

    assert r.names() == ("alpha", "zeta")
    assert r.has("ALPHA")
    assert "alpha" in r
    assert len(r) == 2


def test_unregister_removes_cached_instance() -> None:
    r: Registry[Widget] = Registry("widget")
    r.register("w", Widget)
    r.create("w")

    r.unregister("w")

    assert not r.has("w")
    with pytest.raises(ProviderNotFoundError):
        r.create("w")


def test_empty_name_is_rejected() -> None:
    r: Registry[Widget] = Registry("widget")
    with pytest.raises(ValueError, match="non-empty"):
        r.register("   ", Widget)