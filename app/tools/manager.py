"""The tool pipeline.

Every tool invocation in Azmuth passes through :meth:`ToolManager.invoke`,
which enforces:

    selection      -> name resolves to a registered, available tool
    permission     -> the Principal's mode satisfies the capability policy
    validation     -> arguments match the tool's JSON-schema subset
    confirmation   -> destructive capabilities require explicit user approval
    execution      -> the tool runs; exceptions are wrapped
    event emission -> TOOL_STARTED / TOOL_COMPLETED / TOOL_FAILED

Ordering rationale:

* **permission before validation** -- arguments to a capability the principal
  cannot use are none of their business, and refusing earlier avoids leaking
  schema details.
* **validation before confirmation** -- the user should confirm a *specific*
  action with valid arguments, not an under-specified one.
* **confirmation before execution** -- the whole point of the gate.

The manager is async and side-effect free with respect to the tool. It
never mutates the Principal, never caches results, and never retries.

Performance note (Phase 1.3):
  ``tool.is_available()`` can make network calls (e.g. TCP connect to
  Chrome DevTools ports).  The manager now caches availability results with
  a configurable TTL (default 3 s) so the check runs at most once per TTL
  window per tool.  The cache is bypassed in ``invoke()`` with a shorter TTL
  (0.5 s) to keep execution-time checks accurate.
"""

from __future__ import annotations

import inspect
import time
from typing import Any

from app.config.logging_setup import get_logger
from app.core.errors import (
    ConfirmationRequiredError,
    PermissionDeniedError,
    ProviderUnavailableError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolValidationError,
)
from app.core.events import EventBus, EventName, emit
from app.security.capabilities import Capability
from app.security.modes import Principal
from app.security.permissions import PermissionManager
from app.tools.base import Tool, ToolMetadata, ToolResult
from app.tools.registry import ToolRegistry
from app.tools.schema import validate as validate_arguments

logger = get_logger("tools")

#: Default TTL (seconds) for caching tool.is_available() in list_metadata().
_DEFAULT_LIST_TTL: float = 3.0
#: Shorter TTL used in invoke() to keep execution-time checks accurate.
_DEFAULT_INVOKE_TTL: float = 0.5


class ToolManager:
    """Register tools and run them safely under a Principal."""

    def __init__(
        self,
        permissions: PermissionManager,
        registry: ToolRegistry | None = None,
        *,
        event_bus: EventBus | None = None,
        availability_cache_ttl: float = _DEFAULT_LIST_TTL,
    ) -> None:
        self._permissions = permissions
        self._registry = registry if registry is not None else ToolRegistry()
        self._event_bus = event_bus
        self._availability_cache_ttl = availability_cache_ttl
        # Cache: tool_name -> (is_available: bool, timestamp: float)
        self._availability_cache: dict[str, tuple[bool, float]] = {}

    # -- registration ------------------------------------------------------
    @property
    def permissions(self) -> PermissionManager:
        return self._permissions

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def register(self, tool: Tool, *, replace: bool = False) -> None:
        self._registry.register(tool, replace=replace)
        # Invalidate any cached availability for this tool name so the new
        # registration is reflected immediately.
        self._availability_cache.pop(tool.name, None)
        logger.info(
            "tool registered",
            extra={
                "event": "TOOL_REGISTERED",
                "tool": tool.name,
                "capability": tool.capability.value,
            },
        )

    def unregister(self, name: str) -> bool:
        self._availability_cache.pop(name, None)
        return self._registry.unregister(name)

    # -- discovery ---------------------------------------------------------
    def names(self) -> tuple[str, ...]:
        return self._registry.names()

    def metadata(self, name: str) -> ToolMetadata:
        return self._registry.get(name).metadata()

    # -- availability cache ------------------------------------------------
    def _check_available(self, tool: Tool, *, ttl: float) -> bool:
        """Return tool.is_available() with TTL caching.

        Results are cached for ``ttl`` seconds to avoid repeated expensive
        calls (e.g. TCP connects to Chrome DevTools ports).  Exceptions
        from ``is_available()`` are propagated unchanged -- the caller
        decides how to handle them.
        """
        now = time.monotonic()
        cached = self._availability_cache.get(tool.name)
        if cached is not None:
            value, ts = cached
            if now - ts < ttl:
                return value
        # Cache miss or expired -- call the real check
        result = tool.is_available()
        self._availability_cache[tool.name] = (result, now)
        return result

    def invalidate_availability(self, name: str | None = None) -> None:
        """Invalidate the availability cache for one tool (or all if None)."""
        if name is None:
            self._availability_cache.clear()
        else:
            self._availability_cache.pop(name, None)

    def list_metadata(
        self, *, principal: Principal | None = None
    ) -> tuple[ToolMetadata, ...]:
        """Return metadata for every tool the principal may invoke.

        When ``principal`` is given, tools whose capability the principal is
        not allowed to use are filtered out. Tools that are not currently
        available (see :meth:`Tool.is_available`) are also filtered out.
        This is exactly the list the brain will hand to the LLM for tool
        selection.

        Availability is cached for ``availability_cache_ttl`` seconds to
        avoid repeated expensive network probes (Phase 1.3).
        """
        result: list[ToolMetadata] = []
        for tool in self._registry.tools():
            try:
                if not self._check_available(tool, ttl=self._availability_cache_ttl):
                    continue
            except Exception:  # noqa: BLE001 - see Tool.is_available contract
                logger.exception(
                    "tool availability check failed",
                    extra={"event": "TOOL_AVAILABILITY_FAILED", "tool": tool.name},
                )
                continue
            if principal is not None:
                can_use = self._permissions.check(principal, tool.capability)
                if not can_use and self._permissions.workspace_policy.approved_roots:
                    # Non-destructive project workspace tools can be used in approved workspaces
                    if tool.capability in (
                        Capability.READ_PERSONAL_FILE,
                        Capability.LIST_FILES,
                        Capability.WRITE_FILE,
                    ):
                        can_use = True
                if not can_use:
                    continue
            result.append(tool.metadata())
        return tuple(result)

    # -- the pipeline ------------------------------------------------------
    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        principal: Principal,
        confirmed: bool = False,
    ) -> ToolResult:
        """Run ``name`` with ``arguments`` under ``principal``.

        Raises:

        * :class:`ToolNotFoundError`      -- unknown tool name.
        * :class:`PermissionDeniedError`  -- capability denied.
        * :class:`ToolValidationError`    -- arguments invalid.
        * :class:`ConfirmationRequiredError` -- destructive capability
          without explicit confirmation.
        * :class:`ProviderUnavailableError` -- tool is not currently available.
        * :class:`ToolExecutionError`     -- the tool raised while running.
        """
        arguments = dict(arguments or {})

        # ---- selection ---------------------------------------------------
        tool = self._registry.get(name)

        # Extract target path(s) if this tool operates on paths
        target_path = arguments.get("path") or arguments.get("source")

        # ---- permission --------------------------------------------------
        self._permissions.require(principal, tool.capability, path=target_path)
        if arguments.get("destination"):
            self._permissions.require(
                principal, tool.capability, path=arguments["destination"]
            )

        # ---- availability ------------------------------------------------
        # Use a short TTL for invoke() so the check is reasonably fresh at
        # execution time, but still benefits from the cache when multiple
        # tools are invoked in rapid succession (Phase 1.3).
        try:
            available = self._check_available(tool, ttl=_DEFAULT_INVOKE_TTL)
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError(
                f"tool {name!r} availability check failed: {exc}"
            ) from exc
        if not available:
            raise ProviderUnavailableError(
                f"tool {name!r} is not available in this environment"
            )

        # ---- validation --------------------------------------------------
        errors = validate_arguments(arguments, tool.parameters)
        if errors:
            raise ToolValidationError(
                f"invalid arguments for {name!r}: " + "; ".join(errors)
            )

        # ---- confirmation ------------------------------------------------
        # The PermissionManager combines the capability policy and the tool's
        # own flag additively, so we can call it unconditionally.
        self._permissions.assert_confirmation(
            principal,
            tool.capability,
            confirmed=confirmed,
            required=tool.requires_confirmation,
        )

        # ---- execution ---------------------------------------------------
        await self._emit(
            EventName.TOOL_STARTED,
            source="tools",
            tool=name,
            capability=tool.capability.value,
            mode=principal.mode.value,
        )
        logger.info(
            "tool invoking",
            extra={
                "event": "TOOL_INVOKING",
                "tool": name,
                "capability": tool.capability.value,
                "principal_mode": principal.mode.value,
            },
        )

        started = time.perf_counter()
        try:
            raw = tool.execute(**arguments)
            output = await raw if inspect.isawaitable(raw) else raw
        except Exception as exc:  # noqa: BLE001 - last line of defence
            duration_ms = (time.perf_counter() - started) * 1000
            await self._emit(
                EventName.TOOL_COMPLETED,
                source="tools",
                tool=name,
                capability=tool.capability.value,
                success=False,
                error=str(exc),
            )
            logger.exception(
                "tool execution failed",
                extra={"event": "TOOL_FAILED", "tool": name},
            )
            raise ToolExecutionError(f"tool {name!r} failed: {exc}") from exc

        duration_ms = (time.perf_counter() - started) * 1000
        result = self._coerce_result(name, output, duration_ms)

        await self._emit(
            EventName.TOOL_COMPLETED,
            source="tools",
            tool=name,
            capability=tool.capability.value,
            success=True,
            duration_ms=round(duration_ms, 3),
        )
        logger.info(
            "tool completed",
            extra={
                "event": "TOOL_COMPLETED",
                "tool": name,
                "duration_ms": round(duration_ms, 3),
            },
        )
        return result

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _coerce_result(name: str, output: Any, duration_ms: float) -> ToolResult:
        if isinstance(output, ToolResult):
            # Preserve the tool's own result but stamp name and duration so
            # callers always see consistent fields.
            return ToolResult(
                success=output.success,
                tool=name,
                output=output.output,
                error=output.error,
                duration_ms=duration_ms,
                metadata=dict(output.metadata),
            )
        return ToolResult(
            success=True,
            tool=name,
            output=output,
            duration_ms=duration_ms,
        )

    async def _emit(self, name: EventName, **payload: Any) -> None:
        if self._event_bus is None:
            return
        try:
            await emit(self._event_bus, name, **payload)
        except Exception:  # noqa: BLE001 - event delivery must never break the pipeline
            logger.exception(
                "failed to emit tool event",
                extra={"event": name.value},
            )

    def requires_confirmation(self, name: str) -> bool:
        """True if the tool or its capability requires user confirmation."""
        tool = self._registry.get(name)
        if tool.requires_confirmation:
            return True
        return self._permissions.requires_confirmation(tool.capability)

__all__ = ["ToolManager"]
