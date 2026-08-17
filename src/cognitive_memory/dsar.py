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

:class:`DSARService` sits on top of it and plans a validated request against
a governed memory service: a strictly read-only report of what the request
would touch, audited by counts only.

Pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Dict, List
import uuid

from .reliability import MemoryRecord, Scope, tokenize

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


class DSARService:
    """Plans data subject requests against a governed memory service.

    :meth:`plan` is the dry-run half of the DSAR pipeline: it answers what a
    request would touch (which records, which sources, which derivative
    stores, whether a strict profile would hold it) without changing a single
    byte of state. No tombstone is written, no pending revocation is queued,
    the backend is never mutated, so an operator can show the plan to a data
    protection officer before anything is executed.

    The matching mirrors ``GovernedMemory._execute_forget`` exactly (same
    tokenization, same tenant filter, same token-subset test) rather than the
    retrieval path, because the plan has to predict erasure, not recall.
    """

    def __init__(self, service: Any = None) -> None:
        if service is None:
            # Imported lazily: mcp_server imports this module, so a module
            # level import would close the cycle.
            from .mcp_server import GovernedMemoryService

            service = GovernedMemoryService()
        self.service = service

    def plan(self, request: DSARRequest) -> Dict[str, Any]:
        """Return the read-only effect report for ``request``.

        Term kinds (erasure, consent withdrawal) report the matched records,
        their sources, the registered derivative stores and the hold reason a
        strict revocation profile would raise. Access requests report counts
        only, split into active and quarantined records.
        """

        if request.kind == "access":
            return self._plan_access(request)
        return self._plan_term(request)

    # -- kind handlers ------------------------------------------------------

    def _plan_term(self, request: DSARRequest) -> Dict[str, Any]:
        mem = self.service.memory_for(request.tenant)
        matched = self._matches(mem, request.tenant, request.term)
        would_hold = mem._revocation_hold_reason(
            Scope(tenant=request.tenant, subject=request.subject),
            request.requester, request.source, request.term,
        )
        mem.audit.record(
            "dsar_plan", request=request.to_dict(),
            matched_count=len(matched), would_hold=would_hold,
        )
        return {
            "request_id": request.request_id,
            "kind": request.kind,
            "tenant": request.tenant,
            "term": request.term,
            "matched_count": len(matched),
            "matched_ids": sorted(record.id for record in matched),
            "sources": sorted({record.source for record in matched}),
            "derivative_stores": [store.name for store in mem._derivative_stores],
            "would_hold": would_hold,
        }

    def _plan_access(self, request: DSARRequest) -> Dict[str, Any]:
        mem = self.service.memory_for(request.tenant)
        matched = self._matches(mem, request.tenant, request.subject)
        active = [record for record in matched if not record.quarantined]
        quarantined = [record for record in matched if record.quarantined]
        mem.audit.record(
            "dsar_plan", request=request.to_dict(),
            record_count=len(active), quarantined_count=len(quarantined),
        )
        return {
            "request_id": request.request_id,
            "kind": request.kind,
            "tenant": request.tenant,
            "subject": request.subject,
            "record_count": len(active),
            "quarantined_count": len(quarantined),
        }

    # -- matching -----------------------------------------------------------

    @staticmethod
    def _matches(mem: Any, tenant: str, term: str) -> List[MemoryRecord]:
        # Same token view as GovernedMemory._execute_forget: an empty token
        # set (a term like "---") deliberately matches nothing.
        term_tokens = tokenize(term)
        return [
            record for record in mem.backend.all_records()
            if record.scope.tenant == tenant and mem._term_hits(term_tokens, record)
        ]
