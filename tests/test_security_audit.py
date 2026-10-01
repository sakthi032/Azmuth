"""Security invariant tests.

Two kinds of assertion live here:

* **Static scans** — the audit script from ``scripts/security_audit.py``
  runs against the real ``app/`` tree. A finding fails the test.
* **Self-tests** — the audit is run against *planted* bad code, to prove
  the audit itself is not vacuous. Without these, an audit that always
  returned "clean" would pass this file.

The static scans are backed by runtime checks that import the live
classes and verify their properties directly. Between the two, a change
that weakens a guarantee fails the suite.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

from app.security.capabilities import Capability
from app.tools.base import Tool
from scripts.security_audit import (
    DANGEROUS_CALLS,
    audit_tree,
    scan_dangerous_calls,
    scan_hardcoded_secrets,
    scan_tools_have_capability,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "app"


# ---------------------------------------------------------------------------
# The live tree is clean
# ---------------------------------------------------------------------------
@pytest.mark.adversarial
def test_app_tree_has_no_findings() -> None:
    """The real ``app/`` tree must pass every check."""
    findings = audit_tree(APP_ROOT)
    assert findings == [], "\n".join(str(f) for f in findings)


@pytest.mark.adversarial
def test_audit_actually_walks_the_tree() -> None:
    """Guard against the audit being silently skipped."""
    files = list(APP_ROOT.rglob("*.py"))
    assert len(files) > 50, f"only found {len(files)} .py files under app/"


# ---------------------------------------------------------------------------
# Self-tests: the audit catches planted violations
# ---------------------------------------------------------------------------
def _scan_source(source: str, path: str = "<test>") -> list:
    tree = ast.parse(source)
    findings = list(scan_dangerous_calls(tree, Path(path)))
    findings.extend(scan_hardcoded_secrets(tree, Path(path)))
    findings.extend(scan_tools_have_capability(tree, Path(path)))
    return findings


@pytest.mark.adversarial
def test_audit_detects_shell_true() -> None:
    source = "import subprocess\nsubprocess.run(['ls'], shell=True)\n"
    findings = _scan_source(source)
    rules = {f.rule for f in findings}
    assert "shell-true" in rules


@pytest.mark.adversarial
def test_audit_detects_eval() -> None:
    findings = _scan_source("eval('1+1')\n")
    assert any(f.rule == "dangerous-call" and "eval" in f.detail for f in findings)


@pytest.mark.adversarial
def test_audit_detects_exec() -> None:
    findings = _scan_source("exec('x = 1')\n")
    assert any(f.rule == "dangerous-call" and "exec" in f.detail for f in findings)


@pytest.mark.adversarial
def test_audit_detects_os_system() -> None:
    findings = _scan_source("import os\nos.system('rm -rf /')\n")
    assert any(f.rule == "dangerous-call" and "os.system" in f.detail for f in findings)


@pytest.mark.adversarial
def test_audit_detects_pickle_loads() -> None:
    findings = _scan_source("import pickle\npickle.loads(b'x')\n")
    assert any("pickle" in f.detail for f in findings)


@pytest.mark.adversarial
def test_audit_detects_hardcoded_secret() -> None:
    source = 'api_key = "sk-1234567890abcdefghijklmnop"\n'
    findings = _scan_source(source)
    assert any(f.rule == "hardcoded-secret" for f in findings)


@pytest.mark.adversarial
def test_audit_does_not_flag_short_strings() -> None:
    # A short string is usually a format hint or a placeholder, not a secret.
    findings = _scan_source('api_key = "x"\n')
    assert findings == []


@pytest.mark.adversarial
def test_audit_detects_tool_without_capability() -> None:
    source = (
        "from app.tools.base import Tool\n"
        "class BadTool(Tool):\n"
        "    name = 'bad'\n"
        "    async def execute(self):\n"
        "        pass\n"
    )
    findings = _scan_source(source)
    assert any(f.rule == "tool-missing-capability" for f in findings)


@pytest.mark.adversarial
def test_audit_does_not_flag_tool_with_capability() -> None:
    source = (
        "from app.tools.base import Tool\n"
        "from app.security.capabilities import Capability\n"
        "class GoodTool(Tool):\n"
        "    name = 'good'\n"
        "    capability = Capability.CHAT\n"
        "    async def execute(self):\n"
        "        pass\n"
    )
    findings = _scan_source(source)
    assert findings == []


# ---------------------------------------------------------------------------
# Runtime invariants
# ---------------------------------------------------------------------------
@pytest.mark.adversarial
def test_every_live_tool_has_a_valid_capability() -> None:
    """Every registered tool subclass declares a real Capability."""
    # Force-import every module that might define a tool.
    modules_to_load = [
        "app.tools.builtin.echo",
        "app.tools.builtin.get_time",
        "app.plugins.builtin.uppercase.plugin",
    ]
    for module_name in modules_to_load:
        importlib.import_module(module_name)

    tool_classes = _all_tool_subclasses()
    assert tool_classes, "no Tool subclasses discovered at runtime"

    for cls in tool_classes:
        capability = getattr(cls, "capability", None)
        assert capability is not None, (
            f"{cls.__module__}.{cls.__qualname__} has no capability"
        )
        assert isinstance(capability, Capability), (
            f"{cls.__module__}.{cls.__qualname__}.capability is not a Capability"
        )


@pytest.mark.adversarial
def test_every_live_tool_declares_name_and_description() -> None:
    for cls in _all_tool_subclasses():
        assert getattr(cls, "name", ""), f"{cls.__qualname__} has no name"
        assert getattr(cls, "description", ""), f"{cls.__qualname__} has no description"
        assert isinstance(getattr(cls, "parameters", None), dict), (
            f"{cls.__qualname__} has no parameters schema"
        )


@pytest.mark.adversarial
def test_no_provider_family_lacks_a_null_default() -> None:
    """Every provider registry must expose a 'null' or 'stub' default."""
    from app.llm.providers import build_llm_registry
    from app.search.providers import build_search_registry
    from app.voice.tts.providers import build_tts_registry
    from app.voice.wake import NullWakeWordDetector
    from app.voice.sources import NullAudioSource
    from app.voice.stt import NullSpeechToTextProvider

    registries_and_defaults = [
        (build_llm_registry(), {"stub"}),
        (build_search_registry(), {"null"}),
        (build_tts_registry(), {"null"}),
    ]
    for registry, expected in registries_and_defaults:
        names = set(registry.names())
        assert names & expected, (
            f"registry {registry.kind!r} has no default provider; names={names}"
        )

    # Providers without a registry still expose a null class.
    assert NullWakeWordDetector().is_available() is False
    assert NullAudioSource().is_available() is False
    assert NullSpeechToTextProvider().is_available() is False


@pytest.mark.adversarial
def test_permission_manager_denies_unknown_capability_by_default() -> None:
    """An unclassified capability defaults to CREATOR + confirmation."""
    from app.security.capabilities import DEFAULT_POLICY
    from app.security.modes import PermissionMode
    from app.security.permissions import PermissionManager

    # Simulate a capability that is not in the shipped table by building a
    # manager with an empty custom policy.
    manager = PermissionManager(policy={})
    capability = Capability.CHAT  # any capability not in the empty policy
    policy = manager.policy_for(capability)
    assert policy.minimum_mode is PermissionMode.CREATOR
    assert policy.requires_confirmation is True


@pytest.mark.adversarial
def test_secret_redaction_covers_common_names() -> None:
    from app.config.logging_setup import _is_sensitive

    for key in (
        "api_key", "API_KEY", "password", "passwd", "pin", "token",
        "credential", "authorization", "secret",
    ):
        assert _is_sensitive(key), f"{key!r} should be redacted"


# ---------------------------------------------------------------------------
# The audit's rule list is not accidentally empty
# ---------------------------------------------------------------------------
@pytest.mark.adversarial
def test_dangerous_call_list_is_populated() -> None:
    assert len(DANGEROUS_CALLS) >= 10
    for name in ("eval", "exec", "os.system", "pickle.loads"):
        assert name in DANGEROUS_CALLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _all_tool_subclasses() -> set[type[Tool]]:
    """Walk subclasses of Tool transitively."""
    found: set[type[Tool]] = set()
    stack: list[type[Tool]] = [Tool]
    while stack:
        parent = stack.pop()
        for child in parent.__subclasses__():
            if child in found:
                continue
            found.add(child)
            stack.append(child)
    # Exclude abstract intermediates.
    return {cls for cls in found if not inspect.isabstract(cls)}