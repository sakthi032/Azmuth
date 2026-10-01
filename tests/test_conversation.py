from __future__ import annotations

from datetime import timedelta

import pytest

from app.config.settings import Settings, load_settings
from app.core.errors import SessionNotFoundError
from app.language import build_language_manager
from app.llm.messages import Role
from app.memory import ContextBuilder, Session, SessionManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def settings(tmp_path) -> Settings:
    return load_settings(
        env_file=tmp_path / "missing.env",
        environ={
            "AZMUTH_HOME": str(tmp_path),
            "AZMUTH_LOG_LEVEL": "WARNING",
            "AZMUTH_LOG_TO_CONSOLE": "false",
            "AZMUTH_LOG_TO_FILE": "false",
            "SESSION_MAX_TURNS": "3",
            "SESSION_TTL_MINUTES": "5",
        },
        create_directories=False,
    )


@pytest.fixture()
def manager(settings: Settings) -> SessionManager:
    return SessionManager(settings)


@pytest.fixture()
def builder() -> ContextBuilder:
    return ContextBuilder()


# ---------------------------------------------------------------------------
# Session — basic behaviour
# ---------------------------------------------------------------------------
def test_session_starts_empty_with_defaults() -> None:
    s = Session(id="s1")
    assert s.messages == []
    assert s.user_id is None
    assert s.language is None
    assert s.metadata == {}
    assert s.user_turn_count() == 0


def test_add_messages_appends_and_touches() -> None:
    s = Session(id="s1")
    before = s.last_activity

    u = s.add_user_message("hi")
    a = s.add_assistant_message("hello")

    assert u.role is Role.USER and u.content == "hi"
    assert a.role is Role.ASSISTANT and a.content == "hello"
    assert s.user_turn_count() == 1
    assert s.last_activity >= before


def test_add_message_with_explicit_role() -> None:
    s = Session(id="s1")
    msg = s.add_message(Role.SYSTEM, "you are azmuth")
    assert msg.role is Role.SYSTEM


def test_clear_removes_messages_but_keeps_identity() -> None:
    s = Session(id="s1", user_id="u", language="ta")
    s.add_user_message("hi")
    s.add_assistant_message("hello")

    s.clear()

    assert s.messages == []
    assert s.id == "s1"
    assert s.user_id == "u"
    assert s.language == "ta"


# ---------------------------------------------------------------------------
# Session — recent_messages
# ---------------------------------------------------------------------------
def _populate(s: Session, pairs: int) -> None:
    for i in range(pairs):
        s.add_user_message(f"u{i}")
        s.add_assistant_message(f"a{i}")


def test_recent_messages_within_limit_returns_all() -> None:
    s = Session(id="s1", max_turns=10)
    _populate(s, 3)

    result = s.recent_messages()

    assert len(result) == 6
    assert result[0].content == "u0"
    assert result[-1].content == "a2"


def test_recent_messages_trims_to_last_n_user_turns() -> None:
    s = Session(id="s1", max_turns=2)
    _populate(s, 5)  # u0,a0 .. u4,a4

    result = s.recent_messages()

    # Last 2 user turns = u3,a3,u4,a4
    assert [m.content for m in result] == ["u3", "a3", "u4", "a4"]


def test_recent_messages_override_wins() -> None:
    s = Session(id="s1", max_turns=5)
    _populate(s, 5)

    result = s.recent_messages(max_turns=1)

    assert [m.content for m in result] == ["u4", "a4"]


@pytest.mark.parametrize("limit", [0, -1])
def test_recent_messages_zero_or_negative_returns_empty(limit: int) -> None:
    s = Session(id="s1")
    _populate(s, 2)
    assert s.recent_messages(max_turns=limit) == ()


def test_recent_messages_empty_session() -> None:
    assert Session(id="s1").recent_messages() == ()


def test_recent_messages_handles_unpaired_assistant() -> None:
    # Latest user message has no reply yet (typical mid-request state).
    s = Session(id="s1", max_turns=2)
    s.add_user_message("u0")
    s.add_assistant_message("a0")
    s.add_user_message("u1")  # no assistant yet

    result = s.recent_messages()

    assert [m.content for m in result] == ["u0", "a0", "u1"]


# ---------------------------------------------------------------------------
# Session — history limit
# ---------------------------------------------------------------------------
def test_history_limit_evicts_oldest_messages() -> None:
    s = Session(id="s1", history_limit=4)
    _populate(s, 5)  # would be 10 messages

    assert len(s.messages) == 4
    # Oldest dropped, newest kept.
    assert [m.content for m in s.messages] == ["u3", "a3", "u4", "a4"]


def test_history_limit_zero_disables_eviction() -> None:
    s = Session(id="s1", history_limit=0)
    _populate(s, 20)
    assert len(s.messages) == 40


# ---------------------------------------------------------------------------
# Session — expiry
# ---------------------------------------------------------------------------
def test_session_not_expired_within_ttl() -> None:
    s = Session(id="s1")
    assert s.is_expired(timedelta(minutes=5)) is False


def test_session_expires_after_ttl() -> None:
    s = Session(id="s1")
    s.last_activity -= timedelta(minutes=10)
    assert s.is_expired(timedelta(minutes=5)) is True


# ---------------------------------------------------------------------------
# SessionManager — creation & lookup
# ---------------------------------------------------------------------------
def test_create_assigns_unique_id_and_uses_settings(manager: SessionManager) -> None:
    a = manager.create()
    b = manager.create()

    assert a.id != b.id
    assert len(a.id) >= 12  # token_urlsafe(12) → 16 chars
    assert a.max_turns == 3  # from SESSION_MAX_TURNS
    assert a.history_limit >= 40


def test_create_respects_explicit_fields(manager: SessionManager) -> None:
    s = manager.create(user_id="creator", language="ta", metadata={"k": "v"})
    assert s.user_id == "creator"
    assert s.language == "ta"
    assert s.metadata == {"k": "v"}


def test_create_with_duplicate_id_rejected(manager: SessionManager) -> None:
    manager.create(session_id="dup")
    with pytest.raises(ValueError, match="already exists"):
        manager.create(session_id="dup")


def test_get_unknown_raises(manager: SessionManager) -> None:
    with pytest.raises(SessionNotFoundError, match="not found"):
        manager.get("nope")


def test_get_expired_purges_and_raises(manager: SessionManager) -> None:
    s = manager.create(session_id="old")
    s.last_activity -= timedelta(minutes=10)

    with pytest.raises(SessionNotFoundError, match="expired"):
        manager.get("old")

    # Side effect: gone.
    assert "old" not in manager.list_ids()


def test_get_or_create_existing_returns_same(manager: SessionManager) -> None:
    created = manager.create(session_id="keep", user_id="u")
    retrieved = manager.get_or_create("keep")
    assert retrieved is created


def test_get_or_create_new_with_explicit_id(manager: SessionManager) -> None:
    s = manager.get_or_create("brand-new", user_id="u", language="ta")
    assert s.id == "brand-new"
    assert s.user_id == "u"
    assert s.language == "ta"
    assert manager.count() == 1


def test_get_or_create_without_id_generates_one(manager: SessionManager) -> None:
    s = manager.get_or_create()
    assert manager.count() == 1
    assert manager.get(s.id) is s


# ---------------------------------------------------------------------------
# SessionManager — deletion, listing, purge
# ---------------------------------------------------------------------------
def test_delete_returns_true_then_false(manager: SessionManager) -> None:
    manager.create(session_id="x")
    assert manager.delete("x") is True
    assert manager.delete("x") is False


def test_list_ids_and_count(manager: SessionManager) -> None:
    manager.create(session_id="a")
    manager.create(session_id="b")
    assert set(manager.list_ids()) == {"a", "b"}
    assert manager.count() == 2


def test_sessions_returns_snapshot(manager: SessionManager) -> None:
    manager.create(session_id="a")
    snapshot = manager.sessions()
    manager.create(session_id="b")
    assert len(snapshot) == 1  # snapshot is not affected by later creates


def test_purge_expired_removes_only_expired(manager: SessionManager) -> None:
    fresh = manager.create(session_id="fresh")
    stale = manager.create(session_id="stale")
    stale.last_activity -= timedelta(minutes=10)

    purged = manager.purge_expired()

    assert purged == 1
    assert manager.list_ids() == ("fresh",)


def test_clear_removes_everything(manager: SessionManager) -> None:
    manager.create(session_id="a")
    manager.create(session_id="b")
    manager.clear()
    assert manager.count() == 0


# ---------------------------------------------------------------------------
# ContextBuilder
# ---------------------------------------------------------------------------
def test_builder_returns_only_history_when_no_extras() -> None:
    s = Session(id="s1", max_turns=2)
    _populate(s, 3)

    messages = ContextBuilder().build(s)

    assert [m.content for m in messages] == ["u1", "a1", "u2", "a2"]


def test_builder_prepends_system_prompt() -> None:
    s = Session(id="s1")
    s.add_user_message("hi")

    messages = ContextBuilder().build(s, system_prompt="You are Azmuth.")

    assert messages[0].role is Role.SYSTEM
    assert messages[0].content == "You are Azmuth."
    assert messages[1].content == "hi"


def test_builder_labels_retrieval_context_as_reference() -> None:
    s = Session(id="s1")
    s.add_user_message("hi")

    messages = ContextBuilder().build(
        s, retrieval_context="Chrome was last opened at 10:00."
    )

    assert messages[0].role is Role.SYSTEM
    assert messages[0].content.startswith("[reference]")
    assert "Chrome was last opened at 10:00." in messages[0].content


def test_builder_keeps_system_and_reference_separate() -> None:
    s = Session(id="s1")
    s.add_user_message("hi")

    messages = ContextBuilder().build(
        s,
        system_prompt="app rules",
        retrieval_context="external data",
    )

    assert messages[0].role is Role.SYSTEM and messages[0].content == "app rules"
    assert messages[1].role is Role.SYSTEM and messages[1].content.startswith("[reference]")
    assert messages[2].content == "hi"


def test_builder_override_of_max_turns_wins_over_session() -> None:
    s = Session(id="s1", max_turns=10)
    _populate(s, 3)

    messages = ContextBuilder().build(s, max_turns=1)

    assert [m.content for m in messages] == ["u2", "a2"]


def test_builder_empty_session_with_system_prompt_only() -> None:
    messages = ContextBuilder().build(Session(id="s1"), system_prompt="rules")
    assert [m.role for m in messages] == [Role.SYSTEM]


# ---------------------------------------------------------------------------
# Integration: session + language + context
# ---------------------------------------------------------------------------
def test_session_language_flows_into_context(settings: Settings) -> None:
    lang = build_language_manager(settings)
    sessions = SessionManager(settings)
    s = sessions.create()

    # User's first message establishes Tamil in this session.
    first = "இன்று வானிலை என்ன?"
    s.language = lang.resolve_response_language(first, session_language=s.language)
    s.add_user_message(first)
    s.add_assistant_message("இன்று வெயில் அதிகம்.")

    # Next message is Tanglish; the resolved language should stay Tamil.
    second = "Chrome open pannu"
    s.language = lang.resolve_response_language(second, session_language=s.language)
    s.add_user_message(second)

    messages = ContextBuilder().build(
        s, system_prompt="You are Azmuth, reply in the session language."
    )

    assert s.language == "ta"
    assert messages[0].role is Role.SYSTEM
    assert messages[1].content == first
    assert messages[-1].content == second