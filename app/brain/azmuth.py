"""The top-level facade.

Owns one instance of every subsystem, wires them, and exposes:

* :meth:`Azmuth.respond` — the pipeline entry point used by the UI.
* :meth:`Azmuth.status` — subsystem health for the status bar.
* :meth:`Azmuth.shutdown` — best-effort release of resources.

Construction is via :func:`build_azmuth`. Every dependency is a keyword
argument so tests can substitute any subsystem without subclassing.
The factory builds the defaults (settings-driven) and lets callers
override any of them.
"""

from __future__ import annotations

import asyncio

from app.brain.pipeline import Brain, BrainResponse
from app.config.logging_setup import get_logger
from app.config.settings import Settings
from app.core.events import EventBus
from app.llm import LLMManager, build_llm_registry
from app.llm.base import LLMProvider
from app.memory import build_memory_manager
from app.memory.conversation.manager import SessionManager
from app.memory.manager import MemoryManager
from app.plugins import PluginManager, build_plugin_manager
from app.search import SearchManager, build_search_manager
from app.security import Authenticator, PermissionManager, Principal
from app.security.capabilities import Capability
from app.tasks import TaskManager
from app.tools import ToolManager, build_builtin_tools
from app.ui.state import StatusSnapshot
from app.voice import (
    SpeechToTextProvider,
    TTSManager,
    VoiceInputManager,
    WakeWordDetector,
    build_tts_manager,
    build_voice_input_manager,
    build_wake_word_detector,
)
from app.voice.input_manager import UserInput


from app.core.errors import ProviderUnavailableError
from app.llm import (
    LLMManager,
    LLMRouter,
    ModelPurpose,
    build_default_registry,
    build_llm_registry,
)
from app.llm.providers import OllamaProvider  # noqa: F401  (for discoverability)


logger = get_logger("brain.azmuth")


class Azmuth:
    """The whole assistant, as one object."""

    def __init__(
        self,
        *,
        settings: Settings,
        event_bus: EventBus,
        language,               # LanguageManager
        llm_provider: LLMProvider,
        memory: MemoryManager,
        sessions: SessionManager,
        tools: ToolManager,
        search: SearchManager,
        tts: TTSManager,
        voice: VoiceInputManager,
        wake: WakeWordDetector,
        tasks: TaskManager,
        permissions: PermissionManager,
        authenticator: Authenticator,
        plugin_manager: PluginManager,
        brain: Brain,
    ) -> None:
        self._settings = settings
        self._event_bus = event_bus
        self._language = language
        self._llm_provider = llm_provider
        self._memory = memory
        self._sessions = sessions
        self._tools = tools
        self._search = search
        self._tts = tts
        self._voice = voice
        self._wake = wake
        self._tasks = tasks
        self._permissions = permissions
        self._authenticator = authenticator
        self._plugins = plugin_manager
        self._brain = brain
        self._started = False

    # -- accessors ---------------------------------------------------------
    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def language(self):  # noqa: ANN201
        return self._language

    @property
    def brain(self) -> Brain:
        return self._brain

    @property
    def memory(self) -> MemoryManager:
        return self._memory

    @property
    def tools(self) -> ToolManager:
        return self._tools

    @property
    def search(self) -> SearchManager:
        return self._search

    @property
    def tts(self) -> TTSManager:
        return self._tts

    @property
    def voice(self) -> VoiceInputManager:
        return self._voice

    @property
    def wake(self) -> WakeWordDetector:
        return self._wake

    @property
    def tasks(self) -> TaskManager:
        return self._tasks

    @property
    def permissions(self) -> PermissionManager:
        return self._permissions

    @property
    def authenticator(self) -> Authenticator:
        return self._authenticator

    @property
    def plugins(self) -> PluginManager:
        return self._plugins

    # -- pipeline ----------------------------------------------------------
    async def respond(
        self,
        user_input: UserInput,
        *,
        conversation_id: str | None = None,
        principal: Principal | None = None,
    ) -> BrainResponse:
        """Process one user turn."""
        return await self._brain.respond(
            user_input,
            conversation_id=conversation_id,
            principal=principal,
        )

    async def respond_as_text(
        self,
        user_input: UserInput,
        conversation_id: str | None = None,
        *,
        principal: Principal | None = None,
    ) -> tuple[str, str]:
        """UI responder: return ``(reply_text, session_id)``.

        The session id is returned so the caller can pass it back on the
        next turn — that is what makes short-term memory persist across
        turns through the desktop UI.

        When ``principal`` is not given, the current principal from the
        authenticator is used, so the UI automatically gets the right
        mode (restricted vs creator) without holding its own copy.
        """
        if principal is None:
            principal = self._authenticator.current_principal
        response = await self.respond(
            user_input,
            conversation_id=conversation_id,
            principal=principal,
        )
        return response.text, response.session_id
        

    # -- lifecycle ---------------------------------------------------------
    async def startup(self) -> None:
        if self._started:
            return
        self._started = True
        await self._plugins.startup()

        # Detect location once. Best-effort; failure is logged and the
        # prompt simply omits the location line.
        try:
            self._brain._location_service and self._brain._location_service.detect()
        except Exception:  # noqa: BLE001
            logger.exception("location detection failed")
        # Warm up cloud models so the user's first request is fast.
        # This is a no-op when no router is configured.
        if self._brain._llm_router is not None:
            try:
                await self._brain._llm_router.warmup()
            except Exception:  # noqa: BLE001
                logger.exception("model warmup failed")
        logger.info("azmuth started", extra={"event": "AZMUTH_STARTED"})

    async def shutdown(self) -> None:
        """Release resources. Safe to call more than once."""
        try:
            await self._plugins.shutdown()
        except Exception:  # noqa: BLE001
            logger.exception("plugin shutdown failed")
        try:
            self._memory.close()
        except Exception:  # noqa: BLE001
            logger.exception("memory close failed")
        logger.info("azmuth stopped", extra={"event": "AZMUTH_STOPPED"})

    # -- status ------------------------------------------------------------
    def status(self) -> StatusSnapshot:
        """Subsystem health for the UI status bar."""
        return StatusSnapshot(
            llm="online" if self._safe(self._llm_provider.is_available) else "offline",
            memory="online",
            voice_input=(
                "online"
                if self._safe(lambda: self._voice.is_available(require_wake_word=False))
                else "offline"
            ),
            tts="online" if self._safe(self._tts.is_available) else "offline",
            wake_word="online" if self._safe(self._wake.is_available) else "offline",
            search="online" if self._safe(self._search.is_available) else "offline",
            task_manager="online",
            security="online",
            ui="online",
        )

    @staticmethod
    def _safe(probe) -> bool:  # noqa: ANN001
        try:
            return bool(probe())
        except Exception:  # noqa: BLE001
            return False


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------
def build_azmuth(
    settings: Settings,
    *,
    event_bus: EventBus | None = None,
    principal: Principal | None = None,
    llm_provider: LLMProvider | None = None,
    memory: MemoryManager | None = None,
    sessions: SessionManager | None = None,
    tools: ToolManager | None = None,
    search: SearchManager | None = None,
    tts: TTSManager | None = None,
    voice: VoiceInputManager | None = None,
    wake: WakeWordDetector | None = None,
    stt: SpeechToTextProvider | None = None,
    tasks: TaskManager | None = None,
    permissions: PermissionManager | None = None,
    authenticator: Authenticator | None = None,
    plugin_manager: PluginManager | None = None,
) -> Azmuth:
    """Build an :class:`Azmuth` with all subsystems wired.

    Every argument is optional; omitted ones are constructed from
    ``settings``. Tests use the overrides to substitute fakes for
    specific subsystems without replacing the whole wiring.
    """
    from app.language import build_language_manager

    settings.paths.ensure()

    bus = event_bus or EventBus()
    language_manager = build_language_manager(settings)

    # ---- security --------------------------------------------------------
    perms = permissions or PermissionManager()
    auth = authenticator or Authenticator()
    if authenticator is None:
        # Register the standard methods. The PIN method is unavailable
        # until a credential is stored — which is the honest default.
        from app.security import build_default_methods
        auth.register_all(build_default_methods())

    # ---- LLM -------------------------------------------------------------
    router: LLMRouter | None = None
    if llm_provider is None:
        # Build a router over the models Azmuth knows about, then hand it
        # to the manager. The manager resolves a provider per request.
        registry = build_default_registry()
        router = LLMRouter(settings, registry)
        llm_manager = LLMManager(settings, build_llm_registry(), router=router)
        # Try to get a working provider; if the router has nothing available
        # (e.g. Ollama not running and no NVIDIA key), fall back to the
        # stub so the app still boots rather than crashing at startup.
        try:
            llm_provider = router.for_purpose(ModelPurpose.CHAT)
        except ProviderUnavailableError:
            logger.warning(
                "no LLM provider available; using stub",
                extra={"event": "LLM_FALLBACK_TO_STUB"},
            )
            llm_provider = build_llm_registry().create("stub")

    # ---- memory ----------------------------------------------------------
    mem = memory or build_memory_manager(settings)
    sess = sessions or SessionManager(settings)

    # ---- search (built first so tools can use it) ------------------------
    search_manager = search or build_search_manager(settings.search_provider)

    # ---- automation ------------------------------------------------------
    from app.automation import WindowsAutomationImpl
    automation = WindowsAutomationImpl()

    # ---- tools -----------------------------------------------------------
    tool_manager = tools or ToolManager(perms, event_bus=bus)

    # ---- plugins ---------------------------------------------------------
    plugins = plugin_manager
    if plugins is None:
        plugins, load_report = build_plugin_manager()
        if load_report.failure_count:
            logger.warning(
                "some plugins failed to load",
                extra={
                    "event": "PLUGINS_PARTIAL_LOAD",
                    "failures": load_report.failure_count,
                },
            )

    # Register builtin tools + plugin tools on the same manager.
    if tools is None:
        for tool in build_builtin_tools(
            search=search_manager, automation=automation
        ):
            tool_manager.register(tool)
        plugins.register_tools(tool_manager)

    # ---- voice -----------------------------------------------------------
    wake_detector = wake or build_wake_word_detector(settings, stt=stt)
    voice_manager = voice or build_voice_input_manager(
        language=language_manager,
        stt=stt,
        wake=wake_detector,
        event_bus=bus,
    )

    # ---- TTS -------------------------------------------------------------
    tts_manager = tts or build_tts_manager(settings)

    # ---- tasks -----------------------------------------------------------
    task_manager = tasks or TaskManager(
        settings.task_max_concurrent, event_bus=bus
    )

    # The brain uses the router for per-request model selection. When no
    # router was constructed (e.g. a test passed llm_provider directly),
    # the router is None and every request goes to the single provider.
    router_for_brain: LLMRouter | None = locals().get("router")
    # ---- identity & location --------------------------------------------
    from app.brain.context import LocationService
    from app.brain.identity import ensure_identity_dir, load_identity_block

    ensure_identity_dir(settings)  # create starter files if first run
    identity_block = load_identity_block(settings)
    location_service = LocationService()

    # ---- brain -----------------------------------------------------------
    brain = Brain(
        llm=llm_provider,
        llm_router=router,
        memory=mem,
        sessions=sess,
        language=language_manager,
        tools=tool_manager,
        search=search_manager,
        event_bus=bus,
    )
    brain.attach_identity(
        identity_block=identity_block,
        location_service=location_service,
    )

    del principal  # reserved for future use

    return Azmuth(
        settings=settings,
        event_bus=bus,
        language=language_manager,
        llm_provider=llm_provider,
        memory=mem,
        sessions=sess,
        tools=tool_manager,
        search=search_manager,
        tts=tts_manager,
        voice=voice_manager,
        wake=wake_detector,
        tasks=task_manager,
        permissions=perms,
        authenticator=auth,
        plugin_manager=plugins,
        brain=brain,
    )


def run_azmuth_blocking(settings: Settings) -> int:  # pragma: no cover
    """Convenience for scripts: run startup/shutdown around an async block."""
    azmuth = build_azmuth(settings)

    async def _main() -> None:
        await azmuth.startup()
        try:
            await asyncio.sleep(0)  # caller's work would go here
        finally:
            await azmuth.shutdown()

    asyncio.run(_main())
    return 0


__all__ = ["Azmuth", "build_azmuth"]