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
would touch, audited by counts only. :meth:`DSARService.execute` is the
write half: it performs the erasure or the consent withdrawal exactly once
per ``request_id``, so an ITSM channel that retries a delivery never erases
twice and never issues a second certificate.

Pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Dict, List, Optional, Set, Tuple
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

    :meth:`execute` is the write half. Its replay registry is derived from the
    audit trail, not held in memory only, so idempotency survives a restart:
    the ``dsar_execute`` entry carries the full response summary and a fresh
    service rehydrates it on first use per tenant.
    """

    def __init__(self, service: Any = None) -> None:
        if service is None:
            # Imported lazily: mcp_server imports this module, so a module
            # level import would close the cycle.
            from .mcp_server import GovernedMemoryService

            service = GovernedMemoryService()
        self.service = service
        # (tenant, request_id) -> the audited summary of the first execution.
        self._registry: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # Tenants whose registry has already been rehydrated from the audit.
        self._registry_tenants: Set[str] = set()

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

    def execute(self, request: DSARRequest) -> Dict[str, Any]:
        """Carry out ``request`` against the governed memory, exactly once.

        The ``request_id`` is the idempotency key: a request that has already
        been executed (in this process or in an earlier one, as reconstructed
        from the audit trail) is replayed from its recorded summary without
        touching memory and without appending a second audit entry.

        A first execution ends in one of two states. ``executed`` means the
        revocation went through: an erasure returns the freshly issued
        certificate, a consent withdrawal returns no certificate because no
        record was deleted. ``held`` means a strict revocation profile queued
        the request for a second operator instead: nothing was erased,
        nothing was revoked, and the caller gets the pending id to approve.
        """

        mem = self.service.memory_for(request.tenant)
        self._ensure_registry(mem, request.tenant)

        recorded = self._registry.get((request.tenant, request.request_id))
        if recorded is not None:
            return self._replay(mem, request.tenant, recorded)

        if request.kind == "access":
            raise NotImplementedError(
                "DSAR kind 'access' cannot be executed yet; use "
                "DSARService.plan for the read-only access report"
            )
        if request.kind == "erasure":
            return self._execute_erasure(mem, request)
        return self._execute_consent_withdrawal(mem, request)

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

    # -- execution ----------------------------------------------------------

    def _execute_erasure(self, mem: Any, request: DSARRequest) -> Dict[str, Any]:
        pending_before = len(mem.list_pending_revocations(request.tenant))
        certs_before = len(mem.audit.filter("erasure"))
        removed = mem.forget(
            request.term,
            Scope(tenant=request.tenant, subject=request.subject),
            requester=request.requester, source=request.source,
        )
        held = self._held(mem, request.tenant, pending_before)
        if held is not None:
            return self._hold_result(mem, request, held)

        # Count-based capture, like GovernedMemoryService.forget: filter(...)[-1]
        # would hand back a stale certificate from an earlier erasure.
        certs = mem.audit.filter("erasure")
        cert = certs[-1].to_dict() if len(certs) > certs_before else {}
        details: Dict[str, Any] = {
            "request_id": request.request_id,
            "kind": request.kind,
            "status": "executed",
            "ticket": request.ticket,
        }
        if cert:
            # The seq is the audit index, so a replay can re-read the exact
            # certificate instead of storing a copy of it in two places.
            details["certificate_seq"] = int(cert["seq"])
        self._record(mem, request, details)
        return {
            "request_id": request.request_id,
            "kind": request.kind,
            "tenant": request.tenant,
            "status": "executed",
            "replayed": False,
            "removed": removed,
            "certificate": cert,
        }

    def _execute_consent_withdrawal(self, mem: Any, request: DSARRequest) -> Dict[str, Any]:
        pending_before = len(mem.list_pending_revocations(request.tenant))
        mem.revoke_consent(
            request.term,
            Scope(tenant=request.tenant, subject=request.subject),
            request.purpose,
            requester=request.requester, source=request.source,
        )
        held = self._held(mem, request.tenant, pending_before)
        if held is not None:
            return self._hold_result(mem, request, held)

        details: Dict[str, Any] = {
            "request_id": request.request_id,
            "kind": request.kind,
            "status": "executed",
            "ticket": request.ticket,
        }
        # No certificate: the records stay stored, only the read gate changes.
        self._record(mem, request, details)
        return {
            "request_id": request.request_id,
            "kind": request.kind,
            "tenant": request.tenant,
            "status": "executed",
            "replayed": False,
        }

    def _hold_result(self, mem: Any, request: DSARRequest, held: Any) -> Dict[str, Any]:
        details: Dict[str, Any] = {
            "request_id": request.request_id,
            "kind": request.kind,
            "status": "held",
            "ticket": request.ticket,
            "pending_id": held.id,
            "reason": held.reason,
        }
        self._record(mem, request, details)
        return {
            "request_id": request.request_id,
            "kind": request.kind,
            "tenant": request.tenant,
            "status": "held",
            "replayed": False,
            "pending_id": held.id,
            "reason": held.reason,
        }

    @staticmethod
    def _held(mem: Any, tenant: str, pending_before: int) -> Optional[Any]:
        # Count-based again: forget and revoke_consent report a hold by
        # queueing a pending revocation, not by their return value.
        pending = mem.list_pending_revocations(tenant)
        return pending[-1] if len(pending) > pending_before else None

    # -- idempotency --------------------------------------------------------

    def _ensure_registry(self, mem: Any, tenant: str) -> None:
        """Rehydrate the replay registry for ``tenant`` from the audit trail.

        Done once per tenant and per service: the audit is append-only and
        every later execution is registered in the same step that audits it,
        so a second scan could only re-read what is already known.
        """

        if tenant in self._registry_tenants:
            return
        self._registry_tenants.add(tenant)
        for entry in mem.audit.filter("dsar_execute"):
            details = dict(entry.details)
            request_id = str(details.get("request_id", ""))
            if request_id:
                self._registry[(tenant, request_id)] = details

    def _record(self, mem: Any, request: DSARRequest, details: Dict[str, Any]) -> None:
        """Audit the first execution and register it for replay.

        The registered summary is the audit entry's own details dict, so a
        rebuild from the trail is lossless by construction.
        """

        entry = mem.audit.record("dsar_execute", **details)
        self._registry[(request.tenant, request.request_id)] = dict(entry.details)

    def _replay(self, mem: Any, tenant: str, summary: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(summary)
        result["tenant"] = tenant
        result["replayed"] = True
        seq = summary.get("certificate_seq")
        if isinstance(seq, int):
            entries = mem.audit.entries()
            if 0 <= seq < len(entries):
                result["certificate"] = entries[seq].to_dict()
        return result

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
