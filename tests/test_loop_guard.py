"""Tests for LoopGuard infinite loop detection and context compression."""

import pytest
from app.brain.loop_guard import LoopGuard, LoopGuardConfig, LoopDetectedError


def test_loop_guard_identical_calls():
    """Verify that calling the same tool with identical arguments raises LoopDetectedError."""
    guard = LoopGuard(LoopGuardConfig(max_identical=3))
    
    # First 2 calls should succeed
    guard.check("search_web", {"query": "weather in tokyo"})
    guard.check("search_web", {"query": "weather in tokyo"})
    
    # 3rd identical call must raise LoopDetectedError
    with pytest.raises(LoopDetectedError) as exc_info:
        guard.check("search_web", {"query": "weather in tokyo"})
    
    assert "search_web" in str(exc_info.value)
    assert "consecutive" in str(exc_info.value).lower() or "aborting" in str(exc_info.value).lower()


def test_loop_guard_different_arguments_ok():
    """Different arguments for the same tool do not trigger identical call block."""
    guard = LoopGuard(LoopGuardConfig(max_identical=3))
    guard.check("search_web", {"query": "apple"})
    guard.check("search_web", {"query": "banana"})
    guard.check("search_web", {"query": "cherry"})
    guard.check("search_web", {"query": "date"})
    assert guard.total_calls == 4


def test_loop_guard_ping_pong_detection():
    """A-B-A-B cycle detection across the sliding window."""
    guard = LoopGuard(LoopGuardConfig(pingpong_window=6))
    
    # A B A B A
    guard.check("tool_a", {"x": 1})
    guard.check("tool_b", {"y": 2})
    guard.check("tool_a", {"x": 1})
    guard.check("tool_b", {"y": 2})
    guard.check("tool_a", {"x": 1})
    
    # 6th call completing A-B-A-B-A-B must raise LoopDetectedError
    with pytest.raises(LoopDetectedError) as exc_info:
        guard.check("tool_b", {"y": 2})
    
    assert "ping-pong" in str(exc_info.value).lower() or "loop detected" in str(exc_info.value).lower()


def test_loop_guard_budget_limit():
    """Verify hard tool budget ceiling."""
    guard = LoopGuard(LoopGuardConfig(max_tool_budget=5))
    for i in range(5):
        guard.check(f"tool_{i}", {"step": i})
    assert guard.total_calls == 5
    assert guard.remaining_budget == 0

    with pytest.raises(LoopDetectedError) as exc_info:
        guard.check("tool_overflow", {})
    assert "budget" in str(exc_info.value).lower()


def test_loop_guard_context_compression():
    """Context compression preserves system message and keeps recent turns."""
    guard = LoopGuard(LoopGuardConfig(context_max_messages=10, context_keep_recent=4))
    
    messages = [{"role": "system", "content": "You are Azmuth."}]
    for i in range(15):
        messages.append({"role": "user", "content": f"query {i}"})
        messages.append({"role": "assistant", "content": f"answer {i}"})
    
    assert len(messages) == 31
    compressed = guard.maybe_compress(messages)
    assert compressed is True
    # 1 system message + 4 recent messages = 5
    assert len(messages) == 5
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are Azmuth."
    assert messages[-1]["content"] == "answer 14"


def test_loop_guard_no_compression_needed():
    """If messages <= context_max_messages, maybe_compress returns False."""
    guard = LoopGuard(LoopGuardConfig(context_max_messages=10))
    messages = [
        {"role": "system", "content": "System"},
        {"role": "user", "content": "Hello"},
    ]
    assert guard.maybe_compress(messages) is False
    assert len(messages) == 2


def test_loop_guard_reset():
    """Reset clears history and budgets."""
    guard = LoopGuard(LoopGuardConfig(max_tool_budget=10))
    guard.check("tool_1", {"arg": "val"})
    assert guard.total_calls == 1
    guard.reset()
    assert guard.total_calls == 0
    assert guard.remaining_budget == 10


@pytest.mark.asyncio
async def test_loop_guard_pipeline_integration(make_azmuth):
    """Pipeline aborts repeated tool call loop and sets stopped_reason='loop_detected'."""
    from app.voice import InputManager
    
    repeated_call = '```tool\n{"name": "get_time", "arguments": {}}\n```'
    # LLM repeatedly issues the same tool call 4 times
    azmuth = make_azmuth(responses=[repeated_call, repeated_call, repeated_call, repeated_call])
    inputs = InputManager(azmuth.language)

    response = await azmuth.respond(inputs.from_text("what time is it"))
    assert response.stopped_reason == "loop_detected"
    assert "LoopGuard" in response.text

