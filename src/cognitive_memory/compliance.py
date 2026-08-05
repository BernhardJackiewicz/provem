"""Configurable compliance policy profiles for GovernedMemory.

Different domains have different compliance requirements: recruitment cares about
candidate/client scope isolation and salary sensitivity; pharma cares about
patient identifiers (MRN), adverse-event retention and consent; finance cares
about account/PII isolation. Instead of hard-coding one rule set, a
:class:`CompliancePolicy` parameterizes the governance layer, and profiles are
loadable from JSON (stdlib) or YAML (optional, if PyYAML is present).

The ``default`` profile is behaviour-identical to the original hard-coded
governance, so wrapping code that passes no profile keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
import json
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple


class ComplianceConfigError(ValueError):
    """Raised when a compliance profile is malformed or unknown."""


# A group that both contains an unbounded quantifier and is itself unbounded-
# quantified (e.g. (a+)+, (.*)*, (x+)*) -- the classic catastrophic-backtracking
# (ReDoS) signature. Conservative: reject such user-supplied deny-list patterns.
_REDOS_SIGNATURE = re.compile(r"\([^()]*[+*][^()]*\)\s*[+*]")


def _validate_purpose_rules(rules: Mapping[str, Any]) -> None:
    for purpose, rule in rules.items():
        if not isinstance(purpose, str) or not purpose:
            raise ComplianceConfigError("purpose_rules keys must be non-empty strings")
        if not isinstance(rule, Mapping):
            raise ComplianceConfigError("purpose_rules[%r] must be a mapping" % purpose)
        unknown = set(rule) - {"allowed_relations", "require_consent"}
        if unknown:
            raise ComplianceConfigError(
                "unknown purpose rule keys for %r: %s" % (purpose, sorted(unknown))
            )
        allowed = rule.get("allowed_relations")
        if allowed is not None:
            if isinstance(allowed, str) or not hasattr(allowed, "__iter__"):
                raise ComplianceConfigError(
                    "allowed_relations for %r must be a list of strings" % purpose
                )
            for relation in allowed:
                if not isinstance(relation, str):
                    raise ComplianceConfigError(
                        "allowed_relations for %r must be a list of strings" % purpose
                    )
        if "require_consent" in rule and not isinstance(rule["require_consent"], bool):
            raise ComplianceConfigError("require_consent for %r must be a bool" % purpose)


def _validate_patterns(patterns: Tuple[str, ...], kind: str) -> None:
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ComplianceConfigError("invalid %s regex %r: %s" % (kind, pattern, exc))
        if _REDOS_SIGNATURE.search(pattern):
            raise ComplianceConfigError(
                "potentially catastrophic (ReDoS) %s regex rejected: %r "
                "(nested unbounded quantifier)" % (kind, pattern)
            )


@dataclass(frozen=True)
class CompliancePolicy:
    """A named, serializable governance configuration.

    Fields map directly onto GovernedMemory's write- and read-side decisions.
    Defaults reproduce the original hard-coded behaviour.
    """

    name: str = "default"
    description: str = ""

    # -- detection (write-side quarantine) --------------------------------
    detect_injection: bool = True
    detect_sensitive: bool = True
    # domain-specific regex added on top of the shipped baseline patterns
    extra_injection_patterns: Tuple[str, ...] = ()
    extra_sensitive_patterns: Tuple[str, ...] = ()
    # quarantine sensitive content unless the write declares explicit consent
    require_consent_for_sensitive: bool = True

    # -- provenance / trust ----------------------------------------------
    # per-source trust override (e.g. distrust scrapers)
    source_trust: Mapping[str, float] = field(default_factory=dict)
    # trust assigned to a source NOT listed in source_trust; None keeps the
    # caller-supplied trust (backward compatible). Set it (e.g. 0.3) so an
    # unknown/omitted source cannot bypass a distrust policy via caller trust.
    unlisted_source_trust: Optional[float] = None
    # writes below this trust are quarantined (0.0 = accept anything)
    min_store_trust: float = 0.0
    # cross-source value conflicts need at least this trust gap, else abstain
    trust_margin: float = 0.15

    # -- relevance / abstention ------------------------------------------
    relevance_floor: float = 0.5

    # -- scope / tenancy --------------------------------------------------
    scope_isolation: bool = True
    cross_tenant_allowed: bool = False

    # -- erasure ----------------------------------------------------------
    # strict (default, recommended for GDPR/PHI): a term hit anywhere in a
    #   record's text/subject/object erases it (safe; may over-block look-alikes).
    # lenient: only erase when the term is the record's subject/object -- this
    #   deliberately does NOT erase a term that appears only in free text, so it
    #   can MISS free-text PII. Use only when over-blocking is the bigger risk and
    #   free-text erasure is handled elsewhere.
    erasure_mode: str = "strict"

    # -- deduplication ----------------------------------------------------
    # skip writing a record identical to an existing one (same subject/relation/
    # object/tenant/scope-subject). Off by default (preserves record counts).
    deduplicate: bool = False

    # -- retention --------------------------------------------------------
    retention_days: Mapping[str, int] = field(default_factory=dict)
    # also drop records older than their retention window at recall time (not
    # only via an explicit cleanup pass). Off by default.
    enforce_retention_on_recall: bool = False

    # -- purpose limitation ------------------------------------------------
    # per-purpose rules for the declared (untrusted) purpose on the read
    # path: {"hiring": {"allowed_relations": ("seniority",), "require_consent": True}}
    # An empty mapping (default) means no policy-level purpose rules; records
    # can still carry their own allowed_purposes allowlist.
    purpose_rules: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    # -- audit ------------------------------------------------------------
    # audit successful serves, not only blocks. On by default: an audit
    # trail that only records refusals cannot answer "who saw this value
    # and when". Plain no-match abstentions stay unaudited either way.
    audit_serves: bool = True

    # -- revocation authority ---------------------------------------------
    # strict mode: an erasure/restriction request must carry a requester who
    # is either the scoped data subject or a listed operator, and must not
    # carry instruction-risk content; anything else is held for review
    # instead of executing destructively. Off by default: in-band user
    # revocations execute immediately (the original behaviour).
    strict_revocation: bool = False
    revocation_operators: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.erasure_mode not in ("strict", "lenient"):
            raise ComplianceConfigError("erasure_mode must be 'strict' or 'lenient'")
        thresholds = [self.relevance_floor, self.trust_margin, self.min_store_trust]
        if self.unlisted_source_trust is not None:
            thresholds.append(self.unlisted_source_trust)
        for value in thresholds:
            if not 0.0 <= float(value) <= 1.0:
                raise ComplianceConfigError("trust/relevance thresholds must be in [0,1]")
        # Validate user-supplied deny-list regex now (fail fast, block ReDoS)
        _validate_patterns(self.extra_injection_patterns, "injection")
        _validate_patterns(self.extra_sensitive_patterns, "sensitive")
        _validate_purpose_rules(self.purpose_rules)

    # -- serialization ----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["source_trust"] = dict(self.source_trust)
        data["retention_days"] = dict(self.retention_days)
        data["extra_injection_patterns"] = list(self.extra_injection_patterns)
        data["extra_sensitive_patterns"] = list(self.extra_sensitive_patterns)
        data["revocation_operators"] = list(self.revocation_operators)
        data["purpose_rules"] = {
            purpose: {
                key: (list(value) if isinstance(value, (list, tuple)) else value)
                for key, value in rule.items()
            }
            for purpose, rule in self.purpose_rules.items()
        }
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CompliancePolicy":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = set(data) - known
        if unknown:
            raise ComplianceConfigError("unknown compliance policy fields: %s" % sorted(unknown))
        kwargs = dict(data)
        if "extra_injection_patterns" in kwargs:
            kwargs["extra_injection_patterns"] = tuple(kwargs["extra_injection_patterns"])
        if "extra_sensitive_patterns" in kwargs:
            kwargs["extra_sensitive_patterns"] = tuple(kwargs["extra_sensitive_patterns"])
        if "revocation_operators" in kwargs:
            kwargs["revocation_operators"] = tuple(str(v) for v in kwargs["revocation_operators"])
        if "purpose_rules" in kwargs:
            rules: Dict[str, Any] = {}
            for purpose, rule in dict(kwargs["purpose_rules"]).items():
                rule = dict(rule)
                if rule.get("allowed_relations") is not None:
                    rule["allowed_relations"] = tuple(str(r) for r in rule["allowed_relations"])
                rules[str(purpose)] = rule
            kwargs["purpose_rules"] = rules
        if "source_trust" in kwargs:
            kwargs["source_trust"] = {str(k): float(v) for k, v in dict(kwargs["source_trust"]).items()}
        if "retention_days" in kwargs:
            kwargs["retention_days"] = {str(k): int(v) for k, v in dict(kwargs["retention_days"]).items()}
        # Coerce scalar float fields too (a JSON/YAML profile may quote numbers);
        # otherwise the value stays a str, passes __post_init__'s float() range
        # check, and crashes the first write/recall comparison.
        for scalar in ("min_store_trust", "trust_margin", "relevance_floor"):
            if scalar in kwargs and kwargs[scalar] is not None:
                kwargs[scalar] = float(kwargs[scalar])
        if kwargs.get("unlisted_source_trust") is not None:
            kwargs["unlisted_source_trust"] = float(kwargs["unlisted_source_trust"])
        return cls(**kwargs)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "CompliancePolicy":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ComplianceConfigError("invalid compliance JSON: %s" % exc) from exc
        return cls.from_dict(data)

    @classmethod
    def load(cls, path: str) -> "CompliancePolicy":
        """Load a profile from a .json or .yaml/.yml file (YAML needs PyYAML)."""
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore
            except Exception as exc:  # pragma: no cover - depends on optional dep
                raise ComplianceConfigError(
                    "YAML profiles require PyYAML; install it or use JSON: %s" % exc
                ) from exc
            return cls.from_dict(yaml.safe_load(text))
        return cls.from_json(text)


# ---------------------------------------------------------------------------
# Built-in domain profiles
# ---------------------------------------------------------------------------

_PHARMA_SENSITIVE = (
    r"\bmrn\b",
    r"\bmedical\s+record\s+number\b",
    r"\bpatient\s+id\b",
    r"\bicd[- ]?10\b",
    r"\badverse\s+event\b",
    r"\bdiagnos[ei]s\b",
    r"\bprescription\b",
    r"\bdosage\b",
)
_RECRUITING_SENSITIVE = (
    r"\bsalary\b",
    r"\bcompensation\b",
    r"\bnotice\s+period\b",
    r"\bcurrent\s+employer\b",
    r"\bvisa\s+status\b",
)
_FINANCE_SENSITIVE = (
    r"\biban\b",
    r"\baccount\s+number\b",
    r"\brouting\s+number\b",
    r"\bcredit\s+card\b",
    r"\bcvv\b",
    r"\bpan\b",
)


_BUILTIN: Dict[str, CompliancePolicy] = {
    "default": CompliancePolicy(
        name="default",
        description="Behaviour-identical to the original hard-coded governance.",
    ),
    "recruitment": CompliancePolicy(
        name="recruitment",
        description="Candidate/client scope isolation, salary/visa sensitivity, low trust for tool-sourced writes.",
        extra_sensitive_patterns=_RECRUITING_SENSITIVE,
        source_trust={"user": 0.9, "recruiter": 0.9, "external_tool": 0.5, "scraper": 0.4},
        unlisted_source_trust=0.3,
        trust_margin=0.2,
        scope_isolation=True,
        cross_tenant_allowed=False,
        retention_days={"high": 365, "restricted": 180},
    ),
    "pharma": CompliancePolicy(
        name="pharma",
        description="HIPAA-style PHI: MRN/patient identifiers, consent required, strict erasure, long adverse-event retention.",
        extra_sensitive_patterns=_PHARMA_SENSITIVE,
        require_consent_for_sensitive=True,
        source_trust={"user": 0.9, "clinician": 0.9, "external_tool": 0.4, "scraper": 0.2},
        unlisted_source_trust=0.3,
        min_store_trust=0.3,
        trust_margin=0.25,
        scope_isolation=True,
        cross_tenant_allowed=False,
        erasure_mode="strict",
        retention_days={"restricted": 3650, "high": 1825},
    ),
    "finance": CompliancePolicy(
        name="finance",
        description="PCI/PII: account and card identifiers, strict tenant isolation.",
        extra_sensitive_patterns=_FINANCE_SENSITIVE,
        require_consent_for_sensitive=True,
        source_trust={"user": 0.9, "external_tool": 0.4, "scraper": 0.2},
        unlisted_source_trust=0.2,
        min_store_trust=0.2,
        scope_isolation=True,
        cross_tenant_allowed=False,
        retention_days={"restricted": 2555},
    ),
}


def available_profiles() -> Tuple[str, ...]:
    return tuple(sorted(_BUILTIN))


def load_profile(name: str) -> CompliancePolicy:
    """Return a built-in profile by name."""
    if name not in _BUILTIN:
        raise ComplianceConfigError(
            "unknown compliance profile %r; available: %s" % (name, ", ".join(available_profiles()))
        )
    return _BUILTIN[name]


def resolve_policy(policy: Optional[Any]) -> CompliancePolicy:
    """Coerce None / a profile name / a dict / a CompliancePolicy into a policy."""
    if policy is None:
        return _BUILTIN["default"]
    if isinstance(policy, CompliancePolicy):
        return policy
    if isinstance(policy, str):
        return load_profile(policy)
    if isinstance(policy, Mapping):
        return CompliancePolicy.from_dict(policy)
    raise ComplianceConfigError("cannot resolve compliance policy from %r" % type(policy))
