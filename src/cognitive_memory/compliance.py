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
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple


class ComplianceConfigError(ValueError):
    """Raised when a compliance profile is malformed or unknown."""


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
    # default trust to assign a source when the caller does not supply one
    source_trust: Mapping[str, float] = field(default_factory=dict)
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

    # -- retention (advisory metadata; enforcement is opt-in downstream) --
    retention_days: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.erasure_mode not in ("strict", "lenient"):
            raise ComplianceConfigError("erasure_mode must be 'strict' or 'lenient'")
        for value in (self.relevance_floor, self.trust_margin, self.min_store_trust):
            if not 0.0 <= float(value) <= 1.0:
                raise ComplianceConfigError("trust/relevance thresholds must be in [0,1]")

    # -- serialization ----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["source_trust"] = dict(self.source_trust)
        data["retention_days"] = dict(self.retention_days)
        data["extra_injection_patterns"] = list(self.extra_injection_patterns)
        data["extra_sensitive_patterns"] = list(self.extra_sensitive_patterns)
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
        if "source_trust" in kwargs:
            kwargs["source_trust"] = {str(k): float(v) for k, v in dict(kwargs["source_trust"]).items()}
        if "retention_days" in kwargs:
            kwargs["retention_days"] = {str(k): int(v) for k, v in dict(kwargs["retention_days"]).items()}
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
        source_trust={"external_tool": 0.5, "scraper": 0.4},
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
