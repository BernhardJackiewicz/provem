from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Optional

from .models import DEFAULT_PROJECT_ID, Reflection, TemporalFact


@dataclass
class MemoryScope:
    actor_type: str = "unknown"
    candidate_id: str = ""
    client_id: str = ""
    role_id: str = ""
    project_id: str = DEFAULT_PROJECT_ID
    subject_id: str = ""
    scope_confidence: float = 0.0
    relation: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def infer_scope_from_fact_parts(
    subject: str,
    relation: str,
    object_value: str,
    project_id: str = DEFAULT_PROJECT_ID,
) -> MemoryScope:
    normalized_subject = _normalize_entity(subject)
    scope = MemoryScope(
        actor_type="unknown",
        project_id=project_id,
        subject_id=normalized_subject,
        scope_confidence=0.2,
        relation=relation,
    )
    pitch_match = re.match(r"pitch_candidate_([a-z0-9]+)_client_([a-z0-9]+)", normalized_subject)
    scoped_candidate_match = re.match(r"candidate_([a-z0-9]+)@client_([a-z0-9]+)", normalized_subject)
    if pitch_match:
        scope.actor_type = "mixed"
        scope.candidate_id = "candidate_%s" % pitch_match.group(1)
        scope.client_id = "client_%s" % pitch_match.group(2)
        scope.scope_confidence = 0.95
    elif scoped_candidate_match:
        scope.actor_type = "mixed"
        scope.candidate_id = "candidate_%s" % scoped_candidate_match.group(1)
        scope.client_id = "client_%s" % scoped_candidate_match.group(2)
        scope.scope_confidence = 0.95
    elif normalized_subject == "candidate":
        scope.actor_type = "candidate"
        scope.scope_confidence = 0.75
    elif normalized_subject.startswith("candidate_"):
        scope.actor_type = "candidate"
        scope.candidate_id = normalized_subject
        scope.scope_confidence = 0.95
    elif normalized_subject == "client":
        scope.actor_type = "client"
        scope.scope_confidence = 0.75
    elif normalized_subject.startswith("client_"):
        scope.actor_type = "client"
        scope.client_id = normalized_subject
        scope.scope_confidence = 0.95
    elif normalized_subject == "role":
        scope.actor_type = "role"
        scope.scope_confidence = 0.75
    elif normalized_subject.startswith("role_"):
        scope.actor_type = "role"
        scope.role_id = normalized_subject
        scope.scope_confidence = 0.95
    elif normalized_subject.startswith("project_"):
        scope.actor_type = "project"
        scope.project_id = normalized_subject
        scope.scope_confidence = 0.9
    elif normalized_subject == "recruiter":
        scope.actor_type = "recruiter"
        scope.scope_confidence = 0.75

    object_entity = _normalize_entity(object_value)
    if relation in ("target_client", "client") and object_entity:
        scope.client_id = _with_prefix(object_entity, "client")
    return scope


def infer_scope_from_query(query: str, project_id: str = DEFAULT_PROJECT_ID) -> MemoryScope:
    candidate_ids = _entities_in_query(query, "candidate")
    client_ids = _entities_in_query(query, "client")
    role_ids = _entities_in_query(query, "role")
    project_ids = _entities_in_query(query, "project")
    known_actor_count = sum(bool(items) for items in (candidate_ids, client_ids, role_ids, project_ids))

    scope = MemoryScope(project_id=project_id, relation=infer_relation_from_query(query))
    if known_actor_count > 1:
        scope.actor_type = "mixed"
        scope.candidate_id = candidate_ids[0] if candidate_ids else ""
        scope.client_id = client_ids[0] if client_ids else ""
        scope.role_id = role_ids[0] if role_ids else ""
        scope.subject_id = candidate_ids[0] if candidate_ids else client_ids[0] if client_ids else role_ids[0] if role_ids else ""
        scope.scope_confidence = 0.75
    elif candidate_ids:
        scope.actor_type = "candidate"
        scope.candidate_id = candidate_ids[0]
        scope.subject_id = candidate_ids[0]
        scope.scope_confidence = 0.95
    elif client_ids:
        scope.actor_type = "client"
        scope.client_id = client_ids[0]
        scope.subject_id = client_ids[0]
        scope.scope_confidence = 0.95
    elif role_ids:
        scope.actor_type = "role"
        scope.role_id = role_ids[0]
        scope.subject_id = role_ids[0]
        scope.scope_confidence = 0.95
    elif project_ids:
        scope.actor_type = "project"
        scope.project_id = project_ids[0]
        scope.subject_id = project_ids[0]
        scope.scope_confidence = 0.9
    elif _mentions_actor(query, "candidate"):
        scope.actor_type = "candidate"
        scope.scope_confidence = 0.6
    elif _mentions_actor(query, "client"):
        scope.actor_type = "client"
        scope.scope_confidence = 0.6
    elif _mentions_actor(query, "role"):
        scope.actor_type = "role"
        scope.scope_confidence = 0.6
    else:
        scope.actor_type = "unknown"
        scope.scope_confidence = 0.0

    return scope


def scope_exclusion_reason(query_scope: MemoryScope, fact: TemporalFact) -> Optional[str]:
    fact_scope = scope_from_fact(fact)
    return memory_scope_exclusion_reason(query_scope, fact_scope, fact.subject, fact.relation)


def reflection_scope_exclusion_reason(query_scope: MemoryScope, reflection: Reflection) -> Optional[str]:
    reflection_scope = scope_from_reflection(reflection)
    return memory_scope_exclusion_reason(query_scope, reflection_scope, reflection.subject_id, reflection.relation_type)


def memory_scope_exclusion_reason(
    query_scope: MemoryScope,
    memory_scope: MemoryScope,
    subject_id: str,
    relation_type: str,
) -> Optional[str]:
    if query_scope.actor_type not in ("unknown", "mixed"):
        if memory_scope.actor_type != "unknown" and memory_scope.actor_type != query_scope.actor_type:
            return "wrong_scope"
        if query_scope.subject_id and memory_scope.subject_id and memory_scope.subject_id != query_scope.subject_id:
            return "wrong_scope"
        if memory_scope.actor_type == "unknown" and query_scope.subject_id and subject_id and subject_id != query_scope.subject_id:
            return "wrong_scope"

    if query_scope.actor_type == "candidate" and query_scope.candidate_id:
        if memory_scope.candidate_id and memory_scope.candidate_id != query_scope.candidate_id:
            return "wrong_scope"
    if query_scope.actor_type == "client" and query_scope.client_id:
        if memory_scope.client_id and memory_scope.client_id != query_scope.client_id:
            return "wrong_scope"
    if query_scope.actor_type == "role" and query_scope.role_id:
        if memory_scope.role_id and memory_scope.role_id != query_scope.role_id:
            return "wrong_scope"
    if query_scope.actor_type == "mixed":
        if memory_scope.actor_type == "candidate" and query_scope.candidate_id and memory_scope.candidate_id != query_scope.candidate_id:
            return "wrong_scope"
        if memory_scope.actor_type == "client" and query_scope.client_id and memory_scope.client_id != query_scope.client_id:
            return "wrong_scope"
        if memory_scope.actor_type == "role" and query_scope.role_id and memory_scope.role_id != query_scope.role_id:
            return "wrong_scope"
        if memory_scope.candidate_id and query_scope.candidate_id and memory_scope.candidate_id != query_scope.candidate_id:
            return "wrong_scope"
        if memory_scope.client_id and query_scope.client_id and memory_scope.client_id != query_scope.client_id:
            return "wrong_scope"
        if memory_scope.role_id and query_scope.role_id and memory_scope.role_id != query_scope.role_id:
            return "wrong_scope"

    if query_scope.relation and relation_type and relation_type != "profile" and not relation_matches(query_scope.relation, relation_type, query_scope.actor_type):
        return "insufficient_evidence"
    return None


def scope_from_fact(fact: TemporalFact) -> MemoryScope:
    return MemoryScope(
        actor_type=fact.scope.get("actor_type", "unknown"),
        candidate_id=fact.scope.get("candidate_id", ""),
        client_id=fact.scope.get("client_id", ""),
        role_id=fact.scope.get("role_id", ""),
        project_id=fact.scope.get("project_id", DEFAULT_PROJECT_ID),
        subject_id=fact.scope.get("subject_id", fact.subject),
        scope_confidence=float(fact.scope.get("scope_confidence", 0.0)),
        relation=fact.scope.get("relation", fact.relation),
    )


def scope_from_reflection(reflection: Reflection) -> MemoryScope:
    return MemoryScope(
        actor_type=reflection.actor_type,
        candidate_id=reflection.candidate_id,
        client_id=reflection.client_id,
        role_id=reflection.role_id,
        project_id=reflection.project_id,
        subject_id=reflection.subject_id,
        scope_confidence=reflection.scope_confidence,
        relation=reflection.relation_type,
    )


def infer_relation_from_query(query: str) -> str:
    lowered = query.lower().replace("_", " ")
    ordered = [
        ("required_skill", ("required skill", "requires skill")),
        ("notice_period", ("notice period", "notice")),
        ("salary", ("salary expectation", "salary target", "salary", "compensation")),
        ("work_mode", ("work mode", "remote", "hybrid", "onsite", "on site", "on-site")),
        ("relocation", ("relocation", "relocate")),
        ("location_policy", ("location policy", "location")),
        ("former_company", ("former company",)),
        ("avoid_company", ("company to avoid", "avoid company", "blocked company")),
        ("target_client", ("target client",)),
        ("budget", ("budget",)),
        ("skill", ("skill",)),
        ("stage", ("stage",)),
        ("status", ("status",)),
        ("availability", ("availability",)),
        ("timezone", ("timezone",)),
        ("backend", ("backend",)),
        ("contact_channel", ("contact channel",)),
        ("meeting_time", ("meeting time",)),
        ("response_style", ("response style",)),
        ("objection", ("objection", "concern")),
        ("competing_offer", ("competing offer", "offer")),
    ]
    for relation, markers in ordered:
        if any(marker in lowered for marker in markers):
            return relation
    return ""


def relation_matches(query_relation: str, fact_relation: str, actor_type: str = "unknown") -> bool:
    aliases = {
        "salary": {"salary", "salary_expectation", "salary_target"},
        "skill": {"skill"} if actor_type == "candidate" else {"skill", "required_skill"},
        "required_skill": {"required_skill"},
        "former_company": {"former_company", "company", "employer"},
        "avoid_company": {"avoid_company", "blocked_company"},
        "target_client": {"target_client", "client"},
        "work_mode": {"work_mode"},
        "location_policy": {"location_policy", "location"},
        "notice_period": {"notice_period"},
        "budget": {"budget"},
        "stage": {"stage"},
        "status": {"status"},
        "availability": {"availability"},
        "timezone": {"timezone"},
        "backend": {"backend"},
        "contact_channel": {"contact_channel"},
        "meeting_time": {"meeting_time"},
        "response_style": {"response_style"},
        "relocation": {"relocation"},
        "objection": {"objection"},
        "competing_offer": {"competing_offer"},
    }
    return fact_relation in aliases.get(query_relation, {query_relation})


def has_ambiguous_reference(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in ("that company", "that client", "that candidate", "that number", "that one")
    )


def reference_type_from_text(text: str) -> str:
    lowered = text.lower()
    for reference_type in ("company", "client", "candidate", "number"):
        if "that %s" % reference_type in lowered:
            return reference_type
    return ""


def _entities_in_query(query: str, kind: str) -> list:
    found = []
    pattern = r"\b%s[_\s-]+([a-zA-Z0-9]+)\b" % kind
    for match in re.finditer(pattern, query, flags=re.IGNORECASE):
        if match.group(1).lower() in _RESERVED_ENTITY_FOLLOWERS:
            continue
        found.append("%s_%s" % (kind, match.group(1).lower()))
    return found


def _mentions_actor(query: str, kind: str) -> bool:
    return bool(re.search(r"\b%s\b" % kind, query, flags=re.IGNORECASE))


def _normalize_entity(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _with_prefix(value: str, prefix: str) -> str:
    normalized = _normalize_entity(value)
    if normalized.startswith(prefix + "_"):
        return normalized
    return "%s_%s" % (prefix, normalized)


_RESERVED_ENTITY_FOLLOWERS = {
    "budget",
    "location",
    "policy",
    "preference",
    "requirement",
    "requirements",
    "required",
    "skill",
    "skills",
    "stage",
    "salary",
    "notice",
    "period",
    "work",
    "mode",
    "company",
    "client",
}
