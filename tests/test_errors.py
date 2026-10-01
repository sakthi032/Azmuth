# tests/test_errors.py
from __future__ import annotations

import pytest

from app.core import errors


def test_every_error_derives_from_azmuth_error() -> None:
    for name in errors.__all__:
        assert issubclass(getattr(errors, name), errors.AzmuthError)


def test_specific_errors_are_catchable_as_their_parent() -> None:
    with pytest.raises(errors.ProviderError):
        raise errors.ProviderNotFoundError("no such provider")

    with pytest.raises(errors.ToolError):
        raise errors.ToolValidationError("bad arguments")

    with pytest.raises(errors.AzmuthError):
        raise errors.PermissionDeniedError("denied")