"""Engram: a hippocampal memory layer research prototype for AI."""

from .controller import MemoryController
from .external_eval import (
    ExternalDatasetConfig,
    ExternalValidationError,
    ValidationManifest,
    evaluate_external_manifest,
    load_validation_manifest,
)
from .models import (
    ConsolidatedMemory,
    ConsolidationCandidate,
    ConsolidationDecision,
    ConsolidationRun,
    Episode,
    EventContext,
    EventParticipant,
    EventRelation,
    MemoryCandidate,
    MemoryEvent,
    Reflection,
    ReviewDecision,
    ReviewItem,
    ReviewQueue,
    ReviewStatus,
    RetrievalRequest,
    RetrievalResult,
    TemporalFact,
)
from .policy import PolicyStore
from .persistence import MemorySnapshot, load_snapshot, retrieval_trace_record, save_snapshot
from .reflection import SleepCycle
from .review import build_review_queue, simulate_review
from .retrieval import RetrievalPlanner
from .store import InMemoryStore
from .transcript_eval import Transcript, TranscriptLabels, evaluate_transcripts, load_transcripts

__all__ = [
    "Episode",
    "ConsolidatedMemory",
    "ConsolidationCandidate",
    "ConsolidationDecision",
    "ConsolidationRun",
    "ExternalDatasetConfig",
    "ExternalValidationError",
    "EventContext",
    "EventParticipant",
    "EventRelation",
    "InMemoryStore",
    "MemoryCandidate",
    "MemoryEvent",
    "MemoryController",
    "MemorySnapshot",
    "PolicyStore",
    "Reflection",
    "ReviewDecision",
    "ReviewItem",
    "ReviewQueue",
    "ReviewStatus",
    "RetrievalPlanner",
    "RetrievalRequest",
    "RetrievalResult",
    "SleepCycle",
    "TemporalFact",
    "Transcript",
    "TranscriptLabels",
    "ValidationManifest",
    "evaluate_external_manifest",
    "evaluate_transcripts",
    "build_review_queue",
    "load_validation_manifest",
    "load_snapshot",
    "load_transcripts",
    "retrieval_trace_record",
    "save_snapshot",
    "simulate_review",
]
