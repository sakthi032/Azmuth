
"""Detect the purpose of a user message so the router can pick a model.

A heuristic, and honest about it. The signal comes from four places:

1. Code fences (```...```) and code-shaped tokens (``def ``, ``const ``,
   ``import ``, ``=>``).
2. Programming language names in the text.
3. Language-agnostic intent: a coding verb ("write", "implement",
   "debug") combined with a coding noun ("function", "script") — with or
   without a language name.
4. Short, simple interrogatives ("what is", "who is") are FAST; anything
   longer falls through to CHAT.

Priority order when multiple signals fire:

    CODING > REASONING > FAST > CHAT

That ordering reflects what is *most specific*. A message containing
code is a coding task even if it also asks "why". A long analytical
question is reasoning even if it is short-ish. Only when nothing more
specific is detected does FAST apply for short messages, or CHAT as the
fallback.

The detector is deliberately conservative: when in doubt, return CHAT.
The brain is free to override the result — the purpose is a hint, not a
decision.
"""

from __future__ import annotations

import re

from app.llm.registry import ModelPurpose

#: Fenced code blocks — the strongest possible signal for CODING.
_CODE_FENCE = re.compile(r"```")

#: Code-shaped tokens. Matched with substring checks.
_CODE_TOKENS: tuple[str, ...] = (
    "def ", "class ", "import ", "from ",
    "function(", "=> ", "const ", "let ", "var ",
    "return ", "async ", "await ", "yield ",
    "if __name__", "self.", "this.",
)

#: Programming language names (as lowercase words).
_LANGUAGES: tuple[str, ...] = (
    "python", "javascript", "typescript", "rust", "golang", "go",
    "java", "kotlin", "swift", "ruby", "php", "c++", "c#",
    "sql", "html", "css", "react", "vue", "django", "flask",
    "fastapi", "pandas", "numpy", "pytorch", "tensorflow",
    "bash", "shell", "powershell", "batch",
)

#: Coding verbs. Combined with a noun or a language name, these indicate
#: a coding request.
_CODING_VERBS: tuple[str, ...] = (
    "write", "implement", "create", "build", "make", "generate",
    "fix", "debug", "refactor", "optimize", "optimise",
    "reimplement", "rewrite",
)

#: Coding nouns. Combined with a verb or a language name, these indicate
#: a coding request.
_CODING_NOUNS: tuple[str, ...] = (
    "function", "script", "method", "class", "module", "package",
    "program", "algorithm", "snippet", "code", "regex", "query",
    "endpoint", "api",
)

#: Full phrases that are unambiguously coding regardless of other signals.
_CODING_PHRASES: tuple[str, ...] = (
    "write a function", "write a script", "write code",
    "write a method", "write a program",
    "fix this error", "fix the bug", "debug this",
    "traceback", "stack trace", "syntax error", "compile error",
    "unit test", "integration test", "refactor",
    "time complexity", "space complexity", "big o",
    "how do i code", "how to code", "how do i write",
    "how do i implement",
)

#: Named algorithms / data structures that are unambiguous coding signals.
_ALGORITHMS: tuple[str, ...] = (
    "quicksort", "mergesort", "bubble sort", "heapsort", "binary search",
    "linked list", "hash table", "binary tree", "red-black tree",
    "dijkstra", "kruskal", "prim", "bfs", "dfs", "dynamic programming",
)

#: English reasoning phrases.
_REASONING_PHRASES: tuple[str, ...] = (
    "why does", "why is", "why would", "why did", "why do",
    "prove that", "prove the", "derive the", "solve for",
    "step by step", "walk me through", "explain why", "explain how",
    "compare and contrast", "analyze", "analyse", "evaluate",
    "trade-off", "tradeoffs", "what are the pros", "what are the cons",
    "reasoning", "logically",
)

#: English fast/short-answer phrases.
_FAST_PHRASES: tuple[str, ...] = (
    "quick question", "briefly", "in one line", "in short",
    "just tell me", "one word", "yes or no",
    "tl;dr", "tldr",
)

#: Tamil/Tanglish coding intents.
_TAMIL_CODING: tuple[str, ...] = (
    "code ezhuthu", "code pannu", "function ezhuthu",
    "error fix pannu", "bug fix pannu",
)

#: Tamil/Tanglish reasoning intents.
_TAMIL_REASONING: tuple[str, ...] = (
    "yen", "yenu", "yen endral", "eppadi", "eppadi work",
    "explain pannu", "vivarikkavum",
)

#: Below this character count, the message is eligible for FAST.
_FAST_MAX_CHARS = 80

#: Above this character count, the message is never FAST regardless of
#: phrase — a "quick question" that fills a screen is not a quick question.
_LONG_MESSAGE_CHARS = 400


def detect_purpose(
    text: str,
    *,
    language: str | None = None,
) -> ModelPurpose:
    """Return the best guess at what the user is asking for.

    ``language`` is accepted for future use (per-language heuristics);
    it is currently advisory only. The detector never raises.
    """
    if not text or not text.strip():
        return ModelPurpose.CHAT

    lowered = text.lower()

    # ---- CODING (strongest signal) --------------------------------------
    if _looks_like_coding(text, lowered):
        return ModelPurpose.CODING

    # ---- FAST (short simple queries checked before the math regex) ------
    if _looks_like_fast(text, lowered):
        return ModelPurpose.FAST

    # ---- REASONING ------------------------------------------------------
    if _looks_like_reasoning(lowered):
        return ModelPurpose.REASONING

    # ---- CHAT (default) -------------------------------------------------
    return ModelPurpose.CHAT


# --------------------------------------------------------------------------
# Signal helpers
# --------------------------------------------------------------------------
def _looks_like_coding(text: str, lowered: str) -> bool:
    # 1. Code fence — the single most reliable signal.
    if _CODE_FENCE.search(text):
        return True

    # 2. Explicit coding phrases.
    if any(phrase in lowered for phrase in _CODING_PHRASES):
        return True
    if any(phrase in lowered for phrase in _TAMIL_CODING):
        return True

    # 3. Named algorithm / data structure.
    if any(algo in lowered for algo in _ALGORITHMS):
        return True

    # 4. Language name combined with a coding verb or noun.
    has_language = any(_has_word(lang, lowered) for lang in _LANGUAGES)
    has_verb = any(_has_word(verb, lowered) for verb in _CODING_VERBS)
    has_noun = any(_has_word(noun, lowered) for noun in _CODING_NOUNS)

    if has_language and (has_verb or has_noun):
        return True

    # 5. Verb + noun without a language name ("write a script").
    if has_verb and has_noun:
        return True

    # 6. Two or more code-shaped tokens.
    token_hits = sum(1 for tok in _CODE_TOKENS if tok in text)
    return token_hits >= 2


def _looks_like_reasoning(lowered: str) -> bool:
    if any(phrase in lowered for phrase in _REASONING_PHRASES):
        return True
    if any(phrase in lowered for phrase in _TAMIL_REASONING):
        return True
    # Math notation: a number with an operator, or a common math symbol.
    if re.search(r"\d\s*[+\-*/=^]\s*\d", lowered):
        return True
    if any(symbol in lowered for symbol in ("∑", "∫", "√", "≈", "≤", "≥")):
        return True
    return False


def _looks_like_fast(text: str, lowered: str) -> bool:
    # Length is a hard disqualifier. A "quick question" that fills a
    # screen is a real question, not a fast lookup.
    if len(text) > _LONG_MESSAGE_CHARS:
        return False

    # Fast phrases only apply to genuinely short messages.
    if any(phrase in lowered for phrase in _FAST_PHRASES):
        return True

    if len(text) > _FAST_MAX_CHARS:
        return False

    # Short and starts with a simple interrogative.
    simple_starters = ("what is", "who is", "when is", "where is", "how many")
    if any(lowered.startswith(starter) for starter in simple_starters):
        return True

    # Bare arithmetic like "2 + 2" or "5 * 7" — pure FAST material.
    stripped = text.strip().rstrip("=?")
    if re.fullmatch(r"\s*\d+\s*[+\-*/]\s*\d+\s*", stripped):
        return True

    return False


def _has_word(word: str, text: str) -> bool:
    """True when ``word`` appears as a whole word in ``text``.

    Word-boundary check prevents "write" from matching inside "rewrite"
    and "go" from matching inside "going".
    """
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


__all__ = ["detect_purpose"]
