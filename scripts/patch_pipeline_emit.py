
"""Patch app/brain/pipeline.py to avoid the emit() source collision.

Run once. Idempotent - safe to run again if it fails partway through.
"""

from pathlib import Path

path = Path("app/brain/pipeline.py")
text = path.read_text(encoding="utf-8")
original = text

# Fix 1: import line - drop emit, add Event
old_import = "from app.core.events import EventBus, EventName, emit"
new_import = "from app.core.events import Event, EventBus, EventName"
if old_import in text:
    text = text.replace(old_import, new_import)
    print("patched import line")
elif new_import in text:
    print("import line already patched")
else:
    raise SystemExit("ERROR: could not find import line in pipeline.py")

# Fix 2: _emit body - build Event directly instead of calling emit()
old_emit = '''    async def _emit(self, name: EventName, **payload: Any) -> None:
        if self._event_bus is None:
            return
        try:
            await emit(self._event_bus, name, source="brain", **payload)
        except Exception:  # noqa: BLE001
            logger.exception(
                "failed to emit brain event",
                extra={"event": name.value},
            )
'''

new_emit = '''    async def _emit(self, name: EventName, **payload: Any) -> None:
        if self._event_bus is None:
            return
        try:
            # Build the Event directly rather than via the emit() helper.
            # The helper reserves ``source`` as a keyword argument for the
            # event's emitter, which collides with the pipeline's own use
            # of a ``source`` payload key (the input source: text/voice).
            await self._event_bus.publish(
                Event(name=name, source="brain", payload=payload)
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "failed to emit brain event",
                extra={"event": name.value},
            )
'''

if old_emit in text:
    text = text.replace(old_emit, new_emit)
    print("patched _emit body")
elif new_emit in text:
    print("_emit body already patched")
else:
    raise SystemExit("ERROR: could not find _emit body in pipeline.py")

if text != original:
    path.write_text(text, encoding="utf-8")
    print("wrote app/brain/pipeline.py")
else:
    print("no changes needed")
# '@ | Set-Content -Encoding utf8 scripts\patch_pipeline_emit.py

# py scripts\patch_pipeline_emit.py