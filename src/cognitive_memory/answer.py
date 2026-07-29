"""Keyless extractive answer synthesis for conversational-memory QA.

The retrieval layer now surfaces verbatim conversation turns (see
``OpenConversationRetrievalPlanner`` verbatim indexing). Token-F1 / substring
scoring rewards short answer spans that contain the gold phrase, so returning a
whole turn scores poorly. This module extracts a concise, question-type-aware
span from an evidence turn -- no LLM, no embeddings.

It is deliberately conservative: when it cannot find a plausible span it returns
``""`` so the caller can abstain rather than emit a wrong or verbose answer.
"""

from __future__ import annotations

import re
from typing import List, Optional

from .models import tokenize

_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "did", "do", "does", "of", "to",
    "in", "on", "at", "for", "and", "or", "with", "her", "his", "their", "my",
    "i", "you", "he", "she", "it", "we", "they", "that", "this", "just", "really",
    "am", "been", "be", "have", "has", "had", "will", "would", "can", "could",
}

_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_REL_WORDS = (
    "dating", "married", "seeing", "engaged", "with", "to", "and",
)

# a proper-noun phrase: one to four capitalized words (allows &, -, ')
_PROPER = r"[A-Z][\w'&.-]*(?:\s+[A-Z][\w'&.-]*){0,3}"
_DET = r"(?:the\s+|a\s+|an\s+)?"

# capitalized tokens that are not names (sentence starters, pronoun I, temporal
# words) -- never a valid person/location answer on their own.
_BAD_PROPER = set(_WEEKDAYS) | set(_MONTHS) | {
    "i", "yesterday", "today", "tomorrow", "tonight", "the", "a", "an",
    "this", "that", "these", "those", "now", "then", "when", "where", "who",
    "we", "they", "he", "she", "it", "my", "our", "your", "last", "next",
}


def question_type(question: str) -> str:
    lowered = question.lower().strip()
    tokens = set(tokenize(question))
    if "where" in tokens:
        return "location"
    if tokens & {"who", "whom"}:
        return "person"
    if "when" in tokens or re.search(r"\bwhat\s+(?:day|date|time|year|month)\b", lowered):
        return "temporal"
    if re.search(r"\bhow\s+many\b", lowered) or {"number", "count"} & tokens:
        return "count"
    if re.match(r"^(?:is|are|was|were|do|does|did|has|have|had|can|could|will|would|should)\b", lowered):
        return "yes_no"
    if tokens & {"relationship", "status", "dating", "married", "partner"}:
        return "relationship"
    return "object"


def _first(pattern: str, text: str, flags: int = 0) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip(" .,!?;:") if m else ""


def _trim(span: str, max_tokens: int) -> str:
    parts = span.split()
    return " ".join(parts[:max_tokens]).strip(" .,!?;:")


def extractive_span(question: str, text: str, max_tokens: int = 12, qtype: Optional[str] = None) -> str:
    """Pull a concise answer span from ``text`` for ``question``.

    Returns ``""`` when no plausible span is found so the caller can abstain.
    """
    if not text:
        return ""
    qtype = qtype or question_type(question)
    q_subject = _query_subject(question)

    if qtype == "location":
        # prefer a place after a locative preposition (skipping a determiner)
        span = _first(r"\b(?:at|in|near|to|from|around|visited|visiting)\s+%s(%s)" % (_DET, _PROPER), text)
        span = _clean_proper(_strip_leading_subject(span, q_subject))
        if span:
            return _trim(span, max_tokens)
        # fall back to any proper-noun phrase that is not the question's subject
        return _trim(_other_proper(text, q_subject), max_tokens)

    if qtype == "person":
        span = _first(r"\b(?:%s)\s+%s(%s)" % ("|".join(_REL_WORDS), _DET, _PROPER), text)
        span = _clean_proper(_strip_leading_subject(span, q_subject))
        if span:
            return _trim(span, max_tokens)
        return _trim(_other_proper(text, q_subject), max_tokens)

    if qtype in ("temporal",):
        span = _first(
            r"\b((?:%s)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?)" % "|".join(_MONTHS), text, re.I
        )
        if not span:
            span = _first(r"\b(\d{1,2}\s+(?:%s)(?:,?\s*\d{4})?)" % "|".join(_MONTHS), text, re.I)
        if not span:
            span = _first(r"\b(%s)\b" % "|".join(_WEEKDAYS), text, re.I)
        if not span:
            span = _first(r"\b(yesterday|today|tomorrow|tonight|last\s+\w+|next\s+\w+|this\s+\w+|\d{4})\b", text, re.I)
        return _trim(span, max_tokens)

    if qtype == "count":
        # Don't grab the FIRST number (often a birth year/age/duration): drop
        # 4-digit years and take the last remaining number, which is where the
        # actual count usually sits ("born in 1990, has 2 siblings" -> 2).
        nums = re.findall(r"\d+", text)
        non_year = [n for n in nums if not (len(n) == 4 and 1900 <= int(n) <= 2099)]
        if non_year:
            return non_year[-1]
        if nums:
            return nums[-1]
        return _first(r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\b", text, re.I)

    # object / relationship / default: the content phrase least overlapping the
    # question (the "new" information), trimmed short.
    return _trim(_novel_phrase(question, text), max_tokens)


def _query_subject(question: str) -> str:
    m = re.search(_PROPER, question)
    return m.group(0).strip() if m else ""


def _strip_leading_subject(span: str, subject: str) -> str:
    if subject and span.lower().startswith(subject.lower()):
        span = span[len(subject):].strip(" .,!?;:")
    return span


def _clean_proper(span: str) -> str:
    """Drop leading non-name capitalized tokens ("Yesterday I ...")."""
    parts = span.split()
    while parts and parts[0].strip(" .,!?;:").lower() in _BAD_PROPER:
        parts.pop(0)
    return " ".join(parts).strip(" .,!?;:")


def _other_proper(text: str, subject: str) -> str:
    for m in re.finditer(_PROPER, text):
        cand = _clean_proper(m.group(0).strip())
        if not cand:
            continue
        if subject and cand.lower() == subject.lower():
            continue
        if subject and cand.lower().startswith(subject.lower()):
            rest = cand[len(subject):].strip()
            if rest:
                return rest
            continue
        return cand
    return ""


def _novel_phrase(question: str, text: str) -> str:
    q_tokens = {t for t in tokenize(question)}
    words = text.split()
    best: List[str] = []
    current: List[str] = []
    for word in words:
        norm = word.strip(" .,!?;:").lower()
        if norm and norm not in _STOP and norm not in q_tokens:
            current.append(word.strip(" .,!?;:"))
        else:
            if len(current) > len(best):
                best = current
            current = []
    if len(current) > len(best):
        best = current
    return " ".join(best).strip(" .,!?;:")
