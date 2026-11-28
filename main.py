"""Azmuth 2.0 entry point.

Usage:

* ``py main.py``      — bootstrap check; prints a status summary.
* ``py main.py --ui`` — open the Azmuth desktop window (real brain).

CMD/PowerShell remains a development tool. The desktop window is the
normal interface.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Sequence

from app import __phase__, __version__
from app.config.logging_setup import get_logger, setup_logging
from app.config.settings import Settings, load_settings
from app.core.errors import AzmuthError


def bootstrap() -> Settings:
    """Load configuration, prepare directories and start logging."""
    settings = load_settings()
    settings.paths.ensure()
    setup_logging(settings)
    return settings


def _status_lines(settings: Settings) -> list[str]:
    return [
        f"AZMUTH {__version__} ({__phase__})",
        "",
        f"ENVIRONMENT     {settings.env}",
        f"LOG LEVEL       {settings.log_level}",
        f"LANGUAGES       {', '.join(settings.supported_languages)}"
        f" (default: {settings.default_language})",
        f"DATA ROOT       {settings.paths.data}",
        f"LLM PROVIDER    {settings.llm_provider}",
        f"SEARCH PROVIDERS {' → '.join(settings.search_provider_order)}",
        "",
        "All Phase 1 steps implemented.",
        "",
        "Run `py main.py --ui` to open the Azmuth window.",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    try:
        settings = bootstrap()
    except AzmuthError as exc:
        print(f"Azmuth failed to start: {exc}", file=sys.stderr)
        return 2

    logger = get_logger("bootstrap")
    logger.info(
        "Azmuth bootstrap complete",
        extra={"event": "SYSTEM_STARTED", "env": settings.env},
    )
    if "--check-models" in args:
        return _run_check_models(settings)
    if "--debug-tools" in args:
        idx = args.index("--debug-tools")
        msg = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("--") else None
        return _run_debug_tools(settings, message=msg)
    if "--check-tools" in args:
        return _run_check_tools(settings)
    if "--ui" in args:
        return _run_ui(settings)
    if "--chat" in args:
        return _run_chat(settings)

    print("\n".join(_status_lines(settings)))
    return 0


def _run_check_models(settings: Settings) -> int:
    """Check LLM provider and model availability."""
    import urllib.parse
    import httpx
    from app.llm.registry import build_default_registry
    from app.llm.router import LLMRouter

    print("\n=== Azmuth Model Routing Check ===\n")
    registry = build_default_registry(settings)
    router = LLMRouter(settings, registry)

    seen = set()
    unique_specs = []
    for s in registry.all():
        key = (s.provider, s.name)
        if key not in seen:
            seen.add(key)
            unique_specs.append(s)

    print(f"Registered Models ({len(unique_specs)} total):")
    for s in unique_specs:
        try:
            p = router._provider_for(s)
            avail = p.is_available()
            status = "AVAILABLE" if avail else "UNAVAILABLE (missing key/url or model not pulled)"
        except Exception as exc:
            status = f"ERROR ({exc})"
        print(f"  {s.name:45s} provider={s.provider:12s} [{status}]")

    print("\nProvider Health:")
    providers_info = [
        ("gemini", settings.gemini_api_key, settings.gemini_base_url, "gemini-flash-lite-latest"),
        ("nvidia_nim", settings.nvidia_api_key, settings.nvidia_base_url, "nvidia/nemotron-3.5-lightning-30b-a3b"),
        ("groq", settings.groq_api_key, settings.groq_base_url, "llama-3.3-70b-versatile"),
        ("ollama", None, settings.ollama_base_url, None),
    ]

    for prov_name, key, base_url, test_model in providers_info:
        host = urllib.parse.urlparse(base_url).netloc or base_url
        if prov_name == "ollama":
            key_status = "n/a"
            try:
                root = base_url.rstrip("/")
                if root.endswith("/v1"):
                    root = root[:-3]
                r = httpx.get(f"{root}/api/tags", timeout=3.0)
                if r.status_code == 200:
                    health_status = "OK"
                else:
                    health_status = f"HTTP {r.status_code}"
            except Exception as exc:
                health_status = f"unreachable ({type(exc).__name__})"
        else:
            key_status = "set" if key else "not set"
            if not key:
                health_status = "skipped (key not set)"
            else:
                try:
                    url = f"{base_url.rstrip('/')}/chat/completions"
                    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                    payload = {
                        "model": test_model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1,
                    }
                    r = httpx.post(url, headers=headers, json=payload, timeout=10.0)
                    if r.status_code == 200:
                        health_status = "OK"
                    else:
                        err_msg = ""
                        try:
                            err_msg = r.json().get("error", {}).get("message", "")
                        except Exception:
                            err_msg = r.text[:80]
                        if key and err_msg:
                            err_msg = err_msg.replace(key, "***")
                        health_status = f"HTTP {r.status_code}: {err_msg}" if err_msg else f"HTTP {r.status_code}"
                except Exception as exc:
                    health_status = f"error ({type(exc).__name__})"

        print(f"  {prov_name:12s} key: {key_status:8s} host: {host:36s} health: {health_status}")

    print("\nRouting by Purpose:")
    for row in router.describe_routing():
        purpose = row["purpose"]
        selected = row["selected"] or "none"
        candidates = ", ".join(row["candidates"])
        extra = ""
        if purpose == "embedding" and (selected == "none" or not row["selected"]):
            extra = " (reason: ollama model nomic-embed-text not pulled, fix: run 'ollama pull nomic-embed-text')"
        print(f"  {purpose:12s} -> selected={selected:30s} (candidates: {candidates}){extra}")
    print()
    return 0


def _run_debug_tools(settings: Settings, message: str | None = None) -> int:
    """Print registered tools and, if a message is provided, tool filtering analysis."""
    from app.brain import build_azmuth

    try:
        azmuth = build_azmuth(settings)
        all_registered = azmuth.tools.list_metadata()
    except Exception as exc:
        print(f"Could not build Azmuth: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 3

    principal = azmuth.authenticator.current_principal
    print("\n=== Azmuth Tool Diagnostics ===")
    if message:
        print(f"Target Message: \"{message}\"")
    print(f"Principal Mode: {principal.mode.value} (user_id={principal.user_id or 'anonymous'})")
    print(f"Total Registered Tools: {len(all_registered)}\n")

    # (a) Filtered by permission / capability
    perm_manager = azmuth.permissions
    filtered_by_perm = []
    available_tools = []
    for m in all_registered:
        tool_obj = azmuth.tools.registry.get(m.name)
        if tool_obj and not perm_manager.check(principal, tool_obj.capability):
            filtered_by_perm.append(m.name)
        else:
            available_tools.append(m)

    # (b) Filtered by is_available()
    filtered_by_avail = []
    ready_tools = []
    for m in available_tools:
        tool_obj = azmuth.tools.registry.get(m.name)
        if tool_obj and hasattr(tool_obj, "is_available") and not tool_obj.is_available():
            filtered_by_avail.append(m.name)
        else:
            ready_tools.append(m)

    # (c) Tool pruning / deduplication
    tools_for_llm = azmuth.get_tools_for_llm(principal=principal)
    llm_tool_names = {t.name for t in tools_for_llm}
    filtered_by_prune = [m.name for m in ready_tools if m.name not in llm_tool_names]

    print("--- Tool Filtering Analysis ---")
    print(f"(a) Filtered out by Permission/Capability ({len(filtered_by_perm)}):")
    for name in sorted(filtered_by_perm):
        tool_obj = azmuth.tools.registry.get(name)
        cap = getattr(tool_obj, "capability", "unknown")
        cap_val = getattr(cap, "value", str(cap))
        print(f"    - {name:30s} (requires {cap_val})")
    if not filtered_by_perm:
        print("    None")

    print(f"\n(b) Filtered out by is_available() == False ({len(filtered_by_avail)}):")
    for name in sorted(filtered_by_avail):
        tool_obj = azmuth.tools.registry.get(name)
        reason = tool_obj.unavailable_reason() if tool_obj and hasattr(tool_obj, "unavailable_reason") else None
        suffix = f" ({reason})" if reason else ""
        print(f"    - {name}{suffix}")
    if not filtered_by_avail:
        print("    None")

    print(f"\n(c) Filtered out by Tool Pruning/Deduplication ({len(filtered_by_prune)}):")
    for name in sorted(filtered_by_prune):
        print(f"    - {name}")
    if not filtered_by_prune:
        print("    None")

    print(f"\n--- Exact Tool List Sent to LLM ({len(tools_for_llm)}) ---")
    for m in sorted(tools_for_llm, key=lambda x: x.name):
        tool_obj = azmuth.tools.registry.get(m.name)
        dep_status = "OK" if (tool_obj is None or tool_obj.is_available()) else "UNAVAILABLE"
        print(f"  {m.name:32s} [{dep_status:11s}] - {m.description[:52]}...")
    print()
    return 0

# --------------------------------------------------------------------------
_SANDBOX_LIBRARIES: tuple[str, ...] = ("matplotlib", "numpy", "pandas", "openpyxl")


def _check_sandbox_libraries() -> tuple[bool, str]:
    """Check whether allow-listed sandbox data/plotting libraries are installed."""
    import importlib.util

    missing = [lib for lib in _SANDBOX_LIBRARIES if importlib.util.find_spec(lib) is None]
    if missing:
        cmd = f"pip install {' '.join(missing)}"
        return False, f"Missing sandbox libraries: {', '.join(missing)} — fix: {cmd}"
    return True, f"sandbox libraries ready ({', '.join(_SANDBOX_LIBRARIES)})"


# --------------------------------------------------------------------------
# Tool health check
# --------------------------------------------------------------------------
def _run_check_tools(settings: Settings) -> int:
    """Per-tool availability + read-only health check with actionable fix messages."""
    import subprocess
    from app.brain import build_azmuth

    _DEPENDENCY_HINTS: dict[str, str] = {
        "set_volume": "pip install pycaw comtypes",
        "mute": "pip install pycaw comtypes",
        "take_screenshot": "pip install Pillow",
        "describe_screen": "pip install Pillow",
    }

    try:
        azmuth = build_azmuth(settings)
    except Exception as exc:
        print(f"Could not build Azmuth: {exc}", file=sys.stderr)
        return 3

    def _dry_run_probe(tool_name: str) -> tuple[bool, str]:
        if tool_name in ("set_volume", "mute"):
            from app.automation.pc_controls import _get_volume_sync, _get_mute_sync
            if tool_name == "set_volume":
                vol = _get_volume_sync()
                return True, f"read-only check OK (volume={vol}%)"
            muted = _get_mute_sync()
            return True, f"read-only check OK (muted={muted})"
        if tool_name == "set_brightness":
            if sys.platform != "win32":
                return False, "Windows only"
            cp = subprocess.run(
                [
                    "powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness",
                ],
                capture_output=True, text=True, timeout=5.0, shell=False,
            )
            if cp.returncode == 0 and cp.stdout.strip():
                return True, f"read-only check OK (brightness={cp.stdout.strip()}%)"
            return True, "WMI brightness interface available (desktop monitor may lack WMI brightness)"
        if tool_name == "list_windows":
            tool_obj = azmuth.tools.registry.get("list_windows")
            res = asyncio.run(tool_obj.execute())
            count = len(res.get("windows", [])) if isinstance(res, dict) else len(res or [])
            return True, f"read-only check OK ({count} windows)"
        if tool_name in ("take_screenshot", "describe_screen"):
            import PIL.ImageGrab  # noqa: F401
            return True, "Pillow ImageGrab ready"
        if tool_name == "run_python":
            return _check_sandbox_libraries()
        return True, ""

    all_meta = azmuth.tools.list_metadata()
    print("\n=== Azmuth Tool Health Check ===\n")
    print(f"{'Tool':<35} {'Status':<12} {'Note'}")
    print("-" * 78)

    any_failed = False
    for m in sorted(all_meta, key=lambda x: x.name):
        tool_obj = azmuth.tools.registry.get(m.name)
        if tool_obj is None:
            status = "UNKNOWN"
            note = ""
        else:
            try:
                ok = tool_obj.is_available()
            except Exception as exc:
                ok = False
                note = f"is_available() raised: {exc}"
            else:
                if ok:
                    try:
                        probe_ok, note = _dry_run_probe(m.name)
                        if not probe_ok:
                            ok = False
                    except Exception as exc:
                        ok = False
                        fix = _DEPENDENCY_HINTS.get(m.name, "")
                        fix_str = f" — fix: {fix}" if fix else ""
                        note = f"FAILED: {exc}{fix_str}"
                else:
                    reason = tool_obj.unavailable_reason() if hasattr(tool_obj, "unavailable_reason") else None
                    fix = _DEPENDENCY_HINTS.get(m.name, "see tool documentation")
                    note = f"FAILED: {reason or 'unavailable'} — fix: {fix}"
            status = "OK" if ok else "FAILED"
        if status == "FAILED":
            any_failed = True
        print(f"  {m.name:<33} {status:<12} {note}")

    print()
    if any_failed:
        print("Some tools are unavailable. Run the fix commands above, then re-run --check-tools.")
        return 1
    print("All tools available.")
    return 0


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
def _run_ui(settings: Settings) -> int:
    from app.brain import build_azmuth
    from app.language import build_language_manager
    from app.security import CreatorCredentialStore, PINMethod
    from app.ui.app import AzmuthDesktopApp
    from app.ui.bridge import UIController

    language = build_language_manager(settings)
    azmuth = build_azmuth(settings)
    asyncio.run(azmuth.startup())

    # Ensure the credential store has a PIN. If it doesn't, prompt once
    # in the console. This is the only console touch in the whole UI
    # path, and it only fires on first launch.
    credentials = CreatorCredentialStore(
        settings.paths.users / "creator_credentials.json"
    )
    if not credentials.has_pin():
        print("First run: set a creator PIN.")
        import getpass
        pin = getpass.getpass("Choose a PIN (min 4 digits): ").strip()
        if len(pin) < 4:
            print("PIN too short. Aborting.", file=sys.stderr)
            return 3
        credentials.set_pin(pin)
        # Register it with the authenticator this session.
        from app.security import Authenticator
        azmuth._authenticator.register(
            PINMethod(hashed_pin=credentials.pin_hash() or "")
        )
        print("PIN stored. You can log in from the UI.")
    else:
        from app.security import PINMethod as _PIN
        azmuth._authenticator.register(
            _PIN(hashed_pin=credentials.pin_hash() or "")
        )

    # The responder is a closure so it can pass the confirmer that the
    # UI installs after the window is built.
    async def responder(
        user_input, conversation_id, confirmer, on_delta=None
    ):
        return await azmuth.respond_as_text(
            user_input,
            conversation_id,
            confirmer=confirmer,
            on_delta=on_delta,
        )

    controller = UIController(language=language, responder=responder)

    try:
        app = AzmuthDesktopApp(
            controller,
            authenticator=azmuth._authenticator,
            brain=azmuth.brain,
            voice=azmuth.voice,
            tts=azmuth.tts,
            event_bus=azmuth.event_bus,
            scheduler=azmuth.scheduler,
        )
        app.run()
    except RuntimeError as exc:
        import traceback
        print(f"Could not open the desktop UI: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 3
    finally:
        try:
            asyncio.run(azmuth.shutdown())
        except Exception:  # noqa: BLE001
            pass
    return 0

# --------------------------------------------------------------------------
# Chat (developer fallback; not the intended interface)
# --------------------------------------------------------------------------
def _run_chat(settings: Settings) -> int:
    from app.brain import build_azmuth
    from app.language import build_language_manager
    from app.voice import InputManager

    language = build_language_manager(settings)
    inputs = InputManager(language)
    azmuth = build_azmuth(settings)

    async def _chat() -> None:
        await azmuth.startup()
        try:
            print("Azmuth chat — type 'quit' to exit.")
            while True:
                try:
                    text = input("you> ").strip()
                except EOFError:
                    break
                if not text or text.lower() in {"quit", "exit"}:
                    break
                user_input = inputs.from_text(text)
                response = await azmuth.respond(user_input)
                print(f"azmuth> {response.text}")
        finally:
            await azmuth.shutdown()

    asyncio.run(_chat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())