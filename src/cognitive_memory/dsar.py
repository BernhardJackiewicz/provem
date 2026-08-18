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
write half: it performs the erasure, the consent withdrawal or the access
export exactly once per ``request_id``, so an ITSM channel that retries a
delivery never erases twice and never issues a second certificate.
:meth:`DSARService.verify` is the after-the-fact evidence half (did the
deletion actually hold?), and :meth:`DSARService.approve` /
:meth:`DSARService.reject` let a second operator close out a request that a
strict revocation profile put on hold, addressed by the ``request_id`` the
ITSM ticket knows rather than by an internal pending id.

Around that core sit the operational surfaces an ITSM integration needs:
an optional detached signer for every certificate it hands out,
:meth:`DSARService.status` as the lifecycle view a polling channel asks for,
:meth:`DSARService.metrics` / :meth:`DSARService.render_metrics` for the
service's own health, and :meth:`DSARService.ticket_payload` as the single
evidence bundle that gets attached to the ticket when it is closed.

Pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Dict, List, Optional, Set, Tuple
import hashlib
import time
import uuid

from .reliability import MemoryRecord, Scope, tokenize
from .signing import canonical_json, sign_certificate

KINDS = ("erasure", "consent_withdrawal", "access")

# kinds that act on a term (the identifier whose memories are affected)
_TERM_KINDS = ("erasure", "consent_withdrawal")

DEFAULT_SOURCE = "itsm"

# Audit actions that carry a request summary the replay registry rebuilds from.
_REGISTRY_ACTIONS = ("dsar_execute", "dsar_approve", "dsar_reject")

# Read-side exclusion reasons that count as an honoured consent withdrawal.
_REFUSAL_REASONS = frozenset({"consent_revoked", "do_not_use"})


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

    ``signer`` is optional and duck-typed (any object with
    ``sign(bytes) -> dict``, for example :class:`signing.HmacSigner`). With a
    signer configured, every response that carries a certificate carries a
    detached ``signed_certificate`` next to it; without one, the responses are
    byte-identical to the unsigned artifacts. ``monotonic`` is the clock the
    uptime metric is measured against and exists so a test can pin it.
    """

    def __init__(self, service: Any = None, signer: Any = None,
                 monotonic: Any = None) -> None:
        if service is None:
            # Imported lazily: mcp_server imports this module, so a module
            # level import would close the cycle.
            from .mcp_server import GovernedMemoryService

            service = GovernedMemoryService()
        self.service = service
        self._signer = signer
        self._monotonic = time.monotonic if monotonic is None else monotonic
        self._started = self._monotonic()
        # (tenant, request_id) -> the audited summary of the first execution.
        self._registry: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # Tenants whose registry has already been rehydrated from the audit.
        self._registry_tenants: Set[str] = set()
        # Process-local counters, see metrics().
        self._requests: Dict[str, Dict[str, int]] = {}
        self._verify_passed = 0
        self._verify_failed = 0
        self._residuals_found = 0

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

        An access request is read-only against memory but still write-once in
        the audit: it returns the subject's data package and the hash that
        pins it, and a retry re-derives the package from the current state
        instead of serving a stale copy.
        """

        mem = self.service.memory_for(request.tenant)
        self._ensure_registry(mem, request.tenant)

        recorded = self._registry.get((request.tenant, request.request_id))
        if recorded is not None:
            if str(recorded.get("kind", "")) == "access":
                # The package is never stored, so a replay has to re-derive it
                # from live state; that needs the request, which _replay (fed
                # from the audit summary alone) does not have.
                return self._replay_access(mem, request, recorded)
            return self._replay(mem, request.tenant, recorded)

        if request.kind == "access":
            return self._execute_access(mem, request)
        if request.kind == "erasure":
            return self._execute_erasure(mem, request)
        return self._execute_consent_withdrawal(mem, request)

    def verify(self, request_id: str, tenant: str) -> Dict[str, Any]:
        """Re-check a completed request against current state, on demand.

        "The request was executed" is a claim about the past; this is the
        evidence for it now. An erasure is probed from four independent
        angles (a residual scan over the store, a governed recall that must
        abstain, the tombstone registry, the audit chain) plus the backend's
        own verification sweep when the backend offers one, so a restore from
        backup that silently reinstates erased records shows up as a failed
        check rather than as a green certificate. A consent withdrawal is
        probed by the read gate that is supposed to refuse, an access export
        by re-deriving the package and comparing hashes.

        Read-only against memory and repeatable: every call appends one
        ``dsar_verify`` entry (the probe itself is auditable evidence) and
        never rewrites the request's registered outcome. An unknown
        ``request_id`` is answered structurally instead of raising, because a
        polling ITSM integration asks about ids this tenant may never have
        seen, and there is no kind to audit a probe against.
        """

        mem = self.service.memory_for(tenant)
        self._ensure_registry(mem, tenant)
        summary = self._registry.get((tenant, request_id))
        if summary is None:
            return {
                "request_id": request_id,
                "tenant": tenant,
                "passed": False,
                "unknown_request_id": True,
            }

        report = self._verify_report(mem, tenant, request_id, summary)
        self._count_verify(report)
        mem.audit.record(
            "dsar_verify",
            request_id=request_id,
            kind=str(summary.get("kind", "")),
            status=str(summary.get("status", "")),
            passed=bool(report["passed"]),
        )
        return report

    def approve(self, request_id: str, tenant: str, reviewer: str) -> Dict[str, Any]:
        """Execute a held request after a second operator signed off.

        Addressed by ``request_id``, not by the internal pending id: the ITSM
        ticket knows the request it raised, not the id the hold generated. An
        erasure returns the certificate the approval produced, so the closing
        ITSM comment carries the same evidence a direct execution would.

        Only a held request can be approved. An unknown id is a caller error
        (``KeyError``), an already settled one a state error (``ValueError``);
        neither is silently absorbed, because both would otherwise read as
        "approved" to the channel that asked.
        """

        mem = self.service.memory_for(tenant)
        summary = self._held_summary(mem, tenant, request_id, "approve")
        kind = str(summary.get("kind", ""))
        pending_id = str(summary.get("pending_id", ""))

        # Count-based capture like _execute_erasure: filter(...)[-1] alone
        # could hand back a certificate from an unrelated earlier erasure.
        certs_before = len(mem.audit.filter("erasure"))
        removed = mem.approve_revocation(pending_id, reviewer)
        certs = mem.audit.filter("erasure")
        cert = certs[-1].to_dict() if len(certs) > certs_before else {}

        details = self._review_details(summary, "approved", reviewer, pending_id)
        if cert:
            details["certificate_seq"] = int(cert["seq"])
        self._register_review(mem, tenant, request_id, "dsar_approve", details)

        result: Dict[str, Any] = {
            "request_id": request_id,
            "kind": kind,
            "tenant": tenant,
            "status": "approved",
            "pending_id": pending_id,
            "reviewer": reviewer,
        }
        if kind == "erasure":
            result["removed"] = removed
            result["certificate"] = cert
        return self._signed(result)

    def reject(self, request_id: str, tenant: str, reviewer: str,
               reason: str = "") -> Dict[str, Any]:
        """Close a held request without executing it.

        The data stays untouched and the rejection is durable: the registry
        and the audit both carry the outcome, so a later verify reports
        ``rejected`` with ``passed`` False instead of probing an erasure that
        was deliberately never performed.
        """

        mem = self.service.memory_for(tenant)
        summary = self._held_summary(mem, tenant, request_id, "reject")
        pending_id = str(summary.get("pending_id", ""))
        mem.reject_revocation(pending_id, reviewer, reason)

        details = self._review_details(summary, "rejected", reviewer, pending_id)
        details["reject_reason"] = reason
        self._register_review(mem, tenant, request_id, "dsar_reject", details)
        return {
            "request_id": request_id,
            "kind": str(summary.get("kind", "")),
            "tenant": tenant,
            "status": "rejected",
            "pending_id": pending_id,
            "reviewer": reviewer,
        }

    # -- lifecycle ----------------------------------------------------------

    def status(self, request_id: str, tenant: str) -> Dict[str, Any]:
        """Report where a request stands, for the channel that raised it.

        The lifecycle an ITSM integration polls for: ``planned`` (a dry run
        happened, nothing was executed), ``executed``, ``held``, ``approved``
        or ``rejected``, with the ticket it belongs to and the outcome of the
        most recent verify probe (``last_verify_passed``, ``None`` when the
        request was never verified). A request that neither the replay
        registry nor a plan entry knows raises ``KeyError``, so a gateway can
        answer 404 instead of inventing a state.
        """

        mem = self.service.memory_for(tenant)
        self._ensure_registry(mem, tenant)
        summary = self._registry.get((tenant, request_id))
        if summary is None:
            planned = self._planned_request(mem, request_id)
            if planned is None:
                raise KeyError(
                    "unknown DSAR request_id %r for tenant %r" % (request_id, tenant)
                )
            return {
                "request_id": request_id,
                "tenant": tenant,
                "kind": str(planned.get("kind", "")),
                "status": "planned",
                "ticket": str(planned.get("ticket", "")),
                "last_verify_passed": None,
            }

        state: Dict[str, Any] = {
            "request_id": request_id,
            "tenant": tenant,
            "kind": str(summary.get("kind", "")),
            "status": str(summary.get("status", "")),
            "ticket": str(summary.get("ticket", "")),
        }
        # Only present for the states that have them: a held request has a
        # pending id, an erasure has a certificate.
        for name in ("pending_id", "certificate_seq"):
            if name in summary:
                state[name] = summary[name]
        state["last_verify_passed"] = self._last_verify_passed(mem, request_id)
        return state

    # -- metrics ------------------------------------------------------------

    def metrics(self) -> Dict[str, Any]:
        """Return the service's own counters as nested, JSON-ready dicts.

        Deliberately process-local: the counters describe what this service
        instance handled since it started, not the tenant's full history. A
        history figure would have to be rebuilt from every tenant's audit
        trail on every scrape, and the audit trail is already the authority
        for that question. ``uptime_seconds`` is measured against the
        injected monotonic clock, so it cannot go backwards when the wall
        clock is corrected.

        ``requests`` counts first executions only (``executed`` / ``held``)
        plus the review outcomes (``approved`` / ``rejected``); a replay is
        not a new request and is not counted.
        """

        return {
            "requests": {
                kind: dict(statuses) for kind, statuses in self._requests.items()
            },
            "verify": {"passed": self._verify_passed, "failed": self._verify_failed},
            "residuals_found": self._residuals_found,
            "uptime_seconds": self._monotonic() - self._started,
        }

    def render_metrics(self) -> str:
        """Render :meth:`metrics` as Prometheus-style text lines."""

        snapshot = self.metrics()
        requests = snapshot["requests"]
        lines: List[str] = []
        for kind in sorted(requests):
            for status in sorted(requests[kind]):
                lines.append(
                    'dsar_requests_total{kind="%s",status="%s"} %d'
                    % (kind, status, requests[kind][status])
                )
        # Always emitted, even at zero: a missing series and a zero series
        # read very differently on a dashboard.
        verify = snapshot["verify"]
        lines.append('dsar_verify_total{result="passed"} %d' % verify["passed"])
        lines.append('dsar_verify_total{result="failed"} %d' % verify["failed"])
        lines.append("dsar_residuals_found_total %d" % snapshot["residuals_found"])
        lines.append("dsar_uptime_seconds %.3f" % snapshot["uptime_seconds"])
        return "".join(line + "\n" for line in lines)

    # -- ticket artifact ----------------------------------------------------

    def ticket_payload(self, request_id: str, tenant: str) -> Dict[str, Any]:
        """Bundle the evidence for a request into one attachable artifact.

        This is what gets attached to the ITSM ticket when it is closed: the
        request as it was recorded, its status, the erasure certificate (with
        its detached signature when a signer is configured), a freshly taken
        verify report and the audit head the whole thing is anchored to.

        Building the payload *is* a verification, so it runs the regular
        :meth:`verify` and leaves its ``dsar_verify`` entry in the trail. The
        head hash is read afterwards on purpose: the hash in the attachment
        covers the probe that the attachment reports. A request that is only
        planned is bundled honestly, with the unknown-request verify shape and
        no certificate; an entirely unknown ``request_id`` raises
        ``ValueError``, because a payload without a request is meaningless.
        """

        mem = self.service.memory_for(tenant)
        self._ensure_registry(mem, tenant)
        summary = self._registry.get((tenant, request_id))
        recorded = summary
        if recorded is None:
            recorded = self._planned_request(mem, request_id)
            if recorded is None:
                raise ValueError(
                    "unknown DSAR request_id %r for tenant %r" % (request_id, tenant)
                )

        verify_report = self.verify(request_id, tenant)
        payload: Dict[str, Any] = {
            "request": self._request_fields(tenant, recorded),
            "status": str(summary.get("status", "")) if summary else "planned",
        }
        certificate = self._certificate(mem, summary) if summary else {}
        if certificate:
            payload["certificate"] = certificate
        self._signed(payload)
        payload["verify_report"] = verify_report
        payload["audit"] = {
            "head_hash": mem.audit.head_hash(),
            "verified": bool(mem.verify_audit()),
        }
        return payload

    # -- signing ------------------------------------------------------------

    def _signed(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Attach a detached signature to a result that carries a certificate.

        Detached and additive: the ``certificate`` itself stays byte-identical
        to the audit entry it came from, and without a signer no key is added
        at all, so an unsigned deployment sees exactly the shapes it always
        saw.
        """

        if self._signer is None:
            return result
        certificate = result.get("certificate")
        if isinstance(certificate, dict) and certificate:
            result["signed_certificate"] = sign_certificate(certificate, self._signer)
        return result

    # -- counters -----------------------------------------------------------

    def _count_request(self, kind: Any, status: Any) -> None:
        kind = str(kind or "")
        status = str(status or "")
        if not kind or not status:
            return
        by_status = self._requests.setdefault(kind, {})
        by_status[status] = by_status.get(status, 0) + 1

    def _count_verify(self, report: Dict[str, Any]) -> None:
        """Fold one verify report into the counters.

        The residual count is read off the finished report rather than raised
        inside the checks, so :meth:`_erasure_checks` stays a pure probe with
        no counter side effects.
        """

        if bool(report.get("passed")):
            self._verify_passed += 1
        else:
            self._verify_failed += 1
        if str(report.get("kind", "")) != "erasure":
            return
        checks = report.get("checks")
        if not isinstance(checks, dict):
            return
        scan = checks.get("residual_scan")
        if isinstance(scan, dict):
            self._residuals_found += int(scan.get("residual_count", 0))

    # -- summaries ----------------------------------------------------------

    @staticmethod
    def _planned_request(mem: Any, request_id: str) -> Optional[Dict[str, Any]]:
        """Return the planned request behind ``request_id``, newest first."""

        for entry in reversed(mem.audit.filter("dsar_plan")):
            request = entry.details.get("request")
            if not isinstance(request, dict):
                continue
            if str(request.get("request_id", "")) == request_id:
                return dict(request)
        return None

    @staticmethod
    def _last_verify_passed(mem: Any, request_id: str) -> Optional[bool]:
        """Return the newest verify outcome for ``request_id``, if any."""

        for entry in reversed(mem.audit.filter("dsar_verify")):
            if str(entry.details.get("request_id", "")) == request_id:
                return bool(entry.details.get("passed"))
        return None

    @staticmethod
    def _request_fields(tenant: str, recorded: Dict[str, Any]) -> Dict[str, str]:
        """Restate the request from a recorded summary or plan entry.

        Summaries only carry the fields the request's kind needed (an access
        export has no term, an erasure no purpose), so the missing ones come
        back as empty strings rather than as absent keys: the attachment has
        one shape whatever the kind.
        """

        request = {spec.name: "" for spec in fields(DSARRequest)}
        for name in request:
            value = recorded.get(name)
            if value is not None:
                request[name] = str(value)
        request["tenant"] = tenant
        return request

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
            # The term makes the summary self-supporting: verify re-probes the
            # erasure from the registry alone, without the original request.
            "term": request.term,
        }
        if cert:
            # The seq is the audit index, so a replay can re-read the exact
            # certificate instead of storing a copy of it in two places.
            details["certificate_seq"] = int(cert["seq"])
        self._record(mem, request, details)
        return self._signed({
            "request_id": request.request_id,
            "kind": request.kind,
            "tenant": request.tenant,
            "status": "executed",
            "replayed": False,
            "removed": removed,
            "certificate": cert,
        })

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
            "term": request.term,
            # The purpose decides which read the refusal probe has to make: a
            # purpose-scoped withdrawal only blocks that declared purpose.
            "purpose": request.purpose,
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

    def _execute_access(self, mem: Any, request: DSARRequest) -> Dict[str, Any]:
        """Export everything the tenant holds about the subject (Art. 15).

        Nothing in memory changes; what is written once is the audit pair. The
        ``dsar_execute`` anchor makes the export replayable under the same
        idempotency key as the write kinds, and ``access_export`` is the
        disclosure record a regulator asks for. Both carry counts and the
        package hash only: the package itself is delivered to the caller and
        never persisted, so the audit trail does not become a second copy of
        the personal data the request is about.
        """

        records, withheld = self._access_package(mem, request.tenant, request.subject)
        package_sha256 = self._package_hash(records, withheld)
        details: Dict[str, Any] = {
            "request_id": request.request_id,
            "kind": request.kind,
            "status": "executed",
            "ticket": request.ticket,
            # The subject is an identifier, not content: it lets verify
            # re-derive the package and compare hashes without the request.
            "subject": request.subject,
            "record_count": len(records),
            "withheld_count": len(withheld),
            "package_sha256": package_sha256,
        }
        self._record(mem, request, details)
        mem.audit.record(
            "access_export",
            request_id=request.request_id,
            subject=request.subject,
            requester=request.requester,
            record_count=len(records),
            withheld_count=len(withheld),
            package_sha256=package_sha256,
        )
        return {
            "request_id": request.request_id,
            "kind": request.kind,
            "tenant": request.tenant,
            "subject": request.subject,
            "status": "executed",
            "replayed": False,
            "records": records,
            "withheld": withheld,
            "record_count": len(records),
            "withheld_count": len(withheld),
            "package_sha256": package_sha256,
        }

    @staticmethod
    def _access_package(
        mem: Any, tenant: str, subject: str
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Return (records, withheld) for the subject, both sorted by id.

        Same matching as :meth:`_plan_access`, so the dry-run counts and the
        exported package can never disagree. Quarantined records are listed
        by id and reason instead of by content: the subject learns that data
        is held back and why, without the export handing out a value the
        governance layer has already flagged as untrusted.

        Takes (tenant, subject) rather than the request, because verify has to
        re-derive the package from the audited summary, where no request is
        left to pass.
        """

        matched = DSARService._matches(mem, tenant, subject)
        records = [
            {
                "id": record.id,
                "subject": record.subject,
                "relation": record.relation,
                "object": record.object,
                "text": record.text,
                "source": record.source,
                "provenance": record.provenance,
                # Tuples would serialize differently per JSON encoder; lists
                # keep the hashed package stable across transports.
                "allowed_purposes": list(record.allowed_purposes),
                "consented_purposes": list(record.consented_purposes),
            }
            for record in sorted(
                (item for item in matched if not item.quarantined),
                key=lambda item: item.id,
            )
        ]
        withheld = [
            {"id": record.id, "reason": record.quarantine_reason}
            for record in sorted(
                (item for item in matched if item.quarantined),
                key=lambda item: item.id,
            )
        ]
        return records, withheld

    @staticmethod
    def _package_hash(
        records: List[Dict[str, Any]], withheld: List[Dict[str, Any]]
    ) -> str:
        """Pin the delivered package so a dispute can be settled by hash."""

        payload = canonical_json({"records": records, "withheld": withheld})
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _hold_result(self, mem: Any, request: DSARRequest, held: Any) -> Dict[str, Any]:
        details: Dict[str, Any] = {
            "request_id": request.request_id,
            "kind": request.kind,
            "status": "held",
            "ticket": request.ticket,
            # Carried so a later approve/reject (and the verify after it) works
            # from the registry alone; the request object is long gone by then.
            "term": request.term,
            "pending_id": held.id,
            "reason": held.reason,
        }
        if request.kind == "consent_withdrawal":
            details["purpose"] = request.purpose
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

        Three actions carry a request summary: the execution and the two
        review outcomes. They are replayed in sequence order, so a request
        that was held and then approved rebuilds as approved, exactly as the
        in-process registry would hold it. ``dsar_plan`` (a dry run),
        ``dsar_verify`` (a probe) and ``access_export`` (a disclosure record)
        carry no summary and are skipped.
        """

        if tenant in self._registry_tenants:
            return
        self._registry_tenants.add(tenant)
        for entry in mem.audit.entries():
            if entry.action not in _REGISTRY_ACTIONS:
                continue
            details = dict(entry.details)
            request_id = str(details.get("request_id", ""))
            if request_id:
                self._registry[(tenant, request_id)] = details

    def _record(self, mem: Any, request: DSARRequest, details: Dict[str, Any]) -> None:
        """Audit the first execution and register it for replay.

        The registered summary is the audit entry's own details dict, so a
        rebuild from the trail is lossless by construction. The acting
        requester and the intake channel are carried along so
        :meth:`ticket_payload` can restate the request from the summary alone,
        long after the request object is gone.
        """

        details = dict(details)
        details["requester"] = request.requester
        details["source"] = request.source
        entry = mem.audit.record("dsar_execute", **details)
        self._registry[(request.tenant, request.request_id)] = dict(entry.details)
        self._count_request(details.get("kind"), details.get("status"))

    def _replay(self, mem: Any, tenant: str, summary: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(summary)
        result["tenant"] = tenant
        result["replayed"] = True
        seq = summary.get("certificate_seq")
        if isinstance(seq, int):
            entries = mem.audit.entries()
            if 0 <= seq < len(entries):
                result["certificate"] = entries[seq].to_dict()
        return self._signed(result)

    def _replay_access(
        self, mem: Any, request: DSARRequest, summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Re-derive the access package for an already executed request.

        The first execution stored a hash, not a package, so the replay reads
        current state and reports the comparison honestly: ``hash_drift`` says
        whether the subject's data changed since the export was issued, and
        both hashes are returned so the difference is provable. No second
        audit entry: the disclosure happened once, this is the same one.
        """

        records, withheld = self._access_package(mem, request.tenant, request.subject)
        package_sha256 = self._package_hash(records, withheld)
        recorded_sha256 = str(summary.get("package_sha256", ""))
        return {
            "request_id": request.request_id,
            "kind": "access",
            "tenant": request.tenant,
            "subject": request.subject,
            "status": summary.get("status", ""),
            "replayed": True,
            "records": records,
            "withheld": withheld,
            "record_count": len(records),
            "withheld_count": len(withheld),
            "package_sha256": package_sha256,
            "recorded_package_sha256": recorded_sha256,
            "hash_drift": package_sha256 != recorded_sha256,
        }

    # -- verification -------------------------------------------------------

    def _verify_report(self, mem: Any, tenant: str, request_id: str,
                       summary: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(summary.get("kind", ""))
        status = str(summary.get("status", ""))
        report: Dict[str, Any] = {
            "request_id": request_id,
            "tenant": tenant,
            "kind": kind,
            "status": status,
        }
        if status == "held":
            # Nothing was done yet, so there is nothing to probe: the honest
            # answer is that the request is waiting for a reviewer.
            report["passed"] = False
            report["held_pending_review"] = True
            report["pending_id"] = str(summary.get("pending_id", ""))
            report["reason"] = str(summary.get("reason", ""))
            return report
        if status == "rejected":
            report["passed"] = False
            return report

        if kind == "access":
            checks = self._access_checks(mem, tenant, summary)
            # Drift is expected (the subject's data keeps changing), not a failure;
            # what verify asserts for an export is the audit trail behind it.
            report["passed"] = bool(checks["audit_chain"]["passed"])
        else:
            if kind == "consent_withdrawal":
                checks = self._consent_checks(mem, tenant, summary)
            else:
                checks = self._erasure_checks(mem, tenant, summary)
            report["passed"] = all(check["passed"] for check in checks.values())
        report["checks"] = checks
        return report

    def _erasure_checks(self, mem: Any, tenant: str,
                        summary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        term = str(summary.get("term", ""))
        residual = self._matches(mem, tenant, term)
        probe = mem.recall_value(term, tenant=tenant)
        tombstone = (
            tokenize(term) in mem.erased_terms.get(tenant, [])
            and term in mem.erased_term_texts.get(tenant, [])
        )
        return {
            # Store-side: are matching records physically gone?
            "residual_scan": {
                "passed": len(residual) == 0,
                "residual_count": len(residual),
            },
            # Read-side: even a residual copy must never be served.
            "recall_probe": {"passed": bool(probe.abstained), "reason": probe.reason},
            # Registry-side: the tombstone is what blocks a re-ingest.
            "tombstone_present": {"passed": bool(tombstone)},
            "audit_chain": {"passed": bool(mem.verify_audit())},
            "backend_verification": self._backend_check(mem, summary),
        }

    def _consent_checks(self, mem: Any, tenant: str,
                        summary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        term = str(summary.get("term", ""))
        purpose = str(summary.get("purpose", ""))
        # None, not "": an empty declared purpose would switch purpose gating
        # off entirely instead of asking for the withdrawn purpose.
        result = mem.recall_value(term, tenant=tenant, purpose=purpose or None)
        reasons = sorted({reason for _, reason in result.excluded})
        # Deliberately generic about *which* refusal: an approved withdrawal
        # goes through the shared revocation path and lands as a full
        # restriction (do_not_use) rather than as consent_revoked. The
        # subject's read is refused either way, which is what verify asserts;
        # no_match covers the case where nothing is left to refuse.
        return {
            "refusal": {
                "passed": bool(
                    result.abstained
                    and (
                        bool(_REFUSAL_REASONS & set(reasons))
                        or result.reason == "no_match"
                    )
                ),
                "reasons": reasons,
            },
            "audit_chain": {"passed": bool(mem.verify_audit())},
        }

    def _access_checks(self, mem: Any, tenant: str,
                       summary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        recorded = str(summary.get("package_sha256", ""))
        records, withheld = self._access_package(
            mem, tenant, str(summary.get("subject", ""))
        )
        current = self._package_hash(records, withheld)
        return {
            "audit_chain": {"passed": bool(mem.verify_audit())},
            "package_hash": {
                "recorded": recorded,
                "current": current,
                "drift": current != recorded,
            },
        }

    def _backend_check(self, mem: Any, summary: Dict[str, Any]) -> Dict[str, Any]:
        """Report the backend's own post-delete sweep, when it has one.

        Feature-detected like ``GovernedMemory._execute_forget``: a backend
        without ``verify_erasure`` (and a certificate issued before the sweep
        existed) cannot fail this check, and saying so as ``skipped`` is more
        honest than reporting a pass nobody verified.
        """

        if not callable(getattr(mem.backend, "verify_erasure", None)):
            return {"skipped": True, "passed": True}
        certificate = self._certificate(mem, summary)
        sweep = certificate.get("details", {}).get("backend_verification")
        if not isinstance(sweep, dict):
            return {"skipped": True, "passed": True}
        residual = int(sweep.get("residual", 0))
        return {"skipped": False, "passed": residual == 0, "residual": residual}

    @staticmethod
    def _certificate(mem: Any, summary: Dict[str, Any]) -> Dict[str, Any]:
        """Re-read the erasure certificate the summary points at, if any."""

        seq = summary.get("certificate_seq")
        if not isinstance(seq, int):
            return {}
        entries = mem.audit.entries()
        if 0 <= seq < len(entries):
            return entries[seq].to_dict()
        return {}

    # -- review -------------------------------------------------------------

    def _held_summary(self, mem: Any, tenant: str, request_id: str,
                      action: str) -> Dict[str, Any]:
        self._ensure_registry(mem, tenant)
        summary = self._registry.get((tenant, request_id))
        if summary is None:
            raise KeyError(
                "unknown DSAR request_id %r for tenant %r" % (request_id, tenant)
            )
        status = str(summary.get("status", ""))
        if status != "held":
            raise ValueError(
                "cannot %s DSAR request %r: status is %r, not 'held'"
                % (action, request_id, status)
            )
        return summary

    @staticmethod
    def _review_details(summary: Dict[str, Any], status: str, reviewer: str,
                        pending_id: str) -> Dict[str, Any]:
        """Build the review entry as a full successor summary.

        It carries the identifying fields of the held entry forward, so the
        registry rebuild can take the latest entry per request_id as the whole
        truth instead of merging two partial ones.
        """

        details: Dict[str, Any] = {
            "request_id": str(summary.get("request_id", "")),
            "kind": str(summary.get("kind", "")),
            "status": status,
        }
        for name in ("ticket", "term", "purpose", "subject", "requester", "source"):
            if name in summary:
                details[name] = summary[name]
        details["reviewer"] = reviewer
        details["pending_id"] = pending_id
        return details

    def _register_review(self, mem: Any, tenant: str, request_id: str,
                         action: str, details: Dict[str, Any]) -> None:
        entry = mem.audit.record(action, **details)
        self._registry[(tenant, request_id)] = dict(entry.details)
        self._count_request(details.get("kind"), details.get("status"))

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
