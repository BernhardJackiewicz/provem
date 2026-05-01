from __future__ import annotations

from typing import List

from ..models import Episode, MemoryCandidate, Reflection, RetrievalRequest, RetrievalResult
from .base import AdapterConfigurationError, OptionalDependencyNotInstalled


class LettaBackend:
    """Stub for a future real Letta integration."""

    def __init__(self, base_url: str = "", **_: object) -> None:
        try:
            import letta_client  # noqa: F401
        except ImportError as exc:
            raise OptionalDependencyNotInstalled(
                "LettaBackend requires the optional 'letta' extra. "
                "Install with `pip install -e .[letta]`, then add real Letta "
                "agent configuration and integration tests."
            ) from exc
        if not base_url:
            raise AdapterConfigurationError("LettaBackend requires base_url; real integration is not implemented yet.")

    def ingest(self, episode: Episode) -> None:
        raise NotImplementedError("Real Letta ingestion is not implemented.")

    def propose_memory(self, episode: Episode) -> List[MemoryCandidate]:
        raise NotImplementedError("Real Letta memory proposal is not implemented.")

    def search_memory(self, request: RetrievalRequest) -> RetrievalResult:
        raise NotImplementedError("Real Letta memory search is not implemented.")

    def write_procedure(self, reflection: Reflection) -> None:
        raise NotImplementedError("Real Letta procedure writes are not implemented.")
