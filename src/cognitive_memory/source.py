from __future__ import annotations

from typing import Dict


SOURCE_TRUST_RANK = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "authoritative": 4,
}


def source_metadata_for(source: str, actor: str = "user", memory_type: str = "") -> Dict[str, str]:
    """Normalize lightweight source metadata for conflict handling.

    This is intentionally small. It is not a production trust model; it only
    gives the local prototype enough structure to avoid unsafe source mixing.
    """

    normalized = (source or "unknown").strip().lower()
    actor_normalized = (actor or "user").strip().lower()

    if memory_type == "constraint" or normalized in ("system_policy", "policy"):
        return {
            "source_type": "system_policy",
            "source_trust": "authoritative",
            "source_conflict_policy": "policy_wins",
            "source_sensitivity": "",
        }
    if normalized in ("candidate", "candidate_statement"):
        return {
            "source_type": "candidate_statement",
            "source_trust": "high",
            "source_conflict_policy": "direct_statement_precedence",
            "source_sensitivity": "",
        }
    if normalized in ("client", "client_statement"):
        return {
            "source_type": "client_statement",
            "source_trust": "high",
            "source_conflict_policy": "direct_statement_precedence",
            "source_sensitivity": "",
        }
    if normalized in ("recruiter_note", "recruiter", "note"):
        return {
            "source_type": "recruiter_note",
            "source_trust": "low",
            "source_conflict_policy": "weak_assumption",
            # a human free-text note is where volunteered special-category
            # data lands: sensitive by provenance, not by content
            "source_sensitivity": "high",
        }
    if normalized in ("verified_tool", "verified_crm"):
        return {
            "source_type": "tool_record",
            "source_trust": "authoritative",
            "source_conflict_policy": "abstain_on_conflict",
            "source_sensitivity": "",
        }
    if normalized in ("crm", "crm_record"):
        return {
            "source_type": "crm_record",
            "source_trust": "medium",
            "source_conflict_policy": "abstain_on_conflict",
            "source_sensitivity": "",
        }
    if normalized in ("tool", "tool_record"):
        return {
            "source_type": "tool_record",
            "source_trust": "high",
            "source_conflict_policy": "abstain_on_conflict",
            "source_sensitivity": "",
        }
    if normalized in ("user", "chat", "user_statement"):
        source_type = "user_statement"
        if actor_normalized == "tool":
            source_type = "tool_record"
        return {
            "source_type": source_type,
            "source_trust": "medium",
            "source_conflict_policy": "abstain_on_conflict",
            "source_sensitivity": "",
        }
    return {
        "source_type": "unknown",
        "source_trust": "medium",
        "source_conflict_policy": "abstain_on_conflict",
        "source_sensitivity": "",
    }


def source_rank(source_trust: str) -> int:
    return SOURCE_TRUST_RANK.get((source_trust or "medium").lower(), 2)


def is_direct_source(source_type: str) -> bool:
    return source_type in {"candidate_statement", "client_statement", "user_statement"}


def is_subject_direct_source(subject: str, source_type: str) -> bool:
    if subject.startswith("candidate_"):
        return source_type == "candidate_statement"
    if subject.startswith("client_"):
        return source_type == "client_statement"
    return source_type == "user_statement"


def source_conflict_decision(
    old_subject: str,
    old_source_type: str,
    old_source_trust: str,
    new_source_type: str,
    new_source_trust: str,
) -> str:
    """Return one of supersede, keep_existing or store_conflict."""

    if old_source_type == new_source_type:
        return "supersede"

    old_subject_direct = is_subject_direct_source(old_subject, old_source_type)
    new_subject_direct = is_subject_direct_source(old_subject, new_source_type)

    if old_subject_direct and new_source_type == "recruiter_note":
        return "keep_existing"
    if old_subject_direct and new_source_type == "tool_record":
        return "keep_existing"
    if new_subject_direct and old_source_type == "recruiter_note":
        return "supersede"
    if new_subject_direct and old_source_type == "tool_record":
        return "supersede"

    old_rank = source_rank(old_source_trust)
    new_rank = source_rank(new_source_trust)
    if new_rank <= 1 and old_rank >= 3:
        return "keep_existing"
    if old_source_type == "crm_record" and new_source_type == "user_statement" and old_rank <= 2:
        return "supersede"
    if old_rank <= 1 and new_rank >= 3 and is_direct_source(new_source_type):
        return "supersede"

    return "store_conflict"
