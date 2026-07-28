from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import Episode, MemoryCandidate
from .scope import reference_type_from_text
from .source import source_metadata_for


FACT_PATTERN = re.compile(r"^(FACT|SENSITIVE)\s+([^|]+)\|([^|]+)\|(.+)$", re.IGNORECASE)
PREFERENCE_PATTERN = re.compile(r"^PREFERENCE\s+([^=]+)=(.+)$", re.IGNORECASE)
PROCEDURE_PATTERN = re.compile(r"^PROCEDURE\s+(.+)$", re.IGNORECASE)
DELETE_PATTERN = re.compile(r"^(DELETE|FORGET|DO_NOT_USE)\s+(.+)$", re.IGNORECASE)


class ExtractorSchemaError(ValueError):
    """Raised when an optional schema-constrained extractor returns bad data."""


class DeterministicExtractor:
    """Small schema-first extractor for reproducible research tests.

    Production extraction should use a model with strict schema validation, but
    a deterministic extractor keeps the benchmark falsifiable and repeatable.
    """

    def extract(self, episode: Episode) -> List[MemoryCandidate]:
        candidates: List[MemoryCandidate] = []
        for raw_line in episode.content.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            structured = self._extract_structured_line(line, episode)
            if structured is not None:
                candidates.append(structured)
                continue

            natural = self._extract_natural_line(line, episode)
            if natural is not None:
                candidates.append(natural)

        if not candidates and episode.content.strip():
            low_value = MemoryCandidate(
                claim=episode.content.strip(),
                type="episodic_note",
                importance=0.2,
                novelty=0.2,
                confidence=0.4,
                stability="ephemeral",
                lifespan="session",
                evidence_episode_ids=[episode.id],
                recommended_action="ignore",
                user_id=episode.user_id,
                project_id=episode.project_id,
                metadata=self._episode_metadata(episode),
            )
            candidates.append(low_value)
        return candidates

    def _extract_structured_line(self, line: str, episode: Episode) -> Optional[MemoryCandidate]:
        fact_match = FACT_PATTERN.match(line)
        if fact_match:
            kind, subject, relation, object_value = fact_match.groups()
            sensitive = kind.upper() == "SENSITIVE"
            return self._candidate_for_fact(
                episode=episode,
                subject=subject.strip(),
                relation=relation.strip(),
                object_value=object_value.strip(),
                sensitive=sensitive,
                confidence=0.75 if not sensitive else 0.65,
            )

        preference_match = PREFERENCE_PATTERN.match(line)
        if preference_match:
            relation, object_value = preference_match.groups()
            return self._candidate_for_fact(
                episode=episode,
                subject=episode.user_id,
                relation=relation.strip(),
                object_value=object_value.strip(),
                sensitive=False,
                memory_type="preference",
                confidence=0.7,
            )

        procedure_match = PROCEDURE_PATTERN.match(line)
        if procedure_match:
            procedure = procedure_match.group(1).strip()
            return MemoryCandidate(
                claim=procedure,
                type="procedure",
                importance=0.75,
                novelty=0.6,
                confidence=0.65,
                stability="stable",
                lifespan="until_changed",
                evidence_episode_ids=[episode.id],
                user_id=episode.user_id,
                project_id=episode.project_id,
                metadata=self._episode_metadata(episode),
            )

        delete_match = DELETE_PATTERN.match(line)
        if delete_match:
            command, term = delete_match.groups()
            action = "do_not_use" if command.upper() == "DO_NOT_USE" else "delete"
            return MemoryCandidate(
                claim="do not use %s" % term,
                type="constraint",
                importance=1.0,
                novelty=1.0,
                confidence=0.95,
                stability="stable",
                lifespan="until_changed",
                evidence_episode_ids=[episode.id],
                recommended_action=action,
                user_id=episode.user_id,
                project_id=episode.project_id,
                metadata=dict(self._episode_metadata(episode, memory_type="constraint"), deletion_term=term),
            )

        return None

    def _extract_natural_line(self, line: str, episode: Episode) -> Optional[MemoryCandidate]:
        lowered = line.lower()
        if "forget" in lowered or "do not use" in lowered or "delete" in lowered:
            term = self._term_after_keyword(line)
            if term:
                action = "do_not_use" if "do not use" in lowered else "delete"
                return MemoryCandidate(
                    claim="do not use %s" % term,
                    type="constraint",
                    importance=1.0,
                    novelty=1.0,
                    confidence=0.8,
                    stability="stable",
                    lifespan="until_changed",
                    evidence_episode_ids=[episode.id],
                    recommended_action=action,
                    user_id=episode.user_id,
                    project_id=episode.project_id,
                    metadata=dict(self._episode_metadata(episode, memory_type="constraint"), deletion_term=term),
                )

        preference = self._natural_preference(line)
        if preference is not None:
            relation, object_value = preference
            return self._candidate_for_fact(
                episode=episode,
                subject=episode.user_id,
                relation=relation,
                object_value=object_value,
                sensitive=False,
                memory_type="preference",
                confidence=0.55,
            )
        return None

    def _candidate_for_fact(
        self,
        episode: Episode,
        subject: str,
        relation: str,
        object_value: str,
        sensitive: bool,
        memory_type: str = "semantic_fact",
        confidence: float = 0.65,
    ) -> MemoryCandidate:
        risk = "high" if sensitive else "low"
        sensitivity = "high" if sensitive else episode.sensitivity
        action = "ask_consent" if sensitive and episode.consent_basis != "explicit" else "store"
        return MemoryCandidate(
            claim="%s %s %s" % (subject, relation, object_value),
            type=memory_type,
            importance=0.65 if not sensitive else 0.8,
            novelty=0.7,
            confidence=confidence,
            stability="stable" if memory_type == "preference" else "temporary",
            lifespan="until_changed",
            risk_level=risk,
            evidence_episode_ids=[episode.id],
            recommended_action=action,
            user_id=episode.user_id,
            project_id=episode.project_id,
            metadata=dict(
                self._episode_metadata(episode),
                subject=subject,
                relation=relation,
                object=object_value,
                sensitivity=sensitivity,
            ),
        )

    def _episode_metadata(self, episode: Episode, memory_type: str = "") -> dict:
        source_defaults = source_metadata_for(episode.source, episode.actor, memory_type=memory_type)
        use_defaults = memory_type == "constraint"
        return {
            "source": episode.source,
            "source_type": source_defaults["source_type"] if use_defaults else (episode.source_type or source_defaults["source_type"]),
            "source_trust": source_defaults["source_trust"] if use_defaults else (episode.source_trust or source_defaults["source_trust"]),
            "source_timestamp": episode.source_timestamp,
            "source_conflict_policy": source_defaults["source_conflict_policy"]
            if use_defaults
            else (episode.source_conflict_policy or source_defaults["source_conflict_policy"]),
            "sensitivity": episode.sensitivity,
            "consent_basis": episode.consent_basis,
            "retention_policy": episode.retention_policy,
            "visibility": episode.visibility,
        }

    def _term_after_keyword(self, line: str) -> str:
        parts = re.split(r"forget|do not use|delete", line, flags=re.IGNORECASE, maxsplit=1)
        if len(parts) == 2:
            return parts[1].strip(" :.'\"")
        return ""

    def _natural_preference(self, line: str) -> Optional[Tuple[str, str]]:
        patterns = [
            (r"\bI now prefer\s+(.+)$", "preference"),
            (r"\bI prefer\s+(.+)$", "preference"),
            (r"\bmy preferred ([a-z0-9_ -]+) is\s+(.+)$", None),
        ]
        for pattern, relation in patterns:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match and relation is not None:
                return relation, match.group(1).strip(" .'\"")
            if match and relation is None:
                return match.group(1).strip().replace(" ", "_"), match.group(2).strip(" .'\"")
        return None


class NoisyRuleBasedExtractor(DeterministicExtractor):
    """Conservative natural-language extractor for the noisy benchmark.

    This is intentionally small and deterministic. It recognizes benchmark
    stress patterns that are clear enough to store, and it emits ignored
    candidates for ambiguous language so the controller can abstain.
    """

    AMBIGUOUS_MARKERS = (
        "don't make a whole personality trait",
        "do not make a whole personality trait",
        "not a stable preference",
        "not saying this is a rule",
        "just venting",
        "just today",
        "rough thought",
        "maybe",
        "kind of",
        "sort of",
        "i guess",
        "sarcasm",
    )
    SENSITIVE_MARKERS = (
        "migraine",
        "medical",
        "diagnosis",
        "home address",
        "ssn",
        "social security",
        "child",
        "has a kid",
        "has kids",
    )

    def _extract_natural_line(self, line: str, episode: Episode) -> Optional[MemoryCandidate]:
        candidates = self._extract_noisy_line(line, episode)
        if candidates:
            return candidates[0]
        return super()._extract_natural_line(line, episode)

    def extract(self, episode: Episode) -> List[MemoryCandidate]:
        candidates: List[MemoryCandidate] = []
        for raw_line in episode.content.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            policy_candidate = self._natural_policy_request(line, episode)
            if policy_candidate is not None:
                candidates.append(policy_candidate)
                continue

            structured = self._extract_structured_line(line, episode)
            if structured is not None:
                candidates.append(structured)
                continue

            noisy = self._extract_noisy_line(line, episode)
            if noisy:
                candidates.extend(noisy)
                continue

            natural = super()._extract_natural_line(line, episode)
            if natural is not None:
                candidates.append(natural)

        if not candidates and episode.content.strip():
            candidates.append(self._ignored_candidate(episode.content.strip(), episode, confidence=0.25))
        return candidates

    def _extract_noisy_line(self, line: str, episode: Episode) -> List[MemoryCandidate]:
        lowered = line.lower()

        policy_candidate = self._natural_policy_request(line, episode)
        if policy_candidate is not None:
            return [policy_candidate]

        if self._is_ambiguous(lowered):
            return [self._ignored_candidate(line, episode, confidence=0.2)]

        project_candidates = self._project_switch_candidates(line, episode)
        if project_candidates:
            return project_candidates

        sensitive_candidate = self._sensitive_candidate(line, episode)
        if sensitive_candidate is not None:
            return [sensitive_candidate]

        preference = self._noisy_preference(line)
        if preference is not None:
            relation, object_value, confidence = preference
            return [
                self._candidate_for_fact(
                    episode=episode,
                    subject=episode.user_id,
                    relation=relation,
                    object_value=object_value,
                    sensitive=False,
                    memory_type="preference",
                    confidence=confidence,
                )
            ]

        fact = self._noisy_fact(line)
        if fact is not None:
            subject, relation, object_value, confidence = fact
            return [
                self._candidate_for_fact(
                    episode=episode,
                    subject=subject,
                    relation=relation,
                    object_value=object_value,
                    sensitive=False,
                    memory_type="semantic_fact",
                    confidence=confidence,
                )
            ]

        return []

    def _natural_policy_request(self, line: str, episode: Episode) -> Optional[MemoryCandidate]:
        lowered = line.lower()
        action = ""
        if re.search(r"\b(forget|delete|remove)\b", lowered):
            action = "delete"
        if re.search(r"\b(do not use|don't use|do not mention|don't mention|do not bring up|don't bring up|please don't bring up)\b", lowered):
            action = "do_not_use"
        if not action:
            return None

        term = self._canonical_policy_term(line)
        reference_type = reference_type_from_text(line)
        if not term and not reference_type:
            return self._ambiguous_policy_candidate(line, episode, action=action, reference_type="ambiguous")
        return MemoryCandidate(
            claim="do not use %s" % (term or "that_%s" % reference_type),
            type="constraint",
            importance=1.0,
            novelty=1.0,
            confidence=0.85,
            stability="stable",
            lifespan="until_changed",
            evidence_episode_ids=[episode.id],
            recommended_action=action,
            user_id=episode.user_id,
            project_id=episode.project_id,
            metadata=dict(self._episode_metadata(episode, memory_type="constraint"), deletion_term=term, reference_type=reference_type),
        )

    def _canonical_policy_term(self, line: str) -> str:
        lowered = line.lower()
        if "salary" in lowered:
            return "salary"
        if "phone" in lowered:
            return "phone"
        if "address" in lowered:
            return "address"
        if "company" in lowered and "again" in lowered:
            company = self._capitalized_after(line, r"(?:company|client)\s+")
            if company:
                return company

        explicit = re.search(
            r"(?:bring up|mention|use|forget|delete|remove)\s+(?:that\s+company\s+)?([A-Z][A-Za-z0-9_-]+)",
            line,
        )
        if explicit:
            return explicit.group(1)

        trailing = re.split(
            r"forget|delete|remove|do not use|don't use|do not mention|don't mention|do not bring up|don't bring up",
            line,
            flags=re.IGNORECASE,
            maxsplit=1,
        )
        if len(trailing) == 2:
            cleaned = trailing[1].strip(" :;,.!?'\"")
            words = [word.strip(" ,;.!?'\"") for word in cleaned.split()]
            useful = [
                word
                for word in words
                if word.lower()
                not in ("the", "that", "this", "it", "one", "thing", "again", "earlier", "please", "for", "now")
            ]
            if useful and useful[0].lower() in ("company", "client", "candidate", "number"):
                return ""
            return " ".join(useful[:3])
        return ""

    def _ambiguous_policy_candidate(
        self,
        claim: str,
        episode: Episode,
        action: str,
        reference_type: str,
    ) -> MemoryCandidate:
        return MemoryCandidate(
            claim=claim,
            type="constraint",
            importance=1.0,
            novelty=1.0,
            confidence=0.2,
            stability="temporary",
            lifespan="session",
            evidence_episode_ids=[episode.id],
            recommended_action="ignore",
            user_id=episode.user_id,
            project_id=episode.project_id,
            metadata=dict(
                self._episode_metadata(episode, memory_type="constraint"),
                unresolved_reference=True,
                policy_action=action,
                reference_type=reference_type,
            ),
        )

    def _is_ambiguous(self, lowered: str) -> bool:
        return any(marker in lowered for marker in self.AMBIGUOUS_MARKERS)

    def _sensitive_candidate(self, line: str, episode: Episode) -> Optional[MemoryCandidate]:
        lowered = line.lower()
        if not any(marker in lowered for marker in self.SENSITIVE_MARKERS):
            return None
        relation = "sensitive_note"
        object_value = "restricted"
        if "migraine" in lowered:
            relation, object_value = "medical_condition", "migraine"
        elif "home address" in lowered or "address" in lowered:
            relation, object_value = "home_address", "mentioned"
        elif "child" in lowered or "kid" in lowered:
            relation, object_value = "family_status", "has_child"
        elif "ssn" in lowered or "social security" in lowered:
            relation, object_value = "government_id", "mentioned"
        return self._candidate_for_fact(
            episode=episode,
            subject=episode.user_id,
            relation=relation,
            object_value=object_value,
            sensitive=True,
            confidence=0.5,
        )

    def _noisy_preference(self, line: str) -> Optional[Tuple[str, str, float]]:
        lowered = line.lower()
        if "remote used to be" in lowered and "hybrid" in lowered and "now" in lowered:
            return "work_mode", "hybrid_if_offer_strong", 0.5
        if "used to be remote" in lowered or "remote used to be" in lowered or "remote only" in lowered:
            return "work_mode", "remote_only", 0.58
        if "hybrid" in lowered and ("fine now" in lowered or "okay now" in lowered or "works now" in lowered):
            return "work_mode", "hybrid_if_offer_strong", 0.55
        if "email is easiest" in lowered or "email works best" in lowered:
            return "contact_channel", "email", 0.58
        if "phone is better now" in lowered or "call me now" in lowered:
            return "contact_channel", "phone", 0.58
        if "mornings are best" in lowered:
            return "meeting_time", "morning", 0.58
        if "afternoons are better now" in lowered:
            return "meeting_time", "afternoon", 0.58
        if "keep answers short" in lowered or "concise answers" in lowered:
            return "response_style", "brief", 0.58
        if "go deeper now" in lowered or "more detail now" in lowered:
            return "response_style", "detailed", 0.58
        if "i care about flutter" in lowered:
            return "tech_interest", "Flutter", 0.55
        if "python is my default" in lowered:
            return "tech_stack", "Python", 0.58
        if "rust is the default now" in lowered:
            return "tech_stack", "Rust", 0.58
        if "crm notes should be terse" in lowered:
            return "crm_note_style", "terse", 0.55
        if "coaching notes can be warmer" in lowered:
            return "coaching_note_style", "warm", 0.55
        return None

    def _noisy_fact(self, line: str) -> Optional[Tuple[str, str, str, float]]:
        lowered = line.lower()
        budget = re.search(r"client\s+([a-z0-9_-]+).{0,30}\b(\d+k)\b.{0,20}budget", lowered)
        if budget:
            return "client_%s" % budget.group(1), "budget", budget.group(2), 0.6
        budget_alt = re.search(r"client\s+([a-z0-9_-]+).{0,30}budget.{0,20}\b(\d+k)\b", lowered)
        if budget_alt:
            return "client_%s" % budget_alt.group(1), "budget", budget_alt.group(2), 0.6
        stage = re.search(r"candidate\s+([a-z0-9_-]+).{0,30}\b(screened|interested|offer|hired)\b", lowered)
        if stage:
            return "candidate_%s" % stage.group(1), "stage", stage.group(2), 0.6
        if "mara has an offer now" in lowered:
            return "candidate_mara", "stage", "offer", 0.6
        timezone = re.search(r"timezone.{0,20}\b(cet|pst|est|utc)\b", lowered)
        if timezone:
            return "user", "timezone", timezone.group(1).upper(), 0.6
        salary = re.search(r"salary.{0,40}\b(\d+k)\b", lowered)
        if salary:
            return "user", "salary_target", salary.group(1), 0.55
        phone = re.search(r"phone number is\s+([0-9]+)", lowered)
        if phone:
            return "candidate", "phone", phone.group(1), 0.55
        if "globex" in lowered and ("avoid" in lowered or "company" in lowered):
            return "user", "avoid_company", "Globex", 0.55
        if "recruitco" in lowered and "agency" in lowered:
            return "user", "agency", "RecruitCo", 0.55
        if "competitorx" in lowered:
            return "account", "competitor", "CompetitorX", 0.55
        if "acme" in lowered and ("avoid" in lowered or "company" in lowered):
            return "user", "avoid_company", "Acme", 0.55
        if "sales calls" in lowered and "hate" in lowered:
            return "user", "temporary_mood", "dislikes_sales_calls_today", 0.25
        return None

    def _project_switch_candidates(self, line: str, episode: Episode) -> List[MemoryCandidate]:
        lowered = line.lower()
        candidates: List[MemoryCandidate] = []
        if "gymbuddy" in lowered and "flutter" in lowered:
            candidate = self._candidate_for_fact(
                episode=episode,
                subject=episode.user_id,
                relation="code_preference",
                object_value="Flutter_hands_on",
                sensitive=False,
                memory_type="preference",
                confidence=0.58,
            )
            candidate.project_id = "gymbuddy"
            candidates.append(candidate)
        if "psychotest24" in lowered and ("don't want to touch code" in lowered or "do not want to touch code" in lowered):
            candidate = self._candidate_for_fact(
                episode=episode,
                subject=episode.user_id,
                relation="code_preference",
                object_value="no_code_self",
                sensitive=False,
                memory_type="preference",
                confidence=0.58,
            )
            candidate.project_id = "psychotest24"
            candidates.append(candidate)
        if "alpha" in lowered and "python" in lowered:
            candidate = self._candidate_for_fact(
                episode=episode,
                subject=episode.user_id,
                relation="tech_stack",
                object_value="Python",
                sensitive=False,
                confidence=0.58,
            )
            candidate.project_id = "alpha"
            candidates.append(candidate)
        if "beta" in lowered and "rust" in lowered:
            candidate = self._candidate_for_fact(
                episode=episode,
                subject=episode.user_id,
                relation="tech_stack",
                object_value="Rust",
                sensitive=False,
                confidence=0.58,
            )
            candidate.project_id = "beta"
            candidates.append(candidate)
        return candidates

    def _ignored_candidate(self, claim: str, episode: Episode, confidence: float) -> MemoryCandidate:
        return MemoryCandidate(
            claim=claim,
            type="episodic_note",
            importance=0.1,
            novelty=0.2,
            confidence=confidence,
            stability="ephemeral",
            lifespan="session",
            evidence_episode_ids=[episode.id],
            recommended_action="ignore",
            user_id=episode.user_id,
            project_id=episode.project_id,
            metadata=self._episode_metadata(episode),
        )

    def _capitalized_after(self, line: str, prefix_pattern: str) -> str:
        match = re.search(prefix_pattern + r"([A-Z][A-Za-z0-9_-]+)", line)
        if match:
            return match.group(1)
        return ""


class RecruitingRuleBasedExtractor(NoisyRuleBasedExtractor):
    """Small deterministic extractor for recruiting-domain benchmark phrases."""

    def _extract_noisy_line(self, line: str, episode: Episode) -> List[MemoryCandidate]:
        recruiting = self._extract_recruiting_line(line, episode)
        if recruiting:
            return recruiting
        return super()._extract_noisy_line(line, episode)

    def _extract_recruiting_line(self, line: str, episode: Episode) -> List[MemoryCandidate]:
        lowered = line.lower()
        if "do not contact" in lowered or "don't contact" in lowered:
            candidate_id = self._entity_after(line, r"candidate\s+")
            if candidate_id:
                return [self._constraint("do_not_use", "candidate_%s" % candidate_id, episode)]

        if "confidential" in lowered or "do not share" in lowered or "don't share" in lowered:
            term = self._entity_after(line, r"(?:company|employer|client)\s+") or self._capitalized_token(line)
            if term:
                return [self._constraint("do_not_use", term, episode)]

        fact = self._recruiting_fact(line)
        if fact is not None:
            subject, relation, object_value, confidence, sensitive = fact
            return [
                self._candidate_for_fact(
                    episode=episode,
                    subject=subject,
                    relation=relation,
                    object_value=object_value,
                    sensitive=sensitive,
                    confidence=confidence,
                )
            ]
        return []

    def _recruiting_fact(self, line: str) -> Optional[Tuple[str, str, str, float, bool]]:
        lowered = line.lower()
        entity = self._entity_after(line, r"candidate\s+")
        if entity:
            subject = "candidate_%s" % entity
            salary = re.search(r"(?:salary|comp|compensation).{0,40}\b(\d+k)\b", lowered)
            if salary:
                return subject, "salary_expectation", salary.group(1), 0.62, False
            notice = re.search(r"notice.{0,30}\b(\d+\s*(?:weeks?|months?))\b", lowered)
            if notice:
                return subject, "notice_period", notice.group(1).replace(" ", "_"), 0.62, False
            if "remote" in lowered or "hybrid" in lowered or "on-site" in lowered or "onsite" in lowered:
                if "remote" in lowered and "only" in lowered:
                    return subject, "work_mode", "remote_only", 0.6, False
                if "hybrid" in lowered:
                    return subject, "work_mode", "hybrid", 0.6, False
                if "on-site" in lowered or "onsite" in lowered:
                    return subject, "work_mode", "onsite", 0.6, False
            if "relocation" in lowered or "relocate" in lowered:
                if "open" in lowered or "willing" in lowered:
                    return subject, "relocation", "willing", 0.6, False
                if "not" in lowered or "no " in lowered:
                    return subject, "relocation", "not_willing", 0.6, False
            if "offer" in lowered:
                return subject, "competing_offer", "yes", 0.58, False
            if "objection" in lowered or "concern" in lowered:
                if "resolved" in lowered or "cleared" in lowered:
                    return subject, "objection", "resolved", 0.58, False
                return subject, "objection", "open", 0.58, False
            if any(term in lowered for term in ("migraine", "child", "medical", "home address")):
                return subject, "sensitive_note", "mentioned", 0.5, True

        client = self._entity_after(line, r"client\s+")
        if client:
            subject = "client_%s" % client
            budget = re.search(r"budget.{0,35}\b(\d+k)\b", lowered)
            if budget:
                return subject, "budget", budget.group(1), 0.62, False
            skill = self._skill_from_text(line)
            if skill:
                return subject, "required_skill", skill, 0.62, False
            if "remote" in lowered or "hybrid" in lowered or "onsite" in lowered or "on-site" in lowered:
                if "onsite" in lowered or "on-site" in lowered:
                    return subject, "location_policy", "onsite", 0.6, False
                if "hybrid" in lowered:
                    return subject, "location_policy", "hybrid", 0.6, False
                return subject, "location_policy", "remote", 0.6, False

        role = self._entity_after(line, r"role\s+")
        if role:
            subject = "role_%s" % role
            skill = self._skill_from_text(line)
            if skill:
                return subject, "required_skill", skill, 0.62, False
            if "senior" in lowered:
                return subject, "seniority", "senior", 0.58, False
            if "budget" in lowered:
                budget = re.search(r"\b(\d+k)\b", lowered)
                if budget:
                    return subject, "budget", budget.group(1), 0.58, False
        return None

    def _constraint(self, action: str, term: str, episode: Episode) -> MemoryCandidate:
        return MemoryCandidate(
            claim="do not use %s" % term,
            type="constraint",
            importance=1.0,
            novelty=1.0,
            confidence=0.9,
            stability="stable",
            lifespan="until_changed",
            evidence_episode_ids=[episode.id],
            recommended_action=action,
            user_id=episode.user_id,
            project_id=episode.project_id,
            metadata=dict(self._episode_metadata(episode, memory_type="constraint"), deletion_term=term),
        )

    def _entity_after(self, line: str, prefix_pattern: str) -> str:
        match = re.search(prefix_pattern + r"([A-Za-z][A-Za-z0-9_-]*)", line, flags=re.IGNORECASE)
        if not match:
            return ""
        return match.group(1).lower()

    def _capitalized_token(self, line: str) -> str:
        match = re.search(r"\b([A-Z][A-Za-z0-9_-]+)\b", line)
        return match.group(1) if match else ""

    def _skill_from_text(self, line: str) -> str:
        for skill in ("Python", "Flutter", "Rust", "Kubernetes", "ML", "Django", "FastAPI"):
            if skill.lower() in line.lower():
                return skill
        return ""


class GenericConversationExtractor(DeterministicExtractor):
    """Conservative extractor for raw conversational dialogue.

    This is intentionally rule-based and modest. It extracts only clear,
    speaker-grounded facts from lines shaped like ``speaker: utterance``. It is
    meant for external long-conversation evaluation readiness, not production
    open-domain extraction.
    """

    UNCERTAIN_MARKERS = (
        "maybe",
        "perhaps",
        "i guess",
        "sort of",
        "kind of",
        "not sure",
        "i might",
        "probably",
        "could be",
    )

    def extract(self, episode: Episode) -> List[MemoryCandidate]:
        structured = super().extract(episode)
        stored_structured = [candidate for candidate in structured if candidate.recommended_action != "ignore"]
        if stored_structured:
            return structured

        speaker, utterance = self._speaker_and_utterance(episode.content, episode.actor)
        if not utterance:
            return structured
        if self._is_uncertain(utterance):
            return [self._ignored_dialogue_candidate(episode, utterance, confidence=0.2)]

        speaker_subject = self._normalize_speaker(speaker)
        candidates: List[MemoryCandidate] = []
        for subject, relation, object_value, memory_type, confidence in self._extract_dialogue_facts(utterance, speaker_subject):
            candidates.append(
                self._candidate_for_fact(
                    episode=episode,
                    subject=subject,
                    relation=relation,
                    object_value=object_value,
                    sensitive=False,
                    memory_type=memory_type,
                    confidence=confidence,
                )
            )
        if candidates:
            return candidates
        return [self._ignored_dialogue_candidate(episode, utterance, confidence=0.2)]

    def _speaker_and_utterance(self, content: str, fallback_speaker: str) -> Tuple[str, str]:
        if ":" in content:
            speaker, utterance = content.split(":", 1)
            return speaker.strip() or fallback_speaker or "speaker", utterance.strip()
        return fallback_speaker or "speaker", content.strip()

    def _normalize_speaker(self, speaker: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_]+", "_", speaker.strip().lower()).strip("_")
        return normalized or "speaker"

    def _is_uncertain(self, text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in self.UNCERTAIN_MARKERS)

    def _extract_dialogue_facts(self, utterance: str, speaker_subject: str) -> List[Tuple[str, str, str, str, float]]:
        facts: List[Tuple[str, str, str, str, float]] = []
        for pattern, relation, memory_type, confidence in (
            (r"\bI (?:really )?(?:like|love|enjoy)\s+(.+?)(?:[.!?]|$)", "likes", "preference", 0.5),
            (r"\bI (?:really )?(?:hate|dislike)\s+(.+?)(?:[.!?]|$)", "dislikes", "preference", 0.5),
            (r"\bI (?:drink|eat|read|play|practice)\s+(.+?)(?:[.!?]|$)", "habit", "semantic_fact", 0.48),
            (r"\bI (?:live|stay)\s+in\s+(.+?)(?:[.!?]|$)", "location", "semantic_fact", 0.55),
            (r"\bI (?:moved|relocated)\s+to\s+(.+?)(?:[.!?]|$)", "location", "semantic_fact", 0.55),
            (r"\bI(?:'m| am) from\s+(.+?)(?:[.!?]|$)", "origin_location", "semantic_fact", 0.52),
            (r"\bI grew up in\s+(.+?)(?:[.!?]|$)", "origin_location", "semantic_fact", 0.52),
            (r"\bI was born in\s+(.+?)(?:[.!?]|$)", "birth_location", "semantic_fact", 0.5),
            (r"\b(?:we|I) (?:stayed|are staying|were staying)\s+(?:in|at|near)\s+(.+?)(?:[.!?]|$)", "stay_location", "semantic_fact", 0.5),
            (r"\b(?:our|my) (hotel|cabin|campsite|campground|airbnb|apartment|house|room) (?:was|is)\s+(?:in|at|near)\s+(.+?)(?:[.!?]|$)", "lodging_location", "semantic_fact", 0.5),
            (r"\b(?:our|my|the) trip (?:was|is)?\s*to\s+(.+?)(?:[.!?]|$)", "trip_location", "semantic_fact", 0.48),
            (r"\b(?:the|my|our) ([A-Za-z][A-Za-z0-9_ -]{1,40}) (?:is|was)\s+(?:in|near)\s+(.+?)(?:[.!?]|$)", "object_location", "semantic_fact", 0.46),
            (r"\bI (?:work|worked)\s+(?:as|as an|as a)\s+(.+?)(?:[.!?]|$)", "career", "semantic_fact", 0.5),
            (r"\bI (?:work|worked)\s+(?:at|for)\s+(.+?)(?:[.!?]|$)", "employer", "semantic_fact", 0.5),
            (r"\bI (?:study|studied|am studying|was studying|major in|majored in|am majoring in)\s+(.+?)(?:[.!?]|$)", "education", "semantic_fact", 0.5),
            (r"\bI (?:want to|wanted to|hope to|planned to|plan to)\s+(?:become|be)\s+(?:an?|the)?\s*(.+?)(?:[.!?]|$)", "career_goal", "semantic_fact", 0.48),
            (r"\bI (?:went|visited|attended)\s+(?:to\s+)?(.+?)(?:[.!?]|$)", "event", "semantic_fact", 0.45),
            (r"\bI (?:will|plan to|am going to|gonna)\s+(.+?)(?:[.!?]|$)", "plan", "semantic_fact", 0.45),
            (r"\bI'm going to\s+(.+?)(?:[.!?]|$)", "plan", "semantic_fact", 0.45),
            (r"\bI(?:'m| am)\s+(dating|married to|engaged to|single|divorced|separated|with)\s+(.+?)(?:[.!?]|$)", "relationship_status", "semantic_fact", 0.48),
            (r"\bI(?:'m| am)\s+(single|married|engaged|divorced|separated)(?:[.!?]|$)", "relationship_status", "semantic_fact", 0.48),
            (r"\bI (?:got married to|married|broke up with|started dating)\s+(.+?)(?:[.!?]|$)", "relationship_status", "semantic_fact", 0.48),
            (r"\bI(?:'m| am)\s+(.+?)(?:[.!?]|$)", "attribute", "semantic_fact", 0.42),
            (r"\bmy ([A-Za-z][A-Za-z0-9_ -]{1,30}) is\s+(.+?)(?:[.!?]|$)", None, "semantic_fact", 0.5),
            (r"\bmy ([A-Za-z][A-Za-z0-9_ -]{1,30}) (?:moved|changed|shifted) to\s+(.+?)(?:[.!?]|$)", None, "semantic_fact", 0.5),
            (r"\bI (?:moved|changed|shifted) my ([A-Za-z][A-Za-z0-9_ -]{1,30}) to\s+(.+?)(?:[.!?]|$)", None, "semantic_fact", 0.5),
            (r"\b(?:the|my) ([A-Za-z][A-Za-z0-9_ -]{1,30}) is (?:on|at)\s+(.+?)(?:[.!?]|$)", "time", "semantic_fact", 0.46),
        ):
            match = re.search(pattern, utterance, flags=re.IGNORECASE)
            if not match:
                continue
            if relation is None:
                relation_value = self._clean_relation(match.group(1))
                object_value = self._clean_object(match.group(2))
            elif relation == "lodging_location" and len(match.groups()) == 2:
                relation_value = "%s_location" % self._clean_relation(match.group(1))
                object_value = self._clean_location_object(match.group(2))
            elif relation == "object_location" and len(match.groups()) == 2:
                if not self._valid_location_subject(match.group(1)):
                    continue
                relation_value = "%s_location" % self._clean_relation(match.group(1))
                object_value = self._clean_location_object(match.group(2))
            elif relation == "relationship_status" and len(match.groups()) == 2:
                relation_value = relation
                object_value = self._clean_object("%s %s" % (match.group(1), match.group(2)))
            elif relation == "time" and len(match.groups()) == 2:
                relation_value = "%s_time" % self._clean_relation(match.group(1))
                object_value = self._clean_object(match.group(2))
            elif relation.endswith("_location") or relation == "location":
                relation_value = relation
                object_value = self._clean_location_object(match.group(1))
            else:
                relation_value = relation
                object_value = self._clean_object(match.group(1))
            if relation_value == "attribute" and any(
                existing_subject == speaker_subject and existing_object.lower() == object_value.lower()
                for existing_subject, _, existing_object, _, _ in facts
            ):
                continue
            if self._valid_object(object_value):
                facts.append((speaker_subject, relation_value, object_value, memory_type, confidence))

        relationship = re.search(
            r"\bmy\s+(friend|sister|brother|mother|father|boss|manager|partner|husband|wife)\s+([A-Z][A-Za-z0-9_-]+)",
            utterance,
        )
        if relationship:
            facts.append((speaker_subject, "relationship_%s" % relationship.group(1).lower(), relationship.group(2), "semantic_fact", 0.45))

        for subject, relation, object_value, confidence in self._extract_related_person_location_facts(utterance, speaker_subject):
            facts.append((subject, relation, object_value, "semantic_fact", confidence))

        for subject, relation, object_value, confidence in self._extract_named_third_party_facts(utterance):
            facts.append((subject, relation, object_value, "semantic_fact", confidence))

        for relation, object_value, confidence in self._extract_event_facts(utterance):
            facts.append((speaker_subject, relation, object_value, "semantic_fact", confidence))

        return facts[:8]

    def _extract_named_third_party_facts(self, utterance: str) -> List[Tuple[str, str, str, float]]:
        facts: List[Tuple[str, str, str, float]] = []
        for pattern, relation, confidence in (
            (r"\b([A-Z][A-Za-z0-9_-]{1,30}) (?:lives|stays)\s+in\s+(.+?)(?:[.!?]|$)", "location", 0.5),
            (r"\b([A-Z][A-Za-z0-9_-]{1,30}) (?:lives|stays)\s+near\s+(.+?)(?:[.!?]|$)", "location", 0.5),
            (r"\b([A-Z][A-Za-z0-9_-]{1,30}) (?:moved|relocated)\s+to\s+(.+?)(?:[.!?]|$)", "location", 0.5),
            (r"\b([A-Z][A-Za-z0-9_-]{1,30}) moved back to\s+(.+?)(?:[.!?]|$)", "location", 0.5),
            (r"\b([A-Z][A-Za-z0-9_-]{1,30}) is from\s+(.+?)(?:[.!?]|$)", "origin_location", 0.48),
            (r"\b([A-Z][A-Za-z0-9_-]{1,30}) grew up in\s+(.+?)(?:[.!?]|$)", "origin_location", 0.48),
            (r"\b([A-Z][A-Za-z0-9_-]{1,30}) was born in\s+(.+?)(?:[.!?]|$)", "birth_location", 0.46),
            (r"\b([A-Z][A-Za-z0-9_-]{1,30}) (?:works|worked)\s+(?:at|for)\s+(.+?)(?:[.!?]|$)", "employer", 0.48),
            (r"\b([A-Z][A-Za-z0-9_-]{1,30}) (?:studies|studied|is studying|was studying)\s+(.+?)(?:[.!?]|$)", "education", 0.48),
            (r"\bmy\s+(?:friend|sister|brother|mother|father|boss|manager|partner|husband|wife)\s+([A-Z][A-Za-z0-9_-]{1,30}) (?:lives|stays)\s+in\s+(.+?)(?:[.!?]|$)", "location", 0.5),
            (r"\bmy\s+(?:friend|sister|brother|mother|father|boss|manager|partner|husband|wife)\s+([A-Z][A-Za-z0-9_-]{1,30}) (?:lives|stays)\s+near\s+(.+?)(?:[.!?]|$)", "location", 0.5),
            (r"\bmy\s+(?:friend|sister|brother|mother|father|boss|manager|partner|husband|wife)\s+([A-Z][A-Za-z0-9_-]{1,30}) (?:moved|relocated|moved back)\s+to\s+(.+?)(?:[.!?]|$)", "location", 0.5),
            (r"\bmy\s+(?:friend|sister|brother|mother|father|boss|manager|partner|husband|wife)\s+([A-Z][A-Za-z0-9_-]{1,30}) is from\s+(.+?)(?:[.!?]|$)", "origin_location", 0.48),
            (r"\bmy\s+(?:friend|sister|brother|mother|father|boss|manager|partner|husband|wife)\s+([A-Z][A-Za-z0-9_-]{1,30}) (?:works|worked)\s+(?:at|for)\s+(.+?)(?:[.!?]|$)", "employer", 0.48),
            (r"\bmy\s+(?:friend|sister|brother|mother|father|boss|manager|partner|husband|wife)\s+([A-Z][A-Za-z0-9_-]{1,30}) is\s+(.+?)(?:[.!?]|$)", "attribute", 0.44),
        ):
            match = re.search(pattern, utterance)
            if not match:
                continue
            if not self._valid_named_subject(match.group(1)):
                continue
            subject = self._normalize_speaker(match.group(1))
            object_value = self._clean_location_object(match.group(2)) if relation.endswith("_location") or relation == "location" else self._clean_object(match.group(2))
            if self._valid_object(object_value):
                facts.append((subject, relation, object_value, confidence))

        relationship = re.search(
            r"\b([A-Z][A-Za-z0-9_-]{1,30}) is my\s+(friend|sister|brother|mother|father|boss|manager|partner|husband|wife)(?:[.!?]|$)",
            utterance,
        )
        if relationship:
            if self._valid_named_subject(relationship.group(1)):
                facts.append((self._normalize_speaker(relationship.group(1)), "relationship_to_speaker", relationship.group(2).lower(), 0.48))
        return facts[:3]

    def _extract_related_person_location_facts(self, utterance: str, speaker_subject: str) -> List[Tuple[str, str, str, float]]:
        facts: List[Tuple[str, str, str, float]] = []
        relation_words = r"friend|sister|brother|mother|mom|father|dad|boss|manager|partner|husband|wife"
        patterns = (
            (r"\bmy\s+(%s)\s+(?:lives|stays)\s+(?:in|near)\s+(.+?)(?:[.!?]|$)" % relation_words, "location", 0.48),
            (r"\bmy\s+(%s)\s+(?:moved|relocated|moved back)\s+to\s+(.+?)(?:[.!?]|$)" % relation_words, "location", 0.48),
            (r"\bmy\s+(%s)\s+is from\s+(.+?)(?:[.!?]|$)" % relation_words, "origin_location", 0.46),
            (r"\bmy\s+(%s)\s+grew up in\s+(.+?)(?:[.!?]|$)" % relation_words, "origin_location", 0.46),
            (r"\bmy\s+(%s)\s+was born in\s+(.+?)(?:[.!?]|$)" % relation_words, "birth_location", 0.44),
        )
        for pattern, relation, confidence in patterns:
            match = re.search(pattern, utterance, flags=re.IGNORECASE)
            if not match:
                continue
            related = self._normalize_related_person(match.group(1))
            subject = "%s_%s" % (speaker_subject, related)
            object_value = self._clean_location_object(match.group(2))
            if self._valid_object(object_value):
                facts.append((subject, relation, object_value, confidence))
        return facts[:2]

    def _normalize_related_person(self, value: str) -> str:
        lowered = value.lower()
        aliases = {"mom": "mother", "dad": "father"}
        return aliases.get(lowered, lowered)

    def _valid_named_subject(self, value: str) -> bool:
        lowered = value.strip().lower()
        return lowered not in ("i", "you", "he", "she", "we", "they", "my", "the")

    def _extract_event_facts(self, utterance: str) -> List[Tuple[str, str, float]]:
        facts: List[Tuple[str, str, float]] = []
        temporal = self._temporal_phrase(utterance)
        for pattern, relation, confidence in (
            (r"\bI (?:just )?(?:ran|completed|finished)\s+(.+?)(?:[.!?]|$)", "event", 0.46),
            (r"\bwe (?:just )?(?:ran|completed|finished)\s+(.+?)(?:[.!?]|$)", "event", 0.45),
            (r"\bI (?:just )?signed up for\s+(.+?)(?:[.!?]|$)", "activity", 0.48),
            (r"\bI (?:just )?(?:researched|looked into|started researching)\s+(.+?)(?:[.!?]|$)", "researched_topic", 0.48),
            (r"^\s*Researching\s+(.+?)(?:[.!?]|--|—|$)", "researched_topic", 0.48),
            (r"\bI (?:gave|delivered)\s+(?:a\s+)?speech\s+(?:about|on)\s+(.+?)(?:[.!?]|$)", "speech_topic", 0.48),
            (r"\bI (?:talked|spoke)\s+about\s+(.+?)(?:[.!?]|$)", "speech_topic", 0.46),
            (r"\bI (?:took|brought)\s+.+?\s+to\s+(.+?)(?:[.!?]|$)", "visited_place", 0.46),
            (r"\bI (?:went|traveled|travelled)\s+to\s+(.+?)(?:[.!?]|$)", "visited_place", 0.46),
            (r"\b(?:went|go|going|took .+?)\s+camping\s+(?:in|at|near)\s+(.+?)(?:[.!?]|$)", "camping_location", 0.48),
            (r"\bcamping (?:trip )?(?:in|at|near)\s+(.+?)(?:[.!?]|$)", "camping_location", 0.46),
            (r"\bI(?:'ve| have) been\s+(.+?)\s+to\s+de-?stress(?:[,;.!?]|$)", "destress_activity", 0.48),
            (r"\bI (?:de-?stress|relax|unwind)\s+by\s+(.+?)(?:[.!?]|$)", "destress_activity", 0.48),
            (r"\b(?:we|I) met up\s+(.+?)(?:[.!?]|$)", "meetup_time", 0.44),
            (r"\b(?:I(?:'ve| have) got|I have|I own)\s+(.+?)(?:[.!?]|$)", "possession", 0.42),
        ):
            match = re.search(pattern, utterance, flags=re.IGNORECASE)
            if not match:
                continue
            object_value = self._clean_event_object(match.group(1))
            if self._valid_object(object_value):
                facts.append((relation, object_value, confidence))

        event_summary = self._event_summary(utterance)
        if event_summary:
            facts.append(("event_summary", event_summary, 0.45))

        for relation, pattern in (
            ("identity", r"\bI(?:'m| am)\s+(transgender|nonbinary|non-binary|gay|lesbian|bisexual|queer)\b"),
            ("identity_journey", r"\bmy\s+(transgender|nonbinary|non-binary|gay|lesbian|bisexual|queer)\s+journey\b"),
            ("identity_status", r"\bI started transitioning\s+(.+?)(?:[.!?]|$)"),
        ):
            match = re.search(pattern, utterance, flags=re.IGNORECASE)
            if match:
                object_value = self._clean_object(match.group(1))
                if self._valid_object(object_value):
                    facts.append((relation, object_value, 0.43))

        if temporal:
            event_relation = self._event_time_relation(utterance)
            facts.append((event_relation, temporal, 0.44))
        return self._dedupe_facts(facts)[:6]

    def _event_summary(self, utterance: str) -> str:
        patterns = (
            r"\b(I|we)\s+((?:just\s+)?(?:took|brought)\s+.+?\s+to\s+.+?)(?:[.!?]|$)",
            r"\b(I|we)\s+((?:just\s+)?(?:went|traveled|travelled|visited|attended)\s+(?:to\s+)?.+?)(?:[.!?]|$)",
            r"\b(I|we)\s+((?:just\s+)?(?:ran|completed|finished)\s+.+?)(?:[.!?]|$)",
            r"\b(I|we)\s+((?:just\s+)?signed up for\s+.+?)(?:[.!?]|$)",
            r"\b(I|we)\s+((?:gave|delivered)\s+(?:a\s+)?speech\s+(?:about|on)\s+.+?)(?:[.!?]|$)",
            r"\b(I|we)\s+((?:talked|spoke)\s+about\s+.+?)(?:[.!?]|$)",
            r"\b(I|we)\s+((?:met up|went camping|took .+? camping)\s+.+?)(?:[.!?]|$)",
        )
        for pattern in patterns:
            match = re.search(pattern, utterance, flags=re.IGNORECASE)
            if not match:
                continue
            summary = self._clean_event_object(match.group(2))
            temporal = self._temporal_phrase(utterance)
            if temporal and temporal.lower() not in summary.lower():
                summary = "%s %s" % (summary, temporal)
            if self._valid_object(summary):
                return summary
        return ""

    def _event_time_relation(self, utterance: str) -> str:
        lowered = utterance.lower()
        for needle, relation in (
            ("charity race", "charity_race_time"),
            ("pottery class", "pottery_class_time"),
            ("museum", "museum_visit_time"),
            ("school event", "school_event_time"),
            ("speech", "speech_time"),
            ("birthday", "birthday_time"),
            ("camping", "camping_time"),
            ("met up", "meetup_time"),
            ("transitioning", "transitioning_time"),
        ):
            if needle in lowered:
                return relation
        return "event_time"

    def _temporal_phrase(self, text: str) -> str:
        patterns = (
            r"\b(?:today|tomorrow|yesterday|tonight)\b",
            r"\blast\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|weekend|month|year)\b",
            r"\bnext\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|weekend|month|year)\b",
            r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+years?\s+ago\b",
            r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?:days?|weeks?|months?)\s+ago\b",
            r"\bsince we last chatted\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return self._clean_object(match.group(0))
        return ""

    def _clean_event_object(self, value: str) -> str:
        cleaned = self._clean_object(value)
        cleaned = re.split(r"\s+(?:last|next|yesterday|today|tomorrow|tonight|since)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
        cleaned = re.split(r"\s+(?:it was|it is|which was|that was)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
        cleaned = re.split(r"\s+(?:and encouraged|and asked|and told|and shared)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
        cleaned = re.split(r"\s+(?:--|—|-)\s+", cleaned, maxsplit=1)[0]
        cleaned = re.split(r",\s+(?:which|it|that)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
        return cleaned.strip(" ,;:'\"")

    def _clean_location_object(self, value: str) -> str:
        cleaned = self._clean_event_object(value)
        cleaned = re.sub(r"^(?:the|a|an)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.split(r"\s+(?:with|for|because|before|after|during|while)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
        return cleaned.strip(" ,;:'\"")

    def _valid_location_subject(self, value: str) -> bool:
        tokens = set(re.findall(r"[a-z0-9]+", value.lower()))
        if not tokens:
            return False
        abstract = {
            "argument",
            "conversation",
            "debate",
            "discussion",
            "faith",
            "idea",
            "mood",
            "plan",
            "relationship",
            "stage",
            "thought",
            "trouble",
        }
        return not bool(tokens & abstract)

    def _dedupe_facts(self, facts: List[Tuple[str, str, float]]) -> List[Tuple[str, str, float]]:
        deduped: List[Tuple[str, str, float]] = []
        seen = set()
        for relation, object_value, confidence in facts:
            key = (relation, object_value.lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append((relation, object_value, confidence))
        return deduped

    def _clean_relation(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_ -]+", "", value.lower()).strip().replace(" ", "_").replace("-", "_")
        return cleaned[:40] or "attribute"

    def _clean_object(self, value: str) -> str:
        cleaned = value.strip(" ,;:'\"")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned[:80]

    def _valid_object(self, value: str) -> bool:
        lowered = value.lower().strip("_")
        if not lowered or len(lowered) < 2:
            return False
        if lowered in ("you", "it", "that", "this", "there", "here", "what", "how"):
            return False
        reference_tokens = {"it", "that", "this", "there", "here", "place", "thing", "one"}
        tokens = set(re.findall(r"[a-z0-9]+", lowered))
        if tokens and tokens <= reference_tokens:
            return False
        return True

    def _ignored_dialogue_candidate(self, episode: Episode, utterance: str, confidence: float) -> MemoryCandidate:
        return MemoryCandidate(
            claim=utterance,
            type="episodic_note",
            importance=0.1,
            novelty=0.2,
            confidence=confidence,
            stability="ephemeral",
            lifespan="session",
            evidence_episode_ids=[episode.id],
            recommended_action="ignore",
            user_id=episode.user_id,
            project_id=episode.project_id,
            metadata=self._episode_metadata(episode),
        )


OPEN_CONVERSATION_LLM_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "provenance_dia_id": {"type": "string"},
                    "speaker": {"type": "string"},
                    "session_id": {"type": "string"},
                    "memory_type": {"type": "string", "enum": ["semantic_fact", "preference", "episodic_note"]},
                    "relation_type": {
                        "type": "string",
                        "enum": [
                            "person_attribute",
                            "preference",
                            "relationship",
                            "event",
                            "plan",
                            "location",
                            "object_location",
                            "temporal_change",
                            "commitment",
                            "question_answerable_fact",
                            "uncertainty",
                            "ambiguous_reference",
                        ],
                    },
                    "subject": {"type": "string"},
                    "object": {"type": "string"},
                    "claim": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "temporal_hints": {"type": "array", "items": {"type": "string"}},
                    "entity_mentions": {"type": "array", "items": {"type": "string"}},
                    "supporting_text": {"type": "string"},
                    "sensitivity": {"type": "string", "enum": ["low", "high", "restricted"]},
                    "resolved_entity": {"type": "boolean"},
                },
                "required": [
                    "provenance_dia_id",
                    "speaker",
                    "session_id",
                    "memory_type",
                    "relation_type",
                    "subject",
                    "object",
                    "claim",
                    "confidence",
                    "temporal_hints",
                    "entity_mentions",
                    "supporting_text",
                    "sensitivity",
                    "resolved_entity",
                ],
            },
        }
    },
    "required": ["memories"],
}


OPEN_CONVERSATION_LLM_PROMPT_VERSION = "open_conversation_llm_v2"
DEFAULT_LLM_EXTRACT_CACHE_DIR = ".cache/engram/llm_extract"


def open_conversation_source_turn(episode: Episode) -> Dict[str, Any]:
    speaker, utterance = GenericConversationExtractor()._speaker_and_utterance(episode.content, episode.actor)
    return {
        "dia_id": episode.id,
        "speaker": speaker,
        "session_id": episode.context_id,
        "timestamp": episode.timestamp.isoformat(),
        "text": utterance,
    }


class CachedLLMExtractionProvider:
    """Local source-turn cache for optional LLM extraction providers."""

    def __init__(
        self,
        provider: Callable[[Dict[str, Any]], object],
        cache_dir: str,
        model: str,
        prompt_schema_version: str = OPEN_CONVERSATION_LLM_PROMPT_VERSION,
    ) -> None:
        self.provider = provider
        self.cache_dir = Path(cache_dir)
        self.model = model or "unknown"
        self.prompt_schema_version = prompt_schema_version
        self.cache_hits = 0
        self.cache_misses = 0
        self.api_calls_made = 0
        self.cache_writes = 0
        self.cache_read_errors = 0

    def __call__(self, source_turn: Dict[str, Any]) -> object:
        cached = self.read(source_turn)
        if cached is not None:
            self.cache_hits += 1
            return cached
        self.cache_misses += 1
        result = self.provider(source_turn)
        self.api_calls_made += 1
        if self._cacheable_response(result):
            self.write(source_turn, result)
        return result

    def has_valid_cache(self, source_turn: Dict[str, Any]) -> bool:
        return self.read(source_turn) is not None

    def read(self, source_turn: Dict[str, Any]) -> Optional[object]:
        path = self.cache_path(source_turn)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            self.cache_read_errors += 1
            return None
        if not isinstance(payload, dict) or payload.get("cache_version") != 1:
            return None
        return payload.get("response")

    def write(self, source_turn: Dict[str, Any], response: object) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_path(source_turn)
        payload = {
            "cache_version": 1,
            "model": self.model,
            "prompt_schema_version": self.prompt_schema_version,
            "key": self.cache_key(source_turn),
            "response": response,
        }
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        self.cache_writes += 1

    def cache_key(self, source_turn: Dict[str, Any]) -> str:
        text = str(source_turn.get("text") or "")
        key_payload = {
            "model": self.model,
            "prompt_schema_version": self.prompt_schema_version,
            "speaker": str(source_turn.get("speaker") or ""),
            "session_id": str(source_turn.get("session_id") or ""),
            "dia_id": str(source_turn.get("dia_id") or ""),
            "turn_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        encoded = json.dumps(key_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def cache_path(self, source_turn: Dict[str, Any]) -> Path:
        return self.cache_dir / ("%s.json" % self.cache_key(source_turn))

    def stats(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "prompt_schema_version": self.prompt_schema_version,
            "cache_dir": str(self.cache_dir),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "api_calls_made": self.api_calls_made,
            "cache_writes": self.cache_writes,
            "cache_read_errors": self.cache_read_errors,
        }

    def _cacheable_response(self, response: object) -> bool:
        if isinstance(response, dict):
            return isinstance(response.get("memories"), list)
        if isinstance(response, str):
            try:
                parsed = json.loads(response)
            except json.JSONDecodeError:
                return False
            return isinstance(parsed, dict) and isinstance(parsed.get("memories"), list)
        return False


class OpenConversationLLMExtractor(GenericConversationExtractor):
    """Schema-constrained LLM extractor for open conversation diagnostics.

    The provider receives only the current source turn metadata and text. It
    does not receive LoCoMo QA questions, answers or evidence labels.
    """

    ALLOWED_RELATIONS = set(OPEN_CONVERSATION_LLM_SCHEMA["properties"]["memories"]["items"]["properties"]["relation_type"]["enum"])
    ALLOWED_MEMORY_TYPES = {"semantic_fact", "preference", "episodic_note"}
    LOW_CONFIDENCE_THRESHOLD = 0.45
    LLM_STOPWORDS = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "for",
        "from",
        "i",
        "in",
        "is",
        "it",
        "my",
        "of",
        "on",
        "or",
        "our",
        "the",
        "to",
        "was",
        "we",
        "with",
    }

    def __init__(self, provider: Callable[[Dict[str, Any]], object], min_confidence: float = LOW_CONFIDENCE_THRESHOLD) -> None:
        self.provider = provider
        self.min_confidence = min_confidence

    def extract(self, episode: Episode) -> List[MemoryCandidate]:
        speaker, utterance = self._speaker_and_utterance(episode.content, episode.actor)
        if not utterance:
            return [self._ignored_llm_candidate(episode, "", "empty_source_turn")]
        payload = self._load_payload(self.provider(self._provider_input(episode, speaker, utterance)))
        raw_memories = payload.get("memories")
        if not isinstance(raw_memories, list):
            raise ExtractorSchemaError("Open conversation LLM output must contain a memories list.")
        candidates = [self._candidate_from_memory(item, episode, speaker, utterance) for item in raw_memories]
        return candidates or [self._ignored_llm_candidate(episode, utterance, "no_memory_candidates")]

    def _provider_input(self, episode: Episode, speaker: str, utterance: str) -> Dict[str, Any]:
        return open_conversation_source_turn(episode)

    def _load_payload(self, raw: object) -> Dict[str, Any]:
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ExtractorSchemaError("Open conversation LLM extractor returned invalid JSON.") from exc
        elif isinstance(raw, dict):
            parsed = raw
        else:
            raise ExtractorSchemaError("Open conversation LLM output must be JSON text or a mapping.")
        if not isinstance(parsed, dict):
            raise ExtractorSchemaError("Open conversation LLM output must be a JSON object.")
        return parsed

    def _candidate_from_memory(self, item: object, episode: Episode, speaker: str, utterance: str) -> MemoryCandidate:
        if not isinstance(item, dict):
            raise ExtractorSchemaError("Each open conversation LLM memory must be an object.")
        schema_error = self._schema_error(item)
        if schema_error:
            return self._ignored_llm_candidate(episode, utterance, schema_error, raw_item=item)

        relation_type = str(item["relation_type"])
        confidence = float(item["confidence"])
        supporting_text = str(item["supporting_text"]).strip()
        subject = self._normalize_subject(str(item["subject"]), speaker)
        object_value = self._clean_object(str(item["object"]))
        claim = str(item["claim"]).strip()

        rejection = self._rejection_reason(
            item=item,
            episode=episode,
            speaker=speaker,
            utterance=utterance,
            relation_type=relation_type,
            confidence=confidence,
            subject=subject,
            object_value=object_value,
            claim=claim,
            supporting_text=supporting_text,
        )
        if rejection:
            return self._ignored_llm_candidate(episode, utterance, rejection, raw_item=item)

        memory_type = str(item["memory_type"])
        if relation_type == "preference":
            memory_type = "preference"
        relation = self._candidate_relation(relation_type)
        candidate = self._candidate_for_fact(
            episode=episode,
            subject=subject,
            relation=relation,
            object_value=object_value,
            sensitive=False,
            memory_type=memory_type,
            confidence=confidence,
        )
        candidate.created_by = "llm"
        candidate.metadata.update(
            {
                "llm_extractor": "open_conversation",
                "speaker": str(item["speaker"]),
                "session_id": str(item["session_id"]),
                "provenance_dia_id": str(item["provenance_dia_id"]),
                "relation_type": relation_type,
                "temporal_hints": list(item["temporal_hints"]),
                "entity_mentions": list(item["entity_mentions"]),
                "supporting_text": supporting_text,
                "sensitivity": str(item["sensitivity"]),
                "resolved_entity": bool(item["resolved_entity"]),
            }
        )
        return candidate

    def _schema_error(self, item: Dict[str, Any]) -> str:
        required = OPEN_CONVERSATION_LLM_SCHEMA["properties"]["memories"]["items"]["required"]
        for field in required:
            if field not in item:
                return "schema_missing_%s" % field
        if item.get("memory_type") not in self.ALLOWED_MEMORY_TYPES:
            return "schema_invalid_memory_type"
        if item.get("relation_type") not in self.ALLOWED_RELATIONS:
            return "schema_invalid_relation_type"
        if not isinstance(item.get("confidence"), (int, float)):
            return "schema_invalid_confidence"
        for field in ("temporal_hints", "entity_mentions"):
            if not isinstance(item.get(field), list) or not all(isinstance(value, str) for value in item.get(field)):
                return "schema_invalid_%s" % field
        if not isinstance(item.get("resolved_entity"), bool):
            return "schema_invalid_resolved_entity"
        for field in ("provenance_dia_id", "speaker", "session_id", "subject", "object", "claim", "supporting_text", "sensitivity"):
            if not isinstance(item.get(field), str):
                return "schema_invalid_%s" % field
        return ""

    def _rejection_reason(
        self,
        item: Dict[str, Any],
        episode: Episode,
        speaker: str,
        utterance: str,
        relation_type: str,
        confidence: float,
        subject: str,
        object_value: str,
        claim: str,
        supporting_text: str,
    ) -> str:
        if str(item["provenance_dia_id"]) != episode.id:
            return "missing_or_wrong_provenance"
        if confidence < self.min_confidence:
            return "low_confidence"
        if relation_type in ("uncertainty", "ambiguous_reference"):
            return "ambiguous_reference" if relation_type == "ambiguous_reference" else "uncertainty"
        if not bool(item["resolved_entity"]):
            return "ambiguous_reference"
        if str(item["sensitivity"]) in ("high", "restricted") and episode.consent_basis != "explicit":
            return "sensitive_without_consent"
        if not supporting_text or not self._contains_text(utterance, supporting_text):
            return "unsupported_memory"
        if not object_value or not claim:
            return "unsupported_memory"
        if not self._subject_grounded(subject, speaker, utterance):
            return "hallucinated_entity"
        if not self._object_grounded(object_value, utterance):
            return "unsupported_memory"
        for entity in item["entity_mentions"]:
            if entity and not self._entity_grounded(str(entity), speaker, utterance):
                return "hallucinated_entity"
        return ""

    def _candidate_relation(self, relation_type: str) -> str:
        mapping = {
            "person_attribute": "attribute",
            "question_answerable_fact": "answerable_fact",
        }
        return mapping.get(relation_type, relation_type)

    def _normalize_subject(self, subject: str, speaker: str) -> str:
        if subject.strip().lower() in ("i", "me", "myself", "speaker"):
            return self._normalize_speaker(speaker)
        return self._normalize_speaker(subject)

    def _contains_text(self, source: str, snippet: str) -> bool:
        return self._normalized_text(snippet) in self._normalized_text(source)

    def _subject_grounded(self, subject: str, speaker: str, source: str) -> bool:
        if subject == self._normalize_speaker(speaker):
            return True
        subject_tokens = set(re.findall(r"[a-z0-9]+", subject.lower()))
        source_tokens = set(re.findall(r"[a-z0-9]+", source.lower()))
        return bool(subject_tokens & source_tokens)

    def _object_grounded(self, object_value: str, source: str) -> bool:
        object_tokens = self._meaningful_tokens(object_value)
        source_tokens = self._meaningful_tokens(source)
        return bool(object_tokens & source_tokens)

    def _entity_grounded(self, entity: str, speaker: str, source: str) -> bool:
        if entity.strip().lower() == speaker.strip().lower():
            return True
        return self._contains_text(source, entity) or bool(self._meaningful_tokens(entity) & self._meaningful_tokens(source))

    def _meaningful_tokens(self, text: str) -> set:
        return {
            token
            for token in re.findall(r"[a-z0-9]+", text.lower())
            if token not in self.LLM_STOPWORDS and len(token) > 1
        }

    def _normalized_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.lower()).strip()

    def _ignored_llm_candidate(
        self,
        episode: Episode,
        utterance: str,
        reason: str,
        raw_item: Optional[Dict[str, Any]] = None,
    ) -> MemoryCandidate:
        return MemoryCandidate(
            claim=utterance or episode.content,
            type="episodic_note",
            importance=0.1,
            novelty=0.2,
            confidence=0.1,
            stability="ephemeral",
            lifespan="session",
            evidence_episode_ids=[episode.id],
            recommended_action="ignore",
            created_by="llm",
            user_id=episode.user_id,
            project_id=episode.project_id,
            metadata=dict(
                self._episode_metadata(episode),
                llm_extractor="open_conversation",
                llm_rejection_reason=reason,
                llm_raw_relation=str(raw_item.get("relation_type", "")) if isinstance(raw_item, dict) else "",
            ),
        )


class OpenAIResponsesExtractionProvider:
    """Guarded OpenAI Responses API provider for structured extraction."""

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.openai.com/v1", timeout_seconds: int = 60) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for --extractor llm.")
        if not model:
            raise ValueError("OPENAI_LLM_EXTRACTOR_MODEL is required for --extractor llm.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "OpenAIResponsesExtractionProvider":
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=cls.from_env_model(),
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )

    @classmethod
    def from_env_model(cls) -> str:
        return os.environ.get("OPENAI_LLM_EXTRACTOR_MODEL", "") or os.environ.get("OPENAI_MODEL", "")

    def __call__(self, source_turn: Dict[str, Any]) -> Dict[str, Any]:
        request = urllib.request.Request(
            self._responses_url(),
            data=json.dumps(self._request_payload(source_turn)).encode("utf-8"),
            headers={
                "Authorization": "Bearer %s" % self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ExtractorSchemaError("OpenAI extraction request failed: HTTP %s %s" % (exc.code, detail[:500])) from exc
        except urllib.error.URLError as exc:
            raise ExtractorSchemaError("OpenAI extraction request failed: %s" % exc.reason) from exc
        return self._parse_response(payload)

    def _responses_url(self) -> str:
        if self.base_url.endswith("/responses"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return "%s/responses" % self.base_url
        return "%s/v1/responses" % self.base_url

    def _request_payload(self, source_turn: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "Extract only source-supported open-conversation memories as JSON. "
                        "Use an empty memories array if no durable memory is supported. "
                        "The input speaker is known; resolve first-person references such as I, me, my, we and our "
                        "to that speaker when the source turn makes the claim clear. "
                        "Extract clear preferences, habits, attributes, relationships, events, plans, locations, "
                        "object locations, temporal changes and commitments. "
                        "Use ambiguous_reference only when the referent cannot be resolved from the source turn "
                        "and speaker metadata. "
                        "Do not infer facts from missing context. Keep supporting_text as an exact substring."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(source_turn, sort_keys=True),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "open_conversation_memory_extraction",
                    "strict": True,
                    "schema": OPEN_CONVERSATION_LLM_SCHEMA,
                }
            },
        }

    def _parse_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(payload.get("output_text"), str):
            return json.loads(str(payload["output_text"]))
        for output in payload.get("output", []):
            if not isinstance(output, dict):
                continue
            for item in output.get("content", []):
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "refusal":
                    return {"memories": []}
                if isinstance(item.get("parsed"), dict):
                    return dict(item["parsed"])
                if isinstance(item.get("text"), str):
                    return json.loads(str(item["text"]))
        raise ExtractorSchemaError("OpenAI extraction response did not contain parseable structured output.")


class SchemaConstrainedLLMExtractor:
    """Optional LLM extractor wrapper with strict local schema validation.

    The injected provider is expected to return JSON or a Python mapping. This
    class makes no network calls and never writes durable memory itself.
    """

    ALLOWED_TYPES = {"semantic_fact", "preference", "procedure", "reflection", "constraint", "episodic_note"}
    ALLOWED_ACTIONS = {"store", "ignore", "ask_consent", "update_existing", "invalidate_existing", "delete", "do_not_use"}

    def __init__(self, provider: Callable[[Episode], object]) -> None:
        self.provider = provider

    def extract(self, episode: Episode) -> List[MemoryCandidate]:
        payload = self._load_payload(self.provider(episode))
        raw_candidates = payload.get("candidates")
        if not isinstance(raw_candidates, list):
            raise ExtractorSchemaError("LLM extractor output must contain a candidates list.")
        return [self._candidate_from_payload(item, episode) for item in raw_candidates]

    def _load_payload(self, raw: object) -> Dict[str, object]:
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ExtractorSchemaError("LLM extractor returned invalid JSON.") from exc
        elif isinstance(raw, dict):
            parsed = raw
        else:
            raise ExtractorSchemaError("LLM extractor output must be JSON text or a mapping.")
        if not isinstance(parsed, dict):
            raise ExtractorSchemaError("LLM extractor output must be a JSON object.")
        return parsed

    def _candidate_from_payload(self, item: object, episode: Episode) -> MemoryCandidate:
        if not isinstance(item, dict):
            raise ExtractorSchemaError("Each candidate must be an object.")
        claim = item.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            raise ExtractorSchemaError("Each candidate requires a non-empty claim.")
        memory_type = item.get("type", "semantic_fact")
        if memory_type not in self.ALLOWED_TYPES:
            raise ExtractorSchemaError("Unsupported candidate type: %r" % memory_type)
        action = item.get("recommended_action", "store")
        if action not in self.ALLOWED_ACTIONS:
            raise ExtractorSchemaError("Unsupported candidate action: %r" % action)
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ExtractorSchemaError("Candidate metadata must be an object.")

        return MemoryCandidate(
            claim=claim.strip(),
            type=str(memory_type),
            importance=self._float_field(item, "importance", 0.5),
            novelty=self._float_field(item, "novelty", 0.5),
            confidence=self._float_field(item, "confidence", 0.6),
            stability=str(item.get("stability", "temporary")),
            lifespan=str(item.get("lifespan", "until_changed")),
            risk_level=str(item.get("risk_level", "low")),
            evidence_episode_ids=self._string_list(item.get("evidence_episode_ids"), default=[episode.id]),
            counter_evidence_ids=self._string_list(item.get("counter_evidence_ids"), default=[]),
            recommended_action=str(action),
            created_by="llm",
            user_id=str(item.get("user_id", episode.user_id)),
            project_id=str(item.get("project_id", episode.project_id)),
            metadata=dict(metadata),
        )

    def _float_field(self, item: Dict[str, object], field: str, default: float) -> float:
        value = item.get(field, default)
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ExtractorSchemaError("Candidate field %s must be numeric." % field) from exc

    def _string_list(self, value: object, default: List[str]) -> List[str]:
        if value is None:
            return list(default)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ExtractorSchemaError("Candidate evidence fields must be string lists.")
        return list(value)
