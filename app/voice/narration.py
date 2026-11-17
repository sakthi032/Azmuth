"""Progress narration from tool events.

Fully automated — no hardcoded tool-name mappings. The narration phrase
is derived from each tool's own description metadata, so a brand-new
tool added tomorrow works with zero changes here.

Priority:
1. Optional curated override in ``data/narration_overrides.json``
   (only needed if you want to polish a specific tool's phrase).
2. Derived from ``ToolMetadata.description``.
3. Derived from the tool name itself (last resort).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

from app.core.events import Event, EventBus, EventName

logger = logging.getLogger(__name__)


#: Leading verbs in descriptions that are *command form* and should be
#: dropped before turning the description into a spoken phrase. These
#: are grammatical rules about English imperatives, not a tool list —
#: they apply to every current and future tool.
_IMPERATIVE_PREFIXES = (
    "Get the ", "Get ", "Fetch the ", "Fetch ",
    "Return the ", "Return ", "Retrieve the ", "Retrieve ",
    "Find the ", "Find ", "Look up the ", "Look up ",
    "Query the ", "Query ", "Read the ", "Read ",
    "List the ", "List ", "Open the ", "Open ",
    "Launch the ", "Launch ", "Close the ", "Close ",
    "Click the ", "Click ", "Type the ", "Type ",
    "Press the ", "Press ", "Set the ", "Set ",
    "Create the ", "Create ", "Delete the ", "Delete ",
    "Move the ", "Move ", "Copy the ", "Copy ",
    "Wait for the ", "Wait for ", "Verify the ", "Verify ",
    "Focus the ", "Focus ", "Search for ", "Search ",
)

#: Word-level noise that adds length but not meaning when spoken.
_NOISE_WORDS = frozenset({
    "the", "a", "an", "or", "and", "for", "from", "of", "in", "to",
    "with", "using", "via", "optionally", "please",
})

#: First-word truncation target for the derived phrase.
_MAX_PHRASE_WORDS = 6


class Narrator:
    """Turn tool events into short spoken progress messages."""

    def __init__(
        self,
        queue,
        *,
        tool_lookup: Callable[[str], object] | None = None,
        overrides_path: Path | None = None,
        min_interval_seconds: float = 5.0,
        max_progress_per_turn: int = 2,
    ) -> None:
        """
        ``tool_lookup`` — callable that takes a tool name and returns the
        ``ToolMetadata`` (or None). Used to read the tool's own
        description so we can derive a phrase from it. If not provided,
        phrases are derived from the tool name alone.
        """
        self._queue = queue
        self._tool_lookup = tool_lookup
        self._min_interval = min_interval_seconds
        self._max_progress = max_progress_per_turn

        # Optional curated overrides — keyed by tool name. Missing
        # entries fall back to automated derivation.
        self._overrides: dict[str, str] = {}
        if overrides_path and Path(overrides_path).is_file():
            try:
                self._overrides = json.loads(
                    Path(overrides_path).read_text(encoding="utf-8")
                )
                logger.info(
                    "narration overrides loaded",
                    extra={"event": "NARRATION_OVERRIDES",
                           "count": len(self._overrides)},
                )
            except Exception:
                logger.exception(
                    "failed to load narration overrides",
                    extra={"event": "NARRATION_OVERRIDES_FAILED"},
                )

        # Per-turn state.
        self._turn_tools_seen = 0
        self._turn_progress_sent = 0
        self._last_spoken_at = 0.0

        self._enabled: Callable[[], bool] = lambda: False
        self._unsubs: list[Callable[[], None]] = []

    # -- wiring ------------------------------------------------------------
    def set_enabled_callback(self, cb: Callable[[], bool]) -> None:
        self._enabled = cb

    def subscribe(self, bus: EventBus) -> None:
        self._unsubs.append(
            bus.subscribe(EventName.USER_INPUT, self._on_user_input)
        )
        self._unsubs.append(
            bus.subscribe(EventName.TOOL_STARTED, self._on_tool_started)
        )
        logger.info(
            "narrator subscribed",
            extra={"event": "NARRATOR_SUBSCRIBED"},
        )

    def unsubscribe(self) -> None:
        for fn in self._unsubs:
            try:
                fn()
            except Exception:
                pass
        self._unsubs.clear()

    # -- handlers ----------------------------------------------------------
    async def _on_user_input(self, event: Event) -> None:
        self._turn_tools_seen = 0
        self._turn_progress_sent = 0

    async def _on_tool_started(self, event: Event) -> None:
        if not self._enabled():
            return
        tool = str(event.payload.get("tool", "")).strip()
        if not tool:
            return

        phrase = self._phrase_for(tool)
        if not phrase:
            # Nothing sensible to say — stay silent rather than lie.
            return

        self._turn_tools_seen += 1

        if self._turn_tools_seen == 1:
            self._last_spoken_at = time.monotonic()
            self._turn_progress_sent = 1
            await self._queue.enqueue(f"Let me {phrase}.")
            return

        if self._turn_progress_sent >= self._max_progress:
            return
        now = time.monotonic()
        if now - self._last_spoken_at < self._min_interval:
            return
        self._last_spoken_at = now
        self._turn_progress_sent += 1
        await self._queue.enqueue(f"I'm {phrase}.")

    # -- phrase derivation -------------------------------------------------
    def _phrase_for(self, tool_name: str) -> str:
        """Return a spoken phrase for ``tool_name``, or '' to skip."""
        # 1. Curated override
        override = self._overrides.get(tool_name, "").strip()
        if override:
            return override

        # 2. Description-derived
        description = self._get_description(tool_name)
        if description:
            derived = self._derive_from_description(description)
            if derived:
                return derived

        # 3. Name-derived (last resort)
        return self._derive_from_name(tool_name)

    def _get_description(self, tool_name: str) -> str:
        if self._tool_lookup is None:
            return ""
        try:
            meta = self._tool_lookup(tool_name)
        except Exception:
            return ""
        if meta is None:
            return ""
        # ToolMetadata has a .description attribute
        return getattr(meta, "description", "") or ""

    @staticmethod
    def _derive_from_description(description: str) -> str:
        """Turn a tool's description into a short speakable phrase.

        The description is already natural language. We strip the
        imperative prefix ("Get the ..."), drop noise words at the ends,
        and trim to ~6 meaningful words.
        """
        # Take the first sentence only.
        sentence = description.split(".")[0].strip()

        # Drop the imperative leading verb.
        for prefix in _IMPERATIVE_PREFIXES:
            if sentence.lower().startswith(prefix.lower()):
                sentence = sentence[len(prefix):]
                break

        # Tokenise.
        words = re.findall(r"[A-Za-z0-9]+", sentence.lower())
        if not words:
            return ""

        # Trim trailing noise words but keep meaningful words inside.
        while words and words[-1] in _NOISE_WORDS:
            words.pop()

        # Cap length.
        if len(words) > _MAX_PHRASE_WORDS:
            words = words[:_MAX_PHRASE_WORDS]

        # Drop trailing noise again in case the cap left one.
        while words and words[-1] in _NOISE_WORDS:
            words.pop()

        return " ".join(words)

    @staticmethod
    def _derive_from_name(tool_name: str) -> str:
        """Fall back to the tool name with underscores removed.

        'search_web' → 'search web'
        'chrome_ext_list_tabs' → 'chrome ext list tabs'
        """
        words = [w for w in tool_name.split("_") if w]
        # Drop trailing noise.
        while words and words[-1].lower() in _NOISE_WORDS:
            words.pop()
        return " ".join(w.lower() for w in words)


__all__ = ["Narrator"]