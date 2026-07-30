"""Deterministic write-time resolution of relative date expressions.

Conversational memory stores turns verbatim; questions later ask "when did X
happen?" while the turn only says "yesterday" or "last Saturday". The message
timestamp is the *utterance* date, not the event date, so answerers routinely
anchor on the wrong day (a measured 14% loss pattern on LoCoMo vs Mem0).

``annotate_relative_dates`` appends the resolved absolute date IN BRACKETS after
the expression ("... yesterday [=7 May 2023] ...") and never rewrites the
original wording. That preserves verbatim fidelity (Mem0's write-time
*replacement* measurably mangles dates) while giving retrieval and answerers the
resolved anchor. Pure stdlib, regex + timedelta; conservative by design: only
expressions with an unambiguous single resolution are annotated.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional, Union

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# Longest-first alternation so "the day before yesterday" wins over "yesterday".
_PATTERN = re.compile(
    r"(?<![\w=\[])(?:"
    r"(?P<daybefore>the\s+day\s+before\s+yesterday)"
    r"|(?P<yesterday>yesterday)"
    r"|(?P<lastnight>last\s+night)"
    r"|(?P<thismorning>this\s+(?:morning|afternoon|evening))"
    r"|(?P<tonight>tonight)"
    r"|(?P<lastweekday>last\s+(?P<wd>monday|tuesday|wednesday|thursday|friday|saturday|sunday))"
    r"|(?P<lastweekend>last\s+weekend)"
    r"|(?P<lastweek>last\s+week)"
    r"|(?P<lastmonth>last\s+month)"
    r"|(?P<lastyear>last\s+year)"
    r"|(?P<weekago>a\s+week\s+ago)"
    r"|(?P<monthago>a\s+month\s+ago)"
    r"|(?P<yearago>a\s+year\s+ago)"
    r"|(?P<daysago>(?P<n>\d{1,2}|a\s+couple\s+of|a\s+few)\s+days\s+ago)"
    r"|(?P<weeksago>(?P<wn>\d{1,2}|a\s+couple\s+of|a\s+few)\s+weeks\s+ago)"
    r")(?!\s*\[=)(?![\w=\]])",
    re.IGNORECASE,
)

_COUNT_WORDS = {"a couple of": 2, "a few": 3}


def _day(anchor: date) -> str:
    return "%d %s %d" % (anchor.day, anchor.strftime("%B"), anchor.year)


def _month(anchor: date) -> str:
    return "%s %d" % (anchor.strftime("%B"), anchor.year)


def _count(raw: str) -> int:
    key = re.sub(r"\s+", " ", raw.strip().lower())
    if key in _COUNT_WORDS:
        return _COUNT_WORDS[key]
    return int(key)


def resolve(expression_group: str, match: "re.Match", anchor: date) -> Optional[str]:
    if expression_group == "daybefore":
        return _day(anchor - timedelta(days=2))
    if expression_group in ("yesterday", "lastnight"):
        return _day(anchor - timedelta(days=1))
    if expression_group in ("thismorning", "tonight"):
        return _day(anchor)
    if expression_group == "lastweekday":
        target = _WEEKDAYS.index(match.group("wd").lower())
        delta = (anchor.weekday() - target) % 7
        return _day(anchor - timedelta(days=delta or 7))
    if expression_group == "lastweekend":
        delta = (anchor.weekday() - 5) % 7  # most recent Saturday strictly before today
        return "weekend of %s" % _day(anchor - timedelta(days=delta or 7))
    if expression_group in ("lastweek", "weekago"):
        return "week of %s" % _day(anchor - timedelta(days=7))
    if expression_group in ("lastmonth", "monthago"):
        first = anchor.replace(day=1) - timedelta(days=1)
        return _month(first)
    if expression_group in ("lastyear", "yearago"):
        return str(anchor.year - 1)
    if expression_group == "daysago":
        return _day(anchor - timedelta(days=_count(match.group("n"))))
    if expression_group == "weeksago":
        return "week of %s" % _day(anchor - timedelta(days=7 * _count(match.group("wn"))))
    return None


def annotate_relative_dates(text: str, anchor: Union[date, datetime, None]) -> str:
    """Append ``[=<resolved date>]`` after each unambiguous relative expression."""
    if not text or anchor is None:
        return text
    if isinstance(anchor, datetime):
        anchor = anchor.date()

    def _sub(match: "re.Match") -> str:
        for group in ("daybefore", "yesterday", "lastnight", "thismorning", "tonight",
                      "lastweekday", "lastweekend", "lastweek", "lastmonth", "lastyear",
                      "weekago", "monthago", "yearago", "daysago", "weeksago"):
            if match.group(group):
                resolved = resolve(group, match, anchor)
                if resolved:
                    return "%s [=%s]" % (match.group(0), resolved)
                break
        return match.group(0)

    return _PATTERN.sub(_sub, text)
