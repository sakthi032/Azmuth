from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from app.core.errors import AutomationError, ProviderUnavailableError
from app.automation import (
    ApplicationPolicy,
    NullAutomation,
    PathPolicy,
    WindowsAutomation,
    WindowsAutomationImpl,
)
from app.automation.browser import _validate_url
from app.automation.applications import _parse_tasklist_csv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _run(coro):  # noqa: ANN001
    return asyncio.run(coro)


def _impl_with_sandbox(*roots: Path) -> WindowsAutomationImpl:
    policy = PathPolicy.from_paths(roots)
    return WindowsAutomationImpl(
        application_policy=ApplicationPolicy(aliases={}),
        path_policy=policy,
    )


# ---------------------------------------------------------------------------
# ApplicationPolicy
# ---------------------------------------------------------------------------
def test_application_policy_rejects_unknown_alias() -> None:
    policy = ApplicationPolicy(aliases={"foo": "/does/not/exist"})
    with pytest.raises(AutomationError, match="not in the allowlist"):
        policy.resolve("bar")


def test_application_policy_rejects_empty_alias() -> None:
    policy = ApplicationPolicy(aliases={"foo": "/does/not/exist"})
    with pytest.raises(AutomationError, match="non-empty"):
        policy.resolve("   ")


def test_application_policy_rejects_nonexistent_target() -> None:
    policy = ApplicationPolicy(aliases={"ghost": "/no/such/binary"})
    with pytest.raises(AutomationError, match="does not exist"):
        policy.resolve("ghost")


def test_application_policy_lookup_is_case_insensitive(tmp_path: Path) -> None:
    exe = tmp_path / "app.exe"
    exe.write_text("fake")
    policy = ApplicationPolicy(aliases={"myapp": str(exe)})
    assert policy.resolve("MyApp") == exe
    assert policy.resolve("MYAPP") == exe


def test_application_policy_lists_aliases_sorted() -> None:
    policy = ApplicationPolicy(aliases={"B": "/x", "a": "/y"})
    assert policy.known_aliases() == ("a", "b")


# ---------------------------------------------------------------------------
# PathPolicy
# ---------------------------------------------------------------------------
def test_path_policy_accepts_path_within_root(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    target = tmp_path / "sub" / "file.txt"
    target.write_text("hi")

    policy = PathPolicy.from_paths([tmp_path])
    assert policy.resolve_and_check(target) == target.resolve()


def test_path_policy_accepts_root_itself(tmp_path: Path) -> None:
    policy = PathPolicy.from_paths([tmp_path])
    assert policy.resolve_and_check(tmp_path) == tmp_path.resolve()


def test_path_policy_rejects_path_outside_root(tmp_path: Path) -> None:
    other = tmp_path.parent
    policy = PathPolicy.from_paths([tmp_path])
    with pytest.raises(AutomationError, match="outside the allowed sandbox"):
        policy.resolve_and_check(other)


def test_path_policy_rejects_dotdot_escape(tmp_path: Path) -> None:
    inside = tmp_path / "inside"
    inside.mkdir()
    escape = inside / ".." / "escape.txt"

    policy = PathPolicy.from_paths([inside])
    with pytest.raises(AutomationError, match="outside the allowed sandbox"):
        policy.resolve_and_check(escape)


def test_path_policy_rejects_empty_path() -> None:
    policy = PathPolicy.from_paths(["/tmp"])
    with pytest.raises(AutomationError, match="non-empty"):
        policy.resolve_and_check("   ")


def test_path_policy_with_no_roots_rejects_everything(tmp_path: Path) -> None:
    policy = PathPolicy()
    with pytest.raises(AutomationError, match="no sandbox roots"):
        policy.resolve_and_check(tmp_path)


def test_path_policy_contains_helper(tmp_path: Path) -> None:
    policy = PathPolicy.from_paths([tmp_path])
    assert policy.contains(tmp_path / "x") is True
    assert policy.contains(tmp_path.parent / "y") is False


# ---------------------------------------------------------------------------
# FileController — through WindowsAutomationImpl
# ---------------------------------------------------------------------------
def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    impl = _impl_with_sandbox(tmp_path)
    target = tmp_path / "note.txt"

    info = _run(impl.write_file(str(target), "hello world"))
    assert info.size == 11
    assert info.is_dir is False
    assert _run(impl.read_file(str(target))) == "hello world"


def test_write_refuses_overwrite_by_default(tmp_path: Path) -> None:
    impl = _impl_with_sandbox(tmp_path)
    target = tmp_path / "note.txt"
    _run(impl.write_file(str(target), "first"))
    with pytest.raises(AutomationError, match="already exists"):
        _run(impl.write_file(str(target), "second"))
    _run(impl.write_file(str(target), "second", overwrite=True))
    assert _run(impl.read_file(str(target))) == "second"


def test_read_rejects_path_outside_sandbox(tmp_path: Path) -> None:
    impl = _impl_with_sandbox(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    with pytest.raises(AutomationError, match="outside the allowed sandbox"):
        _run(impl.read_file(str(outside)))


def test_write_rejects_path_outside_sandbox(tmp_path: Path) -> None:
    impl = _impl_with_sandbox(tmp_path)
    with pytest.raises(AutomationError, match="outside the allowed sandbox"):
        _run(impl.write_file(str(tmp_path.parent / "bad.txt"), "x"))


def test_read_rejects_missing_file(tmp_path: Path) -> None:
    impl = _impl_with_sandbox(tmp_path)
    with pytest.raises(AutomationError, match="not a file"):
        _run(impl.read_file(str(tmp_path / "missing.txt")))


def test_delete_file(tmp_path: Path) -> None:
    impl = _impl_with_sandbox(tmp_path)
    target = tmp_path / "gone.txt"
    target.write_text("bye")

    assert _run(impl.delete_file(str(target))) is True
    assert not target.exists()
    assert _run(impl.delete_file(str(target))) is False


def test_delete_refuses_directory(tmp_path: Path) -> None:
    impl = _impl_with_sandbox(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    with pytest.raises(AutomationError, match="is a directory"):
        _run(impl.delete_file(str(sub)))
    assert sub.exists()


def test_list_directory_returns_entries(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b.txt").write_text("yy")
    (tmp_path / "sub").mkdir()

    impl = _impl_with_sandbox(tmp_path)
    entries = _run(impl.list_directory(str(tmp_path)))

    names = {Path(e.path).name for e in entries}
    assert names == {"a.txt", "b.txt", "sub"}
    sub_entry = next(e for e in entries if e.is_dir)
    assert Path(sub_entry.path).name == "sub"


def test_list_directory_rejects_file_path(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("x")
    impl = _impl_with_sandbox(tmp_path)
    with pytest.raises(AutomationError, match="not a directory"):
        _run(impl.list_directory(str(target)))


def test_file_read_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.automation.files as files_module
    monkeypatch.setattr(files_module, "_MAX_READ_BYTES", 4)

    big = tmp_path / "big.txt"
    big.write_text("12345")
    impl = _impl_with_sandbox(tmp_path)

    with pytest.raises(AutomationError, match="exceeding"):
        _run(impl.read_file(str(big)))


# ---------------------------------------------------------------------------
# BrowserController
# ---------------------------------------------------------------------------
def test_validate_url_accepts_http_and_https() -> None:
    assert _validate_url("http://example.com").scheme == "http"
    assert _validate_url("https://example.com/path?q=1").scheme == "https"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<script>",
        "vbscript:msgbox(1)",
        "ftp://example.com",
        "",
        "   ",
    ],
)
def test_validate_url_rejects_unsafe_schemes(url: str) -> None:
    with pytest.raises(AutomationError):
        _validate_url(url)


def test_validate_url_rejects_missing_host() -> None:
    with pytest.raises(AutomationError, match="host"):
        _validate_url("https:///path")


# ---------------------------------------------------------------------------
# NullAutomation
# ---------------------------------------------------------------------------
def test_null_automation_is_unavailable() -> None:
    automation = NullAutomation()
    assert automation.is_available() is False
    assert automation.name == "null"


@pytest.mark.parametrize(
    "call",
    [
        lambda a: a.open_application("chrome"),
        lambda a: a.close_application("1234"),
        lambda a: a.list_processes(),
        lambda a: a.read_file("/x"),
        lambda a: a.write_file("/x", "y"),
        lambda a: a.delete_file("/x"),
        lambda a: a.list_directory("/x"),
        lambda a: a.open_url("https://x"),
        lambda a: a.take_screenshot("/x"),
        lambda a: a.send_keys("x"),
        lambda a: a.move_mouse(0, 0),
        lambda a: a.click(0, 0),
        lambda a: a.list_windows(),
        lambda a: a.focus_window("x"),
    ],
)
def test_null_automation_raises_unavailable(call) -> None:  # noqa: ANN001
    with pytest.raises(ProviderUnavailableError):
        _run(call(NullAutomation()))


# ---------------------------------------------------------------------------
# WindowsAutomationImpl — availability & optional capabilities
# ---------------------------------------------------------------------------
def test_windows_automation_availability_follows_platform() -> None:
    impl = WindowsAutomationImpl()
    assert impl.is_available() is (sys.platform == "win32")
    assert impl.name == "windows"


def test_screenshot_raises_provider_unavailable(tmp_path: Path) -> None:
    impl = _impl_with_sandbox(tmp_path)
    with pytest.raises(ProviderUnavailableError, match="screenshot"):
        _run(impl.take_screenshot(str(tmp_path / "shot.png")))

class _RecordingInput:
    """Wraps InputController to record calls without sending real input.

    Real ``SendInput`` calls hit whatever window has focus — which, in a
    test run, is the terminal. These tests verify the *dispatch* path,
    not the OS interaction. A real end-to-end check belongs in a manual
    smoke test, not the automated suite.
    """

    def __init__(self) -> None:
        from app.automation.input import InputController
        self._real = InputController()
        self.sent: list[tuple[str, tuple, dict]] = []

    def is_available(self) -> bool:
        return self._real.is_available()

    async def send_keys(self, keys: str) -> None:
        self.sent.append(("send_keys", (keys,), {}))

    async def press_keys(self, keys: str) -> None:
        self.sent.append(("press_keys", (keys,), {}))

    async def type_text(self, text: str, **_: object) -> None:
        self.sent.append(("type_text", (text,), {}))

    async def move_mouse(self, x: int, y: int, **_: object) -> None:
        self.sent.append(("move_mouse", (x, y), {}))

    async def click(self, x: int | None = None, y: int | None = None, **_: object) -> None:
        self.sent.append(("click", (x, y), {}))


def test_input_controller_dispatches_send_keys(tmp_path: Path) -> None:
    """``send_keys`` forwards to the input controller without raising.

    Uses a fake input controller so no real keystrokes are sent.
    """
    impl = _impl_with_sandbox(tmp_path)
    recorder = _RecordingInput()
    impl._input = recorder  # type: ignore[assignment]

    _run(impl.send_keys("enter"))
    _run(impl.send_keys("ctrl+c"))

    assert len(recorder.sent) == 2
    assert recorder.sent[0] == ("send_keys", ("enter",), {})
    assert recorder.sent[1] == ("send_keys", ("ctrl+c",), {})


def test_input_controller_rejects_unknown_key_names(tmp_path: Path) -> None:
    """A real ``InputController`` raises on a nonsense key name.

    Uses the real controller but monkeypatches ``_send`` so no keystroke
    is emitted — the key-name validation runs before any send happens.
    """
    from app.automation.input import InputController

    controller = InputController()
    if not controller.is_available():
        pytest.skip("input controller is not available on this platform")

    sent: list[object] = []
    controller._send = lambda *a, **kw: sent.append((a, kw))  # type: ignore[method-assign]

    with pytest.raises(AutomationError, match="unknown key name"):
        _run(controller.press_keys("not_a_real_key_xyz"))

    assert sent == []  # nothing was emitted before the error


def test_mouse_operations_dispatch_without_sending(tmp_path: Path) -> None:
    impl = _impl_with_sandbox(tmp_path)
    recorder = _RecordingInput()
    impl._input = recorder  # type: ignore[assignment]

    _run(impl.move_mouse(100, 100))
    _run(impl.click(100, 100))

    assert recorder.sent[0][0] == "move_mouse"
    assert recorder.sent[1][0] == "click"
    

def test_list_windows_returns_a_list_on_windows(tmp_path: Path) -> None:
    """``list_windows`` is a real implementation on Windows.

    It returns a list (possibly empty if the desktop is fully hidden,
    but in practice always non-empty). On non-Windows it returns an
    empty list. It must never raise.
    """
    impl = _impl_with_sandbox(tmp_path)
    windows = _run(impl.list_windows())
    assert isinstance(windows, list)


def test_focus_window_raises_provider_unavailable(tmp_path: Path) -> None:
    """Window focus is not implemented — Windows blocks it by design.

    A background process cannot call SetForegroundWindow successfully
    without the foreground lock. The runtime reports this honestly
    rather than silently failing.
    """
    impl = _impl_with_sandbox(tmp_path)
    with pytest.raises(ProviderUnavailableError, match="window"):
        _run(impl.focus_window("any"))


# ---------------------------------------------------------------------------
# WindowsAutomationImpl — application launch (windows only)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific")
def test_launch_unknown_alias_fails(tmp_path: Path) -> None:
    impl = WindowsAutomationImpl(
        application_policy=ApplicationPolicy(aliases={}),
        path_policy=PathPolicy.from_paths([tmp_path]),
    )
    with pytest.raises(AutomationError, match="not in the allowlist"):
        _run(impl.open_application("anything"))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific")
def test_launch_and_close_notepad() -> None:
    """Exercises the launch + close pipeline against a real executable.

    The assertion is about *our* contract, not Windows' behaviour. On
    Windows 11 ``System32\\notepad.exe`` is a launcher that hands off to
    the packaged Store app and exits before we can terminate it; on
    earlier Windows versions the kill succeeds. Both outcomes are correct.
    What must hold on every version is: ``close_application`` returns an
    ``int`` and does not raise.
    """
    import os

    windir = os.environ.get("WINDIR", r"C:\Windows")
    notepad = Path(windir) / "System32" / "notepad.exe"
    if not notepad.is_file():
        pytest.skip("notepad.exe is not present on this system")

    impl = WindowsAutomationImpl(
        application_policy=ApplicationPolicy(aliases={"notepad": str(notepad)}),
        path_policy=PathPolicy(),
    )

    result = _run(impl.open_application("notepad"))
    assert result.alias == "notepad"
    assert result.pid > 0

    # Best-effort close. The OS may or may not permit termination; the
    # call must not raise and must return a non-negative count.
    terminated = _run(impl.close_application(str(result.pid)))
    assert isinstance(terminated, int)
    assert terminated >= 0

    # Best-effort cleanup of any Notepad still around, so the next run of
    # this test starts from a clean state. Failures are ignored — the OS
    # may refuse, and that is fine.
    try:
        _run(impl.close_application("Notepad.exe"))
    except Exception:  # noqa: BLE001 - cleanup is best-effort
        pass


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific")
def test_list_processes_returns_entries() -> None:
    impl = WindowsAutomationImpl(
        application_policy=ApplicationPolicy(aliases={}),
        path_policy=PathPolicy(),
    )
    processes = _run(impl.list_processes())
    assert len(processes) > 0

    # Windows exposes PID 0 for the System Idle Process, so ``> 0`` would
    # be wrong here. What we *can* assert is that every entry has a valid
    # non-negative integer pid and that at least one entry is a real user
    # process (positive pid).
    assert all(isinstance(p.pid, int) and p.pid >= 0 for p in processes)
    assert any(p.pid > 0 for p in processes)


# ---------------------------------------------------------------------------
# tasklist CSV parsing (no OS required)
# ---------------------------------------------------------------------------
def test_parse_tasklist_csv_basic() -> None:
    text = (
        '"notepad.exe","1234","Console","1","12,345 K"\n'
        '"chrome.exe","5678","Console","1","204,800 K"\n'
    )
    parsed = _parse_tasklist_csv(text)
    assert len(parsed) == 2
    assert parsed[0].name == "notepad.exe"
    assert parsed[0].pid == 1234
    assert parsed[0].memory_kb == 12345
    assert parsed[1].memory_kb == 204800


def test_parse_tasklist_csv_skips_malformed_rows() -> None:
    text = (
        '"only-one-column"\n'
        '"good.exe","42","Console","1","1,000 K"\n'
        '"bad-pid.exe","NaN","Console","1","100 K"\n'
    )
    parsed = _parse_tasklist_csv(text)
    assert [p.name for p in parsed] == ["good.exe"]


# ---------------------------------------------------------------------------
# Interface conformance
# ---------------------------------------------------------------------------
def test_both_backends_satisfy_interface() -> None:
    assert isinstance(NullAutomation(), WindowsAutomation)
    assert isinstance(WindowsAutomationImpl(), WindowsAutomation)