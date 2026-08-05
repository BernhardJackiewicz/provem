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
    # --- generalized instruction-override markers (EN) ---------------------
    # Calibrated on the deepset/prompt-injections *train/dev* split only:
    # lifts real-payload detection recall from ~4% to ~36% at 0% benign FPR.
    # Kept high-precision so benign recall/quarantine is not affected.
    r"\b(ignore|disregard|forget)\b.{0,30}\b(all|everything|any|the|above|preceding|previous|prior)\b.{0,30}"
    r"\b(instruction|order|command|information|task|rule|prompt|context|preceding|previous|before)",
    r"\bforget\b.{0,20}\b(everything|all)\b",
    r"\bignore\b.{0,15}\b(above|previous|preceding|prior|earlier)\b",
    r"\bnew\b.{0,15}\b(tasks?|instructions?|orders?)\b.{0,15}\b(follow|now)\b",
    r"\bnow\b.{0,20}\bnew\b.{0,10}\b(tasks?|instructions?)\b",
    r"\b(pretend|act)\b.{0,15}\b(you are|as|to be)\b",
    r"\byou are now\b",
    # --- German variants (deepset is largely German) ----------------------
    r"\bvergiss\b.{0,25}\b(alle|alles|vorherige|bisherige|vorherigen|fr(?:ü|ue)heren|obige)\b",
    r"\bignoriere\b.{0,25}\b(alle|alles|vorherige|bisherige|obige)\b",
    r"\b(nun|jetzt)\b.{0,20}\bfolge?n?\b.{0,15}\bneue",
    r"\bneue\b.{0,15}\b(aufgaben?|anweisungen?|instruktionen?|befehle?)\b.{0,15}\bfolg",
    r"\bich\s+m(?:ö|oe)chte,?\s+dass\s+(sie|du)\b.{0,20}\bals\b.{0,25}\b(fungier|agier|verhalt)",
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


def instruction_risk_reason(text: str, extra_patterns: tuple = ()) -> str:
    lowered = text.lower()
    for pattern in INSTRUCTION_PATTERNS:
        if re.search(pattern, lowered):
            return "possible_prompt_injection"
    for pattern in extra_patterns:
        if re.search(pattern, lowered):
            return "possible_prompt_injection"
    return ""


def sensitive_risk_reason(text: str, extra_patterns: tuple = ()) -> str:
    lowered = text.lower()
    for pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, lowered):
            return "sensitive_without_consent"
    for pattern in extra_patterns:
        if re.search(pattern, lowered):
            return "sensitive_without_consent"
    return ""


def unsafe_memory_reason(
    text: str,
    sensitivity: str = "low",
    consent_basis: str = "implicit",
    source_sensitivity: str = "",
) -> str:
    instruction_reason = instruction_risk_reason(text)
    if instruction_reason:
        return instruction_reason

    sensitive_reason = sensitive_risk_reason(text)
    if sensitive_reason and consent_basis != "explicit":
        return sensitive_reason

    if sensitivity in ("high", "restricted") and consent_basis != "explicit":
        return "sensitive_without_consent"

    # Channel sensitivity: the field the text arrived in (a human free-text
    # note) marks it sensitive even when every word is ordinary.
    if source_sensitivity == "high" and consent_basis != "explicit":
        return "sensitive_channel"

    return ""
