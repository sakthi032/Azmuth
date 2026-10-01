from __future__ import annotations

import asyncio

from app.core.events import Event, EventBus, EventName, emit


def test_publish_invokes_sync_handler(event_bus: EventBus) -> None:
    seen: list[Event] = []
    event_bus.subscribe(EventName.USER_INPUT, seen.append)

    asyncio.run(event_bus.publish(Event(EventName.USER_INPUT, {"text": "hi"})))

    assert len(seen) == 1
    assert seen[0].payload["text"] == "hi"


def test_publish_awaits_async_handler(event_bus: EventBus) -> None:
    seen: list[Event] = []

    async def handler(event: Event) -> None:
        await asyncio.sleep(0)
        seen.append(event)

    event_bus.subscribe(EventName.USER_INPUT, handler)
    asyncio.run(event_bus.publish(Event(EventName.USER_INPUT)))

    assert len(seen) == 1


def test_handlers_run_in_registration_order(event_bus: EventBus) -> None:
    order: list[int] = []
    for i in range(3):
        event_bus.subscribe(EventName.MODE_CHANGED, lambda _e, i=i: order.append(i))

    asyncio.run(event_bus.publish(Event(EventName.MODE_CHANGED)))

    assert order == [0, 1, 2]


def test_wildcard_handler_receives_all_events(event_bus: EventBus) -> None:
    seen: list[EventName] = []
    event_bus.subscribe_all(lambda e: seen.append(e.name))

    asyncio.run(event_bus.publish(Event(EventName.TOOL_STARTED)))
    asyncio.run(event_bus.publish(Event(EventName.SEARCH_COMPLETED)))

    assert seen == [EventName.TOOL_STARTED, EventName.SEARCH_COMPLETED]


def test_failing_handler_does_not_block_others(event_bus: EventBus) -> None:
    ok: list[Event] = []

    def boom(_event: Event) -> None:
        raise RuntimeError("boom")

    event_bus.subscribe(EventName.TASK_STARTED, boom)
    event_bus.subscribe(EventName.TASK_STARTED, ok.append)

    asyncio.run(event_bus.publish(Event(EventName.TASK_STARTED)))

    assert len(ok) == 1


def test_unsubscribe_stops_delivery(event_bus: EventBus) -> None:
    seen: list[Event] = []
    unsubscribe = event_bus.subscribe(EventName.USER_INPUT, seen.append)

    asyncio.run(event_bus.publish(Event(EventName.USER_INPUT)))
    unsubscribe()
    unsubscribe()  # must be idempotent
    asyncio.run(event_bus.publish(Event(EventName.USER_INPUT)))

    assert len(seen) == 1


def test_handler_can_unsubscribe_during_dispatch(event_bus: EventBus) -> None:
    seen: list[Event] = []

    def once(event: Event) -> None:
        seen.append(event)
        unsubscribe()

    unsubscribe = event_bus.subscribe(EventName.MODE_CHANGED, once)

    asyncio.run(event_bus.publish(Event(EventName.MODE_CHANGED)))
    asyncio.run(event_bus.publish(Event(EventName.MODE_CHANGED)))

    assert len(seen) == 1


def test_event_name_serialises_as_string() -> None:
    assert EventName.USER_INPUT == "USER_INPUT"
    assert EventName.USER_INPUT.value == "USER_INPUT"


def test_emit_helper_builds_payload(event_bus: EventBus) -> None:
    seen: list[Event] = []
    event_bus.subscribe(EventName.SYSTEM_ERROR, seen.append)

    asyncio.run(emit(event_bus, EventName.SYSTEM_ERROR, source="test", reason="x"))

    assert seen[0].source == "test"
    assert dict(seen[0].payload) == {"reason": "x"}