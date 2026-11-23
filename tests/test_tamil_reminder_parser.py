"""Deterministic reminder parser — English, Tamil, Tanglish."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.tasks.time_parser import (
    IST, AmbiguousTimeError, parse_schedule_expression,
)

# Fixed "now" for deterministic tests
_NOW_IST = datetime(2026, 11, 19, 15, 0, tzinfo=IST)


def _parse(text: str):
    return parse_schedule_expression(text, now=_NOW_IST)


def test_naalaikku_kaalai_7_manikku_is_tomorrow_0700_ist():
    result = _parse("naalaikku kaalai 7 manikku")
    assert result.target_dt_ist == datetime(2026, 11, 20, 7, 0, tzinfo=IST)


def test_inniki_maalai_6_manikku_is_today_1800_ist():
    result = _parse("inniki maalai 6 manikku")
    assert result.target_dt_ist == datetime(2026, 11, 19, 18, 0, tzinfo=IST)


def test_tamil_script_naalaikku_kaalai_7():
    result = _parse("நாளைக்கு காலை 7 மணிக்கு")
    assert result.target_dt_ist == datetime(2026, 11, 20, 7, 0, tzinfo=IST)


def test_in_2_hours_is_relative():
    result = _parse("in 2 hours")
    assert result.target_dt_ist == datetime(2026, 11, 19, 17, 0, tzinfo=IST)
    assert result.used_rule == "relative"


def test_tanglish_n_nimishathil_is_relative_minutes():
    result = _parse("5 nimishathil")
    assert result.target_dt_ist == datetime(2026, 11, 19, 15, 5, tzinfo=IST)


def test_english_tomorrow_at_7pm():
    result = _parse("tomorrow at 7 pm")
    assert result.target_dt_ist == datetime(2026, 11, 20, 19, 0, tzinfo=IST)


def test_ambiguous_hour_without_marker_raises():
    with pytest.raises(AmbiguousTimeError):
        _parse("naalaikku 7 manikku")  # day present, no tod/ampm


def test_ambiguous_day_missing_raises():
    with pytest.raises(AmbiguousTimeError):
        _parse("kaalai 7 manikku")  # tod present, day missing


def test_past_today_raises():
    with pytest.raises(AmbiguousTimeError):
        _parse("inniki kaalai 6 manikku")  # 06:00 is in the past


def test_garbage_raises():
    with pytest.raises(AmbiguousTimeError):
        _parse("soon")