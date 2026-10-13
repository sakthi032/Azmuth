
"""The top-level facade.

Owns one instance of every subsystem, wires them, and exposes:

* :meth:`Azmuth.respond` - the pipeline entry point used by the UI.
* :meth:`Azmuth.status` - subsystem health for the status bar.
* :meth:`Azmuth.shutdown` - best-effort release of resources.

Construction is via :func:`build_azmuth`. Every dependency is a keyword
argument so tests can substitute any subsystem without subclassing.
"""

from __future__ import annotations

import asyncio

from app.brain.pipeline import Brain, BrainResponse, Confirmer
from app.config.logging_setup import get_logger
from app.config.settings import Settings
from app.core.events import EventBus
from app.llm import LLMManager, LLMRouter, ModelPurpose, build_default_registry, build_llm_registry
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



logger = get_logger("brain.azmuth")


class Azmuth:
    """The whole assistant, as one object."""

    def __init__(
        self,
        *,
        settings: Settings,
        event_bus: EventBus,
        language,
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
    def language(self):
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
        confirmer: Confirmer | None = None,
    ) -> BrainResponse:
        """Process one user turn."""
        return await self._brain.respond(
            user_input,
            conversation_id=conversation_id,
            principal=principal,
            confirmer=confirmer,
        )

    async def respond_as_text(
        self,
        user_input: UserInput,
        conversation_id: str | None = None,
        confirmer: Confirmer | None = None,
        *,
        principal: Principal | None = None,
    ) -> tuple[str, str, str, str]:
        """UI responder: return ``(reply_text, session_id)``.

        ``confirmer`` is positional-or-keyword so the UI's bridge can pass
        it positionally. When omitted, dangerous tools with a
        confirmation requirement are refused by the pipeline.
        """
        if principal is None:
            principal = self._authenticator.current_principal
        response = await self.respond(
            user_input,
            conversation_id=conversation_id,
            principal=principal,
            confirmer=confirmer,
        )
        return response.text, response.session_id, response.model, response.provider

    # -- lifecycle ---------------------------------------------------------
    async def startup(self) -> None:
        if self._started:
            return
        self._started = True
        await self._plugins.startup()

        # Start the Chrome extension bridge. Best-effort.
        try:
            from app.automation.chrome_extension_bridge import get_bridge
            get_bridge()
        except Exception:
            logger.exception("chrome extension bridge failed to start")

        if self._brain._llm_router is not None:
            try:
                await self._brain._llm_router.warmup()
            except Exception:
                logger.exception("model warmup failed")
        logger.info("azmuth started", extra={"event": "AZMUTH_STARTED"})

    async def shutdown(self) -> None:
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
    def _safe(probe) -> bool:
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
    """Build an :class:`Azmuth` with all subsystems wired."""
    from app.language import build_language_manager

    settings.paths.ensure()

    bus = event_bus or EventBus()
    language_manager = build_language_manager(settings)

    # ---- security --------------------------------------------------------
    perms = permissions or PermissionManager()
    auth = authenticator or Authenticator()
    if authenticator is None:
        from app.security import build_default_methods
        auth.register_all(build_default_methods())

    # ---- LLM -------------------------------------------------------------
    router: LLMRouter | None = None
    if llm_provider is None:
        from app.core.errors import ProviderUnavailableError
        registry = build_default_registry()
        router = LLMRouter(settings, registry)
        llm_manager = LLMManager(settings, build_llm_registry(), router=router)
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

    # ---- search ----------------------------------------------------------
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
    if tools is None:
        from app.automation.input import InputController
        from app.automation.settings import SettingsController
        from app.automation.uia import UIAutomationController

        from app.automation.files import FileController
        from app.automation.policy import PathPolicy

        file_policy = PathPolicy.from_paths(settings.file_sandbox_roots)
        file_controller = FileController(file_policy)

        for tool in build_builtin_tools(
            search=search_manager,
            automation=automation,
            settings=SettingsController(),
            uia=UIAutomationController(),
            input_ctl=InputController(),
            files=file_controller,
            include_browsers=True,
            include_extension=True,
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

    # ---- identity & location --------------------------------------------
    from app.brain.context import LocationService
    from app.brain.identity import ensure_identity_dir, load_identity_block

    ensure_identity_dir(settings)
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

    del principal
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
    azmuth = build_azmuth(settings)

    async def _main() -> None:
        await azmuth.startup()
        try:
            await asyncio.sleep(0)
        finally:
            await azmuth.shutdown()

    asyncio.run(_main())
    return 0


__all__ = ["Azmuth", "build_azmuth"]
