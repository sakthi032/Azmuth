"""Tests for Brain._recall_memory() hybrid recall wiring (Phase 1.4)."""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_brain_stub(memory_hybrid_recall: bool = True, has_recall_hybrid: bool = True):
    """Return a minimal Brain-like object with _recall_memory wired."""
    # We import and call the method directly to avoid standing up the full Brain.
    from app.brain import pipeline  # noqa: F401 — ensure module is importable
    import types

    # Build a minimal settings stub
    settings = MagicMock()
    settings.memory_hybrid_recall = memory_hybrid_recall

    # Build a minimal memory stub
    memory = MagicMock()
    if not has_recall_hybrid:
        del memory.recall_hybrid  # simulate missing method

    return settings, memory


class TestRecallMemoryHybridWiring:
    def _call_recall(self, settings, memory, text: str = "test query") -> str | None:
        """Call Brain._recall_memory via the actual method (extracted)."""
        import importlib
        import app.brain.pipeline as pipeline_mod
        import types

        # We instantiate a minimal Brain-like object to call _recall_memory.
        # Avoid full Brain construction (DI heavy).
        class _Stub:
            _settings = settings
            _memory = memory
            _memory_top_k = 5

        stub = _Stub()
        # Call the unbound method with our stub as self
        return pipeline_mod.Brain._recall_memory(stub, text, user_id="user1")  # type: ignore[attr-defined]

    def test_calls_recall_hybrid_when_enabled_and_available(self):
        settings, memory = _make_brain_stub(memory_hybrid_recall=True, has_recall_hybrid=True)
        memory.recall_hybrid.return_value = []

        self._call_recall(settings, memory)

        memory.recall_hybrid.assert_called_once()
        memory.recall.assert_not_called()

    def test_falls_back_to_recall_when_hybrid_disabled(self):
        settings, memory = _make_brain_stub(memory_hybrid_recall=False, has_recall_hybrid=True)
        memory.recall.return_value = []

        self._call_recall(settings, memory)

        memory.recall.assert_called_once()
        memory.recall_hybrid.assert_not_called()

    def test_falls_back_to_recall_when_hybrid_not_available(self):
        settings, memory = _make_brain_stub(memory_hybrid_recall=True, has_recall_hybrid=False)
        memory.recall.return_value = []

        self._call_recall(settings, memory)

        memory.recall.assert_called_once()

    def test_hybrid_results_formatted_correctly(self):
        settings, memory = _make_brain_stub(memory_hybrid_recall=True, has_recall_hybrid=True)
        record = MagicMock()
        record.kind.value = "fact"
        record.content = "User likes Python"
        memory.recall_hybrid.return_value = [record]

        result = self._call_recall(settings, memory, "what does user like")

        assert result is not None
        assert "[MEMORY]" in result
        assert "User likes Python" in result

    def test_returns_none_on_empty_results(self):
        settings, memory = _make_brain_stub(memory_hybrid_recall=True, has_recall_hybrid=True)
        memory.recall_hybrid.return_value = []

        result = self._call_recall(settings, memory)

        assert result is None

    def test_returns_none_on_exception(self):
        settings, memory = _make_brain_stub(memory_hybrid_recall=True, has_recall_hybrid=True)
        memory.recall_hybrid.side_effect = RuntimeError("DB offline")

        result = self._call_recall(settings, memory)

        assert result is None

    def test_settings_field_readable(self):
        """The new settings field exists and has the correct default."""
        from app.config.settings import load_settings
        s = load_settings()
        assert isinstance(s.memory_hybrid_recall, bool)
        assert s.memory_hybrid_recall is True  # default
