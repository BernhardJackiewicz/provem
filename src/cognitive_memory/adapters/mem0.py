from __future__ import annotations

import importlib
import os
from typing import Any, Callable, Dict, List, Optional

from ..models import Episode, RetrievalRequest, RetrievalResult, SelectedMemory, clamp
from .base import AdapterConfigurationError, OptionalDependencyNotInstalled


class Mem0Backend:
    """Optional Mem0-backed external memory baseline.

    This adapter is not used by default. It supports injected clients for tests
    and lazy imports for real Mem0 usage.
    """

    name = "mem0_external"

    def __init__(
        self,
        client: Optional[object] = None,
        api_key: Optional[str] = None,
        client_factory: Optional[Callable[[str], object]] = None,
        project_filtering: bool = True,
    ) -> None:
        if client is not None and (api_key or client_factory is not None):
            raise AdapterConfigurationError("Mem0Backend accepts either an injected client or SDK configuration, not both.")

        self.project_filtering = project_filtering
        if client is not None:
            self.client = client
            return

        if client_factory is not None:
            resolved_key = api_key or os.getenv("MEM0_API_KEY")
            if not resolved_key:
                raise AdapterConfigurationError("Mem0Backend requires api_key or MEM0_API_KEY when using a client factory.")
            self.client = client_factory(resolved_key)
            return

        try:
            MemoryClient = getattr(importlib.import_module("mem0"), "MemoryClient")
        except ImportError as exc:
            raise OptionalDependencyNotInstalled(
                "Mem0Backend requires the optional 'mem0' extra. "
                "Install with `pip install -e .[mem0]` and set MEM0_API_KEY, "
                "or inject a test client."
            ) from exc
        except AttributeError as exc:
            raise OptionalDependencyNotInstalled("Installed mem0 package does not expose MemoryClient.") from exc

        resolved_key = api_key or os.getenv("MEM0_API_KEY")
        if not resolved_key:
            raise AdapterConfigurationError("Mem0Backend requires api_key or MEM0_API_KEY.")
        self.client = MemoryClient(api_key=resolved_key)

    def ingest(self, episode: Episode) -> None:
        messages = [{"role": self._role_for_actor(episode.actor), "content": episode.content}]
        metadata = {
            "episode_id": episode.id,
            "project_id": episode.project_id,
            "context_id": episode.context_id,
            "timestamp": episode.timestamp.isoformat(),
            "source": episode.source,
        }
        self.client.add(messages, user_id=episode.user_id, metadata=metadata)

    def search(self, request: RetrievalRequest) -> RetrievalResult:
        raw_results = self._call_search(request)
        selected: List[SelectedMemory] = []
        for index, raw in enumerate(raw_results[: request.top_k]):
            normalized = self._normalize_result(raw, index)
            if normalized is None:
                continue
            memory_id, claim, score, evidence, metadata = normalized
            if self.project_filtering and metadata.get("project_id") not in (None, request.project_id):
                continue
            selected.append(
                SelectedMemory(
                    id=memory_id,
                    memory_type="mem0_memory",
                    claim=claim,
                    score=score,
                    confidence=score,
                    evidence=evidence,
                )
            )

        provenance = sorted({evidence_id for memory in selected for evidence_id in memory.evidence})
        confidence = selected[0].confidence if selected else 0.0
        return RetrievalResult(
            selected_memories=selected,
            provenance=provenance,
            confidence=confidence,
            abstain_recommended=not selected,
            retrieval_trace="mem0 selected=%s" % (",".join(memory.id for memory in selected) or "none"),
        )

    def _call_search(self, request: RetrievalRequest) -> List[Any]:
        filters = {"user_id": request.user_id}
        try:
            results = self.client.search(request.query, filters=filters, limit=request.top_k)
        except TypeError:
            try:
                results = self.client.search(request.query, user_id=request.user_id, limit=request.top_k)
            except TypeError:
                results = self.client.search(request.query, filters=filters)
        if isinstance(results, dict):
            if "results" in results:
                return list(results["results"])
            if "memories" in results:
                return list(results["memories"])
        return list(results or [])

    def _normalize_result(self, raw: Any, index: int) -> Optional[tuple]:
        if isinstance(raw, str):
            return "mem0_%s" % index, raw, 0.5, [], {}
        if not isinstance(raw, dict):
            return None
        claim = raw.get("memory") or raw.get("text") or raw.get("content") or raw.get("value")
        if not claim:
            return None
        memory_id = str(raw.get("id") or raw.get("memory_id") or "mem0_%s" % index)
        raw_score = raw.get("score", raw.get("similarity", raw.get("confidence", 0.5)))
        try:
            score = clamp(float(raw_score))
        except (TypeError, ValueError):
            score = 0.5
        metadata = raw.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        evidence = []
        episode_id = metadata.get("episode_id") or raw.get("episode_id")
        if episode_id:
            evidence.append(str(episode_id))
        return memory_id, str(claim), score, evidence, metadata

    def _role_for_actor(self, actor: str) -> str:
        if actor == "agent":
            return "assistant"
        if actor in ("system", "tool"):
            return "system"
        return "user"
