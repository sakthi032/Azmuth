"""SYSTEM_PROMPT_TEMPLATE must render without KeyError.

Any unescaped {field} in the template will fail here instead of
crashing the app on the first turn.
"""

from __future__ import annotations

import re

import pytest

from app.brain.prompts import SYSTEM_PROMPT_TEMPLATE, build_system_prompt


def test_format_strictly_renders():
    """The template must .format() cleanly with the four placeholders
    build_system_prompt supplies."""
    rendered = SYSTEM_PROMPT_TEMPLATE.format(
        identity_block="IDENTITY",
        context_block="CONTEXT",
        language_name="English",
        tool_list="(none)",
    )
    assert "IDENTITY" in rendered
    assert "CONTEXT" in rendered
    assert "English" in rendered
    assert "(none)" in rendered


def test_build_system_prompt_happy_path():
    prompt = build_system_prompt(language="en", tools=())
    assert "Azmuth" in prompt
    assert "Available tools" in prompt


def test_build_system_prompt_tamil():
    prompt = build_system_prompt(language="ta", tools=())
    assert "தமிழ்" in prompt


def test_no_unexpected_format_fields():
    """Every {field} in the template must be one of the four known
    placeholders. A literal brace must be escaped {{ or }}."""
    known = {"identity_block", "context_block", "language_name", "tool_list"}
    raw_fields = re.findall(
        r"(?<!\{)\{([^{}]+)\}(?!\})", SYSTEM_PROMPT_TEMPLATE
    )
    field_names = {f.split(":", 1)[0].strip() for f in raw_fields}
    unexpected = field_names - known
    assert not unexpected, (
        f"Unescaped format fields not supplied by build_system_prompt: "
        f"{sorted(unexpected)}. Escape them as {{{{ and }}}}."
    )

def test_search_intent_overrides_dedicated_tools():
    """Reproduces bug 8: 'search the web for gold price news' called
    get_gold_rate instead of search_web. Prompt must carve out an
    exception when the user explicitly says search/find/news."""
    prompt = build_system_prompt(language="en", tools=())
    lower = prompt.lower()
    # Must contain an exception clause near the dedicated-tools rule
    # ("if the user says search...")
    anchor = lower.find("prefer dedicated tools over search")
    assert anchor >= 0, "prefer-dedicated section missing"
    region = lower[anchor:anchor + 800]
    assert (
        "search" in region
        and ("exception" in region or "override" in region or "instead" in region)
    ), (
        "Prefer-dedicated-tools section must carve out an exception for "
        "explicit search/news/find requests."
    )
