"""Mem0-backed :class:`MemoryBackend` for the governance wrapper.

This wires :class:`~cognitive_memory.reliability.GovernedMemory` onto a real
Mem0 store, turning the backend-agnostic *contract* into a runnable deployment
artifact. The point it proves: governance is enforced by the wrapper's read-side
logic, so erasure/scope/quarantine hold **even when the backend rewrites text,
deletes lazily, or refuses to delete at all**.

Key robustness choice: Mem0 extracts and paraphrases memories, so we never rely
on Mem0's stored ``memory`` string for governance. Every governance field
(original text, subject/relation/object, source, trust, provenance, validity,
quarantine state) is mirrored verbatim into Mem0 ``metadata`` and records are
reconstructed from that metadata, not from Mem0's rewrite.

The adapter accepts an injected client (for offline fake-client tests) or lazily
builds a real Mem0 platform client from ``MEM0_API_KEY``. It is intentionally
outside the default import path; nothing here runs unless explicitly used.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..models import lexical_score
from ..reliability import MemoryRecord, Scope


def _record_to_metadata(record: MemoryRecord) -> Dict[str, Any]:
    return {
        "engram_id": record.id,
        "engram_text": record.text,
        "subject": record.subject,
        "relation": record.relation,
        "object": record.object,
        "tenant": record.scope.tenant,
        "scope_subject": record.scope.subject,
        "scope_session": record.scope.session,
        "source": record.source,
        "trust": record.trust,
        "provenance": record.provenance,
        "quarantined": bool(record.quarantined),
        "quarantine_reason": record.quarantine_reason,
        "valid_at": record.valid_at,
        "allowed_purposes": list(record.allowed_purposes),
        "consented_purposes": list(record.consented_purposes),
        "assertions": list(record.assertions),
        "supersedes_ids": list(record.supersedes_ids),
        "contradicts_ids": list(record.contradicts_ids),
    }


def _metadata_to_record(meta: Dict[str, Any], tenant: str) -> Optional[MemoryRecord]:
    if not meta or "engram_text" not in meta:
        return None
    return MemoryRecord(
        subject=str(meta.get("subject", "")),
        relation=str(meta.get("relation", "")),
        object=str(meta.get("object", "")),
        scope=Scope(
            tenant=str(meta.get("tenant", tenant)),
            subject=str(meta.get("scope_subject", "")),
            session=str(meta.get("scope_session", "")),
        ),
        text=str(meta.get("engram_text", "")),
        source=str(meta.get("source", "user")),
        trust=float(meta.get("trust", 0.9)),
        provenance=str(meta.get("provenance", "")),
        quarantined=bool(meta.get("quarantined", False)),
        quarantine_reason=str(meta.get("quarantine_reason", "")),
        valid_at=int(meta.get("valid_at", 0)),
        id=str(meta.get("engram_id", "")),
        allowed_purposes=tuple(str(v) for v in (meta.get("allowed_purposes") or ())),
        consented_purposes=tuple(str(v) for v in (meta.get("consented_purposes") or ())),
        assertions=list(meta.get("assertions") or []),
        supersedes_ids=[str(v) for v in (meta.get("supersedes_ids") or [])],
        contradicts_ids=[str(v) for v in (meta.get("contradicts_ids") or [])],
    )


class Mem0ReliabilityBackend:
    """MemoryBackend contract over a Mem0 client.

    ``tenant`` maps to a namespaced Mem0 ``user_id`` (``<ns>__<tenant>``) so a
    single Mem0 project can host isolated tenants and test runs do not collide.
    """

    name = "mem0_reliability"

    def __init__(
        self,
        client: Optional[object] = None,
        *,
        api_key: Optional[str] = None,
        run_namespace: str = "engram",
        write_settle_seconds: float = 0.0,
        settle_poll_seconds: float = 0.0,
        search_limit: int = 25,
    ) -> None:
        self.run_namespace = run_namespace
        self.write_settle_seconds = write_settle_seconds
        self.settle_poll_seconds = settle_poll_seconds
        self.search_limit = search_limit
        self._counter = 0
        self._user_ids: set = set()
        self._local_ids: Dict[str, str] = {}   # engram_id -> mem0 id (best effort)
        if client is not None:
            self.client = client
        else:
            self.client = self._build_client(api_key)

    def _build_client(self, api_key: Optional[str]) -> object:
        resolved = api_key or os.getenv("MEM0_API_KEY")
        if not resolved:
            raise RuntimeError("Mem0ReliabilityBackend requires api_key or MEM0_API_KEY")
        try:
            from mem0 import MemoryClient  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised only with mem0 installed
            raise RuntimeError("mem0ai is not installed: %s" % exc)
        return MemoryClient(api_key=resolved)

    def _user_id(self, tenant: str) -> str:
        uid = "%s__%s" % (self.run_namespace, tenant)
        self._user_ids.add(uid)
        return uid

    # -- MemoryBackend protocol --------------------------------------------

    def write(self, record: MemoryRecord) -> str:
        self._counter += 1
        record.id = record.id or "m%d" % self._counter
        user_id = self._user_id(record.scope.tenant)
        metadata = _record_to_metadata(record)
        messages = [{"role": "user", "content": record.text}]
        result = self.client.add(messages, user_id=user_id, metadata=metadata)
        mem0_id = self._extract_id(result)
        if mem0_id:
            self._local_ids[record.id] = mem0_id
        if self.write_settle_seconds:
            time.sleep(self.write_settle_seconds)
        return record.id

    def delete_ids(self, ids: Sequence[str]) -> int:
        """Best-effort backend delete. Returns how many the backend confirms gone.

        Governance does NOT depend on this succeeding: erased terms are enforced
        read-side by GovernedMemory regardless of the backend's delete outcome.
        """
        removed = 0
        for engram_id in ids:
            mem0_id = self._local_ids.get(engram_id)
            if not mem0_id:
                continue
            try:
                self.client.delete(memory_id=mem0_id)
                removed += 1
                self._local_ids.pop(engram_id, None)
            except Exception:
                # Backend refused/lagged; read-side erasure still applies.
                continue
        return removed

    def verify_erasure(self, records: Sequence[MemoryRecord], tenant: str) -> Dict[str, int]:
        """Post-delete verification sweep for the best-effort backend.

        delete_ids tolerates a refusing or lagging backend (read-side erasure
        holds regardless), which means copies can silently persist. This
        re-searches each erased record's text, re-deletes surviving copies and
        reports what could not be removed, so the erasure certificate carries
        the honest residual count instead of implying the store is clean.
        """
        user_id = self._user_id(tenant)
        wanted_ids = {record.id for record in records if record.id}
        wanted_texts = {record.text for record in records if record.text}
        seen: set = set()
        resweep_deleted = 0
        residual = 0
        for record in records:
            if not record.text:
                continue
            for raw in self._search(record.text, user_id):
                meta = self._meta_of(raw)
                mem0_id = (raw or {}).get("id")
                if not mem0_id or mem0_id in seen:
                    continue
                if meta.get("engram_id") not in wanted_ids and meta.get("engram_text") not in wanted_texts:
                    continue
                seen.add(mem0_id)
                try:
                    self.client.delete(memory_id=mem0_id)
                    resweep_deleted += 1
                except Exception:
                    residual += 1
        return {"resweep_deleted": resweep_deleted, "residual": residual}

    def all_records(self) -> List[MemoryRecord]:
        records: List[MemoryRecord] = []
        for user_id in sorted(self._user_ids):
            tenant = user_id.split("__", 1)[-1]
            for raw in self._get_all(user_id):
                record = _metadata_to_record(self._meta_of(raw), tenant)
                if record is not None:
                    records.append(record)
        return records

    def get_by_ids(self, ids: Sequence[str]) -> List[MemoryRecord]:
        # No efficient multi-get in the shim; reconstruct from metadata.
        idset = set(ids)
        return [r for r in self.all_records() if r.id in idset]

    def candidates(self, query: str, tenant: str) -> List[Tuple[float, MemoryRecord]]:
        user_id = self._user_id(tenant)
        raws = self._search(query, user_id)
        scored: List[Tuple[float, MemoryRecord]] = []
        for raw in raws:
            record = _metadata_to_record(self._meta_of(raw), tenant)
            if record is None:
                continue
            score = raw.get("score") if isinstance(raw, dict) else None
            if score is None:
                score = lexical_score(query, record.text)
            if score and score > 0:
                scored.append((float(score), record))
        scored.sort(key=lambda item: (item[0], item[1].valid_at), reverse=True)
        return scored

    # -- Mem0 call shims (tolerant of client shape differences) ------------

    def _search(self, query: str, user_id: str) -> List[Any]:
        # v2 platform API wants filters=; older/injected clients want user_id=.
        try:
            results = self.client.search(
                query, version="v2", filters={"user_id": user_id}, top_k=self.search_limit
            )
        except (TypeError, ValueError):
            try:
                results = self.client.search(query, filters={"user_id": user_id}, limit=self.search_limit)
            except (TypeError, ValueError):
                results = self.client.search(query, user_id=user_id, limit=self.search_limit)
        return self._as_list(results)

    def _get_all(self, user_id: str) -> List[Any]:
        getter = getattr(self.client, "get_all", None)
        if getter is None:
            return []
        try:
            results = getter(version="v2", filters={"user_id": user_id})
        except (TypeError, ValueError):
            try:
                results = getter(filters={"user_id": user_id})
            except (TypeError, ValueError):
                results = getter(user_id=user_id)
        return self._as_list(results)

    @staticmethod
    def _as_list(results: Any) -> List[Any]:
        if results is None:
            return []
        if isinstance(results, dict) and "results" in results:
            return list(results["results"])
        if isinstance(results, list):
            return results
        return [results]

    @staticmethod
    def _meta_of(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw.get("metadata") or {})
        return {}

    @staticmethod
    def _extract_id(result: Any) -> str:
        if isinstance(result, dict):
            if "id" in result:
                return str(result["id"])
            results = result.get("results")
            if isinstance(results, list) and results and isinstance(results[0], dict):
                return str(results[0].get("id", ""))
        if isinstance(result, list) and result and isinstance(result[0], dict):
            return str(result[0].get("id", ""))
        return ""

    def purge(self) -> int:
        """Delete all memories created by this run's namespaced tenants."""
        purged = 0
        for user_id in sorted(self._user_ids):
            try:
                self.client.delete_all(user_id=user_id)
                purged += 1
            except (TypeError, ValueError):
                try:
                    self.client.delete_all(filters={"user_id": user_id})
                    purged += 1
                except Exception:
                    continue
            except Exception:
                continue
        return purged
