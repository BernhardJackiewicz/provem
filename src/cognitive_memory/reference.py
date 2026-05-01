from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable, Protocol

from .models import RetrievalRequest, TemporalFact
from .policy import PolicyStore


@dataclass
class ReferenceResolution:
    resolved: bool
    reference_type: str
    term: str = ""
    reason: str = ""
    antecedent_ids: list = field(default_factory=list)


class ReferenceResolver(Protocol):
    def resolve(
        self,
        reference_type: str,
        facts: Iterable[TemporalFact],
        policy: PolicyStore,
        user_id: str,
        project_id: str,
    ) -> ReferenceResolution:
        ...


class DeterministicReferenceResolver:
    """Conservative resolver for policy commands with "that X" references."""

    def resolve(
        self,
        reference_type: str,
        facts: Iterable[TemporalFact],
        policy: PolicyStore,
        user_id: str,
        project_id: str,
    ) -> ReferenceResolution:
        candidates = []
        for fact in sorted(facts, key=lambda item: item.valid_at, reverse=True):
            if policy.exclusion_reason(
                fact,
                RetrievalRequest(query=fact.claim_text, user_id=user_id, project_id=project_id),
            ):
                continue
            term = self._term_for_reference(reference_type, fact)
            if term:
                candidates.append((term, fact.id))

        by_term = {}
        for term, fact_id in candidates:
            by_term.setdefault(term.lower(), {"term": term, "ids": []})["ids"].append(fact_id)

        if len(by_term) == 1:
            item = next(iter(by_term.values()))
            return ReferenceResolution(
                resolved=True,
                reference_type=reference_type,
                term=item["term"],
                antecedent_ids=list(item["ids"]),
            )
        if len(by_term) > 1:
            ids = [fact_id for item in by_term.values() for fact_id in item["ids"]]
            return ReferenceResolution(
                resolved=False,
                reference_type=reference_type,
                reason="ambiguous_reference",
                antecedent_ids=ids,
            )
        return ReferenceResolution(
            resolved=False,
            reference_type=reference_type,
            reason="ambiguous_reference",
        )

    def _term_for_reference(self, reference_type: str, fact: TemporalFact) -> str:
        relation = fact.relation.lower()
        subject = fact.subject.lower()
        if reference_type == "company":
            if "company" in relation or "employer" in relation:
                return fact.object
        if reference_type == "client":
            if "client" in relation:
                return fact.object
            if subject.startswith("client_"):
                return _display_entity(subject, "client")
        if reference_type == "candidate":
            if subject.startswith("candidate_"):
                return subject
        if reference_type == "number":
            if re.search(r"\d", fact.object):
                return fact.object
        return ""


def _display_entity(value: str, prefix: str) -> str:
    if value.startswith(prefix + "_"):
        return value[len(prefix) + 1 :]
    return value
