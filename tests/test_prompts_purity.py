"""Lint the system prompt template for duplicate rules."""

from __future__ import annotations

from app.brain.prompts import SYSTEM_PROMPT_TEMPLATE


def test_no_duplicate_tool_errors_rule():
    """The 'Tool errors are facts' rule must appear once."""
    needle = "**Tool errors are facts.**"
    count = SYSTEM_PROMPT_TEMPLATE.count(needle)
    assert count == 1, (
        f"'Tool errors are facts' appears {count} times — should be 1."
    )


def test_prefer_dedicated_section_is_well_formed():
    """The dedicated-tools section must contain the table, the
    exception, and the weather rules in one contiguous block."""
    anchor = "### Prefer dedicated tools over search"
    end = "## When search succeeds, use the results"
    start = SYSTEM_PROMPT_TEMPLATE.find(anchor)
    stop = SYSTEM_PROMPT_TEMPLATE.find(end)
    assert start >= 0 and stop > start
    section = SYSTEM_PROMPT_TEMPLATE[start:stop]
    assert "get_gold_rate" in section
    assert "get_weather" in section
    assert "Exception — explicit search intent" in section
    assert "For weather questions:" in section
    # Must not be truncated mid-sentence
    assert "The dedicated tools return verified numbers" in section
    assert "prone to parsing errors" in section