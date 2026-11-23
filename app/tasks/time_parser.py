"""Deterministic parser for reminder time expressions.

English, Tanglish, and Tamil script. Returns a ParsedSchedule or raises
AmbiguousTimeError. NEVER uses the current wall clock as a silent
default — every output is traceable to an explicit token in the input.

Supported tokens
----------------
Day      : naalaikku / naalai / நாளைக்கு / நாளை / tomorrow
           inniki / innikku / indru / இன்று / today
Time-of-day
         : kaalai / காலை / morning          (05:00-11:59)
           mathiyam / மதியம் / noon         (12:00-12:59)
           afternoon                        (13:00-16:59)
           maalai / மாலை / evening          (17:00-20:59)
           iravu / இரவு / night             (21:00-04:59)
Clock    : "N manikku" / "N மணிக்கு" / "N o'clock"
           "at N" / "N am|pm" / "N:MM"
Relative : "in N minutes|hours|mins|hrs"
           "N nimishathil" / "N நிமிடத்தில்"
Suffix   : am / pm (case-insensitive)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class AmbiguousTimeError(ValueError):
    """Raised when the input cannot be resolved with confidence."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ParsedSchedule:
    target_dt_utc: datetime
    target_dt_ist: datetime
    source: str
    used_rule: str


# ── Vocabularies ─────────────────────────────────────────────────────
_DAY_WORDS: dict[str, str] = {
    "naalaikku": "tomorrow", "naalai": "tomorrow",
    "நாளைக்கு": "tomorrow", "நாளை": "tomorrow", "tomorrow": "tomorrow",
    "inniki": "today", "innikku": "today", "indru": "today",
    "இன்று": "today", "today": "today",
}

# word → (canonical, lo, hi). lo/hi are 24-hour windows. Informational;
# the actual hour-vs-tod rule lives in _normalize_hour.
_TOD_WORDS: dict[str, tuple[str, int, int]] = {
    "kaalai": ("morning", 5, 11), "காலை": ("morning", 5, 11),
    "morning": ("morning", 5, 11),
    "mathiyam": ("noon", 12, 12), "மதியம்": ("noon", 12, 12),
    "noon": ("noon", 12, 12),
    "afternoon": ("afternoon", 13, 16),
    "maalai": ("evening", 17, 20), "மாலை": ("evening", 17, 20),
    "evening": ("evening", 17, 20),
    "iravu": ("night", 21, 4), "இரவு": ("night", 21, 4),
    "night": ("night", 21, 4),
}

_MIN_UNIT = r"(?:nimishathil|nimisham|நிமிடத்தில்|நிமிஷத்தில்|minutes?|mins?)"
_HOURS_UNIT = r"(?:hours?|hrs?)"
_AMPM = re.compile(r"\b(am|pm)\b", re.IGNORECASE)

# Boundary that treats both Latin and Tamil as word characters. Python's
# default \b does not work around Tamil because vowel signs (ை, ு, …) and
# the virama (்) are combining marks, not \w. Without this, "காலை" with
# \b on either side never matches.
_BOUNDARY = r"A-Za-z0-9\u0B80-\u0BFF"
_BOUNDARY_LEFT = rf"(?<![{_BOUNDARY}])"
_BOUNDARY_RIGHT = rf"(?![{_BOUNDARY}])"


def _word_re(word: str) -> str:
    return f"{_BOUNDARY_LEFT}{re.escape(word)}{_BOUNDARY_RIGHT}"


def _now_ist() -> datetime:
    return datetime.now(tz=IST)


def _match_day(text: str) -> str | None:
    for word, canonical in _DAY_WORDS.items():
        if re.search(_word_re(word), text, re.IGNORECASE):
            return canonical
    return None


def _match_tod(text: str) -> str | None:
    for word in _TOD_WORDS:
        if re.search(_word_re(word), text, re.IGNORECASE):
            return word
    return None


def _match_ampm(text: str) -> str | None:
    m = _AMPM.search(text)
    return m.group(1).lower() if m else None


def _match_clock(text: str) -> tuple[int, int] | None:
    # 1) "N:MM"
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 2) "N am|pm"
    m = re.search(
        rf"{_BOUNDARY_LEFT}(\d{{1,2}})\s*(?:am|pm){_BOUNDARY_RIGHT}",
        text, re.IGNORECASE,
    )
    if m:
        return int(m.group(1)), 0
    # 3) "at N" (English — unit precedes number)
    m = re.search(
        rf"\bat\s+(\d{{1,2}}){_BOUNDARY_RIGHT}",
        text, re.IGNORECASE,
    )
    if m:
        return int(m.group(1)), 0
    # 4) "N manikku|மணிக்கு|o'clock" (unit follows number)
    m = re.search(
        rf"{_BOUNDARY_LEFT}(\d{{1,2}})\s*"
        rf"(?:manikku|மணிக்கு|o'?clock){_BOUNDARY_RIGHT}",
        text, re.IGNORECASE,
    )
    if m:
        return int(m.group(1)), 0
    return None


def _match_relative(text: str) -> tuple[float, str] | None:
    # "in N hours|minutes"
    m = re.search(
        rf"\bin\s+(\d+(?:\.\d+)?)\s+({_HOURS_UNIT}|{_MIN_UNIT})",
        text, re.IGNORECASE,
    )
    if m:
        unit = (
            "hours" if re.match(_HOURS_UNIT, m.group(2), re.I) else "minutes"
        )
        return float(m.group(1)), unit
    # Tanglish "N nimishathil" (no leading "in")
    m = re.search(
        rf"{_BOUNDARY_LEFT}(\d+(?:\.\d+)?)\s+({_MIN_UNIT})",
        text, re.IGNORECASE,
    )
    if m:
        return float(m.group(1)), "minutes"
    return None


def _normalize_hour(
    hour: int, tod: str | None, ampm: str | None, raw: str
) -> int:
    """Return 24-hour representation or raise AmbiguousTimeError."""
    if ampm is not None:
        if not (1 <= hour <= 12):
            raise AmbiguousTimeError(f"invalid 12-hour clock: {hour}")
        if ampm == "am":
            return 0 if hour == 12 else hour
        return 12 if hour == 12 else hour + 12

    if tod is None:
        raise AmbiguousTimeError(
            f"hour {hour} without am/pm or morning/evening marker "
            f"in {raw!r}"
        )

    canonical = _TOD_WORDS[tod][0]

    if canonical == "morning":
        if 5 <= hour <= 11:
            return hour
        if hour == 12:
            return 12
        raise AmbiguousTimeError(
            f"hour {hour} does not fit 'morning' (5-11 AM) in {raw!r}"
        )

    if canonical == "noon":
        if hour == 12:
            return 12
        if 1 <= hour <= 4:
            return hour + 12
        raise AmbiguousTimeError(
            f"hour {hour} does not fit 'noon' in {raw!r}"
        )

    if canonical == "afternoon":
        if 1 <= hour <= 4:
            return hour + 12
        if hour == 12:
            return 12
        raise AmbiguousTimeError(
            f"hour {hour} does not fit 'afternoon' in {raw!r}"
        )

    if canonical == "evening":
        if 5 <= hour <= 8:
            return hour + 12
        raise AmbiguousTimeError(
            f"hour {hour} does not fit 'evening' (5-8 PM) in {raw!r}"
        )

    if canonical == "night":
        if 9 <= hour <= 11:
            return hour + 12
        if hour == 12:
            return 0
        if 1 <= hour <= 4:
            return hour
        raise AmbiguousTimeError(
            f"hour {hour} does not fit 'night' in {raw!r}"
        )

    raise AmbiguousTimeError(f"unknown tod {tod!r}")


def parse_schedule_expression(
    text: str, *, now: datetime | None = None
) -> ParsedSchedule:
    """Parse a reminder time phrase. Raises AmbiguousTimeError on failure."""
    if not text or not text.strip():
        raise AmbiguousTimeError("empty time expression")

    raw = text.strip()
    now_ist = (now or _now_ist()).astimezone(IST)

    # Rule 1 — relative "in N minutes/hours"
    rel = _match_relative(raw)
    if rel is not None:
        amount, unit = rel
        delta = (
            timedelta(hours=amount) if unit == "hours"
            else timedelta(minutes=amount)
        )
        target = now_ist + delta
        return ParsedSchedule(
            target_dt_utc=target.astimezone(timezone.utc),
            target_dt_ist=target,
            source=f"in {amount:g} {unit}",
            used_rule="relative",
        )

    # Rule 2 — clock time with day + time-of-day
    day = _match_day(raw)
    tod = _match_tod(raw)
    ampm = _match_ampm(raw)
    clock = _match_clock(raw)

    if clock is None:
        raise AmbiguousTimeError(f"no time found in {raw!r}")

    hour, minute = clock
    hour24 = _normalize_hour(hour, tod, ampm, raw)

    if day is None:
        raise AmbiguousTimeError(
            f"{raw!r}: no day specified (today or tomorrow?)"
        )
    if not (0 <= hour24 <= 23 and 0 <= minute <= 59):
        raise AmbiguousTimeError(f"invalid time {hour24}:{minute:02d}")

    today_target = now_ist.replace(
        hour=hour24, minute=minute, second=0, microsecond=0
    )
    if day == "tomorrow":
        target = today_target + timedelta(days=1)
    else:  # today
        if today_target <= now_ist:
            raise AmbiguousTimeError(
                f"{raw!r}: 'today {hour24:02d}:{minute:02d}' is in the past"
            )
        target = today_target

    return ParsedSchedule(
        target_dt_utc=target.astimezone(timezone.utc),
        target_dt_ist=target,
        source=f"{day} {tod or ''} {hour24:02d}:{minute:02d}".strip(),
        used_rule="absolute",
    )


def format_ist(ts: float) -> str:
    """Render a UTC epoch as a human IST string."""
    return datetime.fromtimestamp(ts, tz=IST).strftime(
        "%a %d %b %Y, %I:%M %p IST"
    )


__all__ = [
    "IST", "AmbiguousTimeError", "ParsedSchedule",
    "parse_schedule_expression", "format_ist",
]