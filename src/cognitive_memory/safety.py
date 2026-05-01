from __future__ import annotations

import re


INSTRUCTION_PATTERNS = (
    r"\bignore\b.{0,40}\b(instructions?|polic(?:y|ies)|constraints?|do[_ -]?not[_ -]?use|deletion)\b",
    r"\breveal\b.{0,40}\b(deleted|private|confidential|restricted)\b",
    r"\bdelete\b.{0,20}\ball\b.{0,20}\b(constraints?|polic(?:y|ies)|rules?)\b",
    r"\balways\b.{0,20}\bpitch\b",
    r"\btreat\b.{0,40}\bas\b.{0,20}\bsystem\b.{0,20}\bpolic(?:y|ies)\b",
    r"\b(system|developer|tool)\s*:",
    r"ignore[_ -]?all[_ -]?polic(?:y|ies)",
)

SENSITIVE_PATTERNS = (
    r"\bssn\b",
    r"ssn[_ -]?\d{3,}",
    r"\bsocial\s+security\b",
    r"\b\d{3}[-_ ]?\d{2}[-_ ]?\d{4}\b",
    r"(?<!\d)\d{9}(?!\d)",
    r"\bprivate[_ -]?health\b",
    r"\bmedical[_ -]?condition\b",
    r"\bhealth[_ -]?condition\b",
)


def instruction_risk_reason(text: str) -> str:
    lowered = text.lower()
    for pattern in INSTRUCTION_PATTERNS:
        if re.search(pattern, lowered):
            return "possible_prompt_injection"
    return ""


def sensitive_risk_reason(text: str) -> str:
    lowered = text.lower()
    for pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, lowered):
            return "sensitive_without_consent"
    return ""


def unsafe_memory_reason(text: str, sensitivity: str = "low", consent_basis: str = "implicit") -> str:
    instruction_reason = instruction_risk_reason(text)
    if instruction_reason:
        return instruction_reason

    sensitive_reason = sensitive_risk_reason(text)
    if sensitive_reason and consent_basis != "explicit":
        return sensitive_reason

    if sensitivity in ("high", "restricted") and consent_basis != "explicit":
        return "sensitive_without_consent"

    return ""
