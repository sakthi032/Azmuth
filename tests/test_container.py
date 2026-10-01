from __future__ import annotations

import pytest

from app.core.container import Container
from app.core.errors import ConfigurationError


class Service:
    def __init__(self, name: str) -> None:
        self.name = name


def test_factory_is_lazy_and_cached(container: Container) -> None:
    calls: list[int] = []

    def factory(_c: Container) -> Service:
        calls.append(1)
        return Service("svc")

    container.register("svc", factory)

    assert calls == []  # not built yet
    a = container.get("svc")
    b = container.get("svc")

    assert a is b
    assert calls == [1]


def test_register_instance_returns_same_object(container: Container) -> None:
    svc = Service("x")
    container.register_instance("svc", svc)
    assert container.get("svc") is svc


def test_factory_can_resolve_dependencies(container: Container) -> None:
    container.register("a", lambda _c: Service("a"))

    def make_b(c: Container) -> Service:
        return Service(f"b<-{c.get('a').name}")

    container.register("b", make_b)

    assert container.get("b").name == "b<-a"


def test_missing_service_raises(container: Container) -> None:
    with pytest.raises(ConfigurationError, match="not registered"):
        container.get("nope")


def test_duplicate_registration_is_rejected(container: Container) -> None:
    container.register("svc", lambda _c: Service("x"))
    with pytest.raises(ValueError, match="already registered"):
        container.register("svc", lambda _c: Service("y"))


def test_replace_flag_allows_override(container: Container) -> None:
    container.register("svc", lambda _c: Service("old"))
    container.register("svc", lambda _c: Service("new"), replace=True)
    assert container.get("svc").name == "new"


def test_register_instance_after_factory_requires_replace(container: Container) -> None:
    container.register("svc", lambda _c: Service("x"))
    with pytest.raises(ValueError, match="already registered"):
        container.register_instance("svc", Service("y"))


def test_cycle_is_reported_with_chain(container: Container) -> None:
    container.register("a", lambda c: c.get("b"))
    container.register("b", lambda c: c.get("a"))

    with pytest.raises(ConfigurationError, match="circular service dependency"):
        container.get("a")


def test_keys_lists_registered_and_instantiated(container: Container) -> None:
    container.register("a", lambda _c: Service("a"))
    container.register_instance("b", Service("b"))

    assert container.keys() == ("a", "b")