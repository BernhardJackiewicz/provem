"""The DSAR request model: one canonical shape for every intake channel.

MCP tools, the REST gateway and the ITSM pull listeners all converge on
:class:`DSARRequest`, so validation happens once at the boundary instead of
being re-invented per channel. Validation is deliberately stricter than
``GovernedMemory.forget``: an ITSM ticket always names an acting requester,
so ``requester`` is mandatory for every kind and a request that cannot be
attributed is rejected rather than executed.

The dataclass is frozen and validates the values it is given. Normalization
(casting, stripping, defaulting, dropping unknown keys) belongs to
:meth:`DSARRequest.from_dict`, which is the entry point for untrusted
channel payloads.

Pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Dict
import uuid

KINDS = ("erasure", "consent_withdrawal", "access")

# kinds that act on a term (the identifier whose memories are affected)
_TERM_KINDS = ("erasure", "consent_withdrawal")

DEFAULT_SOURCE = "itsm"


def _is_blank(value: str) -> bool:
    return not str(value).strip()


@dataclass(frozen=True)
class DSARRequest:
    """A validated data subject request from an external intake channel."""

    request_id: str
    kind: str
    tenant: str
    requester: str
    term: str = ""
    subject: str = ""
    purpose: str = ""
    ticket: str = ""
    source: str = DEFAULT_SOURCE

    def __post_init__(self) -> None:
        for name in ("request_id", "tenant", "requester"):
            if _is_blank(getattr(self, name)):
                raise ValueError("DSAR request requires a non-empty %s" % name)

        if self.kind not in KINDS:
            raise ValueError(
                "unknown DSAR kind %r, expected one of %s" % (self.kind, ", ".join(KINDS))
            )

        if self.kind in _TERM_KINDS and _is_blank(self.term):
            raise ValueError("DSAR kind %r requires a non-empty term" % self.kind)

        if self.kind == "access" and _is_blank(self.subject):
            raise ValueError("DSAR kind 'access' requires a non-empty subject")

        if not _is_blank(self.purpose) and self.kind != "consent_withdrawal":
            raise ValueError(
                "purpose is only meaningful for kind 'consent_withdrawal', not %r" % self.kind
            )

    @classmethod
    def from_dict(cls, data: Any) -> "DSARRequest":
        """Build a request from an untrusted channel payload.

        Known keys are cast to ``str`` and stripped, unknown keys are dropped
        (ITSM payloads carry plenty of vendor noise), ``None`` reads as empty.
        A missing or empty ``request_id`` is generated so every request is
        addressable; a caller-supplied id is always preserved.
        """

        if not isinstance(data, dict):
            raise ValueError("DSAR payload must be a dict, got %s" % type(data).__name__)

        values: Dict[str, str] = {}
        for spec in fields(cls):
            raw = data.get(spec.name)
            values[spec.name] = "" if raw is None else str(raw).strip()

        if not values["request_id"]:
            values["request_id"] = uuid.uuid4().hex
        if not values["source"]:
            values["source"] = DEFAULT_SOURCE

        return cls(**values)

    def to_dict(self) -> Dict[str, str]:
        """Return the request as a plain JSON-serializable dict."""

        return {spec.name: getattr(self, spec.name) for spec in fields(self)}
