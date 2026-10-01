"""Tests for purpose detection."""

from __future__ import annotations

import pytest

from app.brain import detect_purpose
from app.llm.registry import ModelPurpose


@pytest.mark.parametrize(
    "text",
    [
        "Write a Python function that reverses a string.",
        "Fix this error: TypeError: 'NoneType' object is not subscriptable",
        "```\nprint('hello')\n```",
        "How do I write a regex to match emails?",
        "Debug this code",
        "Implement quicksort in Rust",
        "what is the time complexity of binary search",
        "code pannu",
        "function ezhuthu",
    ],
)
def test_detects_coding(text: str) -> None:
    assert detect_purpose(text) is ModelPurpose.CODING


@pytest.mark.parametrize(
    "text",
    [
        "Why does ice float on water?",
        "Explain step by step how a transformer works",
        "Prove that the square root of 2 is irrational",
        "Compare and contrast TCP and UDP",
        "What are the trade-offs of microservices?",
        "Solve for x: 2x + 5 = 13",
        "Analyze the causes of World War I",
        "explain pannu",
    ],
)
def test_detects_reasoning(text: str) -> None:
    assert detect_purpose(text) is ModelPurpose.REASONING


@pytest.mark.parametrize(
    "text",
    [
        "What is the capital of France?",
        "Who is the current president of India?",
        "When is Diwali this year?",
        "quick question: what time is it in Tokyo?",
        "briefly: what is HTTP?",
    ],
)
def test_detects_fast(text: str) -> None:
    assert detect_purpose(text) is ModelPurpose.FAST


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "Hello, how are you?",
        "Tell me a story about a dragon.",
        "What's the weather like?",
        "I'm feeling tired today.",
    ],
)
def test_defaults_to_chat(text: str) -> None:
    assert detect_purpose(text) is ModelPurpose.CHAT

@pytest.mark.parametrize(
    "text",
    [
        "2 + 2",
        "10 * 7",
        "100 / 4",
        "50 - 8",
        "What is 2 + 2?",
    ],
)
def test_bare_arithmetic_is_fast(text: str) -> None:
    assert detect_purpose(text) is ModelPurpose.FAST
    
    
def test_coding_beats_reasoning() -> None:
    """A coding question that also asks 'why' is still coding."""
    text = "Why does this Python function return None? ```def f(): pass```"
    assert detect_purpose(text) is ModelPurpose.CODING


def test_reasoning_beats_fast() -> None:
    """A short reasoning question is reasoning, not fast."""
    text = "Why is the sky blue?"
    assert detect_purpose(text) is ModelPurpose.REASONING


def test_language_name_alone_is_not_coding() -> None:
    """Mentioning 'python' in a general question is not coding."""
    assert detect_purpose("Tell me about the python snake.") is ModelPurpose.CHAT


def test_short_question_without_starter_is_chat() -> None:
    """A short message that isn't a simple lookup falls through to chat."""
    assert detect_purpose("Hello there!") is ModelPurpose.CHAT


def test_long_message_is_not_fast() -> None:
    """Length disqualifies FAST even with a fast phrase."""
    text = "quick question: " + ("This is a long message. " * 30)
    assert detect_purpose(text) is ModelPurpose.CHAT