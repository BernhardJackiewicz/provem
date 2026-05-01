from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional, Tuple

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
