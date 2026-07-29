from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from copy import deepcopy

from .baselines import BaselineResult, FlatLexicalRagBaseline, LongContextLatestBaseline, NoMemoryBaseline
from .controller import MemoryController
from .external_eval import ExternalValidationError, load_validation_manifest
from .extractor import (
    CachedLLMExtractionProvider,
    DEFAULT_LLM_EXTRACT_CACHE_DIR,
    ExtractorSchemaError,
    GenericConversationExtractor,
    OPEN_CONVERSATION_LLM_PROMPT_VERSION,
    OpenAIResponsesExtractionProvider,
    OpenConversationLLMExtractor,
    open_conversation_source_turn,
)
from .models import Episode, RetrievalRequest, lexical_score, tokenize
from .retrieval import OpenConversationRetrievalPlanner, RetrievalPlanner


CATEGORY_NAMES = {
    "1": "multi_hop",
    "2": "single_hop",
    "3": "temporal",
    "4": "open_domain",
    "5": "adversarial",
}

NO_INFORMATION_ANSWERS = {
    "",
    "unknown",
    "not mentioned",
    "not available",
    "no information",
    "no information available",
    "not enough information",
    "i don't know",
}

ANSWER_MODES = {"normal", "diagnostic-synthesis", "synthesis", "llm"}
DIAGNOSTIC_ANSWER_MIN_CONFIDENCE = 0.45


class LoCoMoEvaluationError(ValueError):
    """Raised when local LoCoMo evaluation cannot be run safely."""


@dataclass
class LoCoMoQuestion:
    sample_id: str
    question_id: str
    question: str
    answers: List[str]
    category: str = ""
    category_name: str = "unknown"
    evidence_ids: List[str] = field(default_factory=list)

    @property
    def expected_abstain(self) -> bool:
        if self.category_name == "adversarial":
            return True
        return all(answer.strip().lower() in NO_INFORMATION_ANSWERS for answer in self.answers)


@dataclass
class LoCoMoSample:
    sample_id: str
    episodes: List[Episode]
    questions: List[LoCoMoQuestion]
    ignored_image_count: int = 0


@dataclass(frozen=True)
class DiagnosticAnswerSynthesis:
    answer: str = "ABSTAIN"
    confidence: float = 0.0
    reason: str = "no_direct_answer"
    question_type: str = "object"
    matched_relation: str = ""
    source_memory_id: str = ""


class LoCoMoLoader:
    """Load LoCoMo's text QA data without fetching or using images."""

    def load(self, path: str) -> List[LoCoMoSample]:
        source = Path(path)
        if not source.exists():
            raise LoCoMoEvaluationError(
                "LoCoMo dataset file does not exist: %s. Download it manually from "
                "https://github.com/snap-research/locomo and place locomo10.json under "
                "data/external/locomo/locomo10.json; do not commit the dataset." % path
            )
        with source.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict) and "samples" in payload:
            raw_samples = payload.get("samples", [])
        elif isinstance(payload, list):
            raw_samples = payload
        elif isinstance(payload, dict):
            raw_samples = [payload]
        else:
            raise LoCoMoEvaluationError("LoCoMo JSON must be a sample object, a list, or an object with samples.")
        return [self._sample_from_dict(dict(sample), index + 1) for index, sample in enumerate(raw_samples)]

    def _sample_from_dict(self, sample: Dict[str, Any], fallback_index: int) -> LoCoMoSample:
        sample_id = str(sample.get("sample_id") or sample.get("id") or "locomo_sample_%03d" % fallback_index)
        conversation = dict(sample.get("conversation", {}))
        episodes, ignored_image_count = self._episodes_from_conversation(sample_id, conversation)
        questions = self._questions_from_sample(sample_id, sample)
        return LoCoMoSample(
            sample_id=sample_id,
            episodes=episodes,
            questions=questions,
            ignored_image_count=ignored_image_count,
        )

    def _episodes_from_conversation(self, sample_id: str, conversation: Dict[str, Any]) -> Tuple[List[Episode], int]:
        episodes: List[Episode] = []
        ignored_image_count = 0
        for session_key in _session_keys(conversation):
            session_num = _session_number(session_key)
            timestamp = _parse_locomo_datetime(conversation.get("%s_date_time" % session_key), session_num)
            turns = conversation.get(session_key) or []
            if not isinstance(turns, list):
                continue
            for turn_index, raw_turn in enumerate(turns):
                if not isinstance(raw_turn, dict):
                    continue
                text = str(raw_turn.get("text") or "").strip()
                if raw_turn.get("img_url") or raw_turn.get("blip_caption") or raw_turn.get("query"):
                    ignored_image_count += 1
                if not text:
                    continue
                dia_id = str(raw_turn.get("dia_id") or "%s:%03d" % (session_key, turn_index + 1))
                speaker = str(raw_turn.get("speaker") or "unknown")
                episode = Episode(
                    "%s: %s" % (speaker, text),
                    actor=speaker,
                    source="chat",
                    user_id=sample_id,
                    project_id="locomo",
                    context_id=sample_id,
                    timestamp=timestamp + timedelta(seconds=turn_index),
                )
                episode.id = dia_id
                episodes.append(episode)
        return episodes, ignored_image_count

    def _questions_from_sample(self, sample_id: str, sample: Dict[str, Any]) -> List[LoCoMoQuestion]:
        raw_questions = sample.get("qa") or sample.get("questions") or []
        questions: List[LoCoMoQuestion] = []
        for index, raw_question in enumerate(raw_questions):
            if not isinstance(raw_question, dict):
                continue
            question = str(raw_question.get("question") or raw_question.get("query") or "").strip()
            if not question:
                continue
            category = str(raw_question.get("category", ""))
            answers = _answers_from_raw(raw_question)
            questions.append(
                LoCoMoQuestion(
                    sample_id=sample_id,
                    question_id=str(raw_question.get("question_id") or raw_question.get("qid") or "%s_q%03d" % (sample_id, index + 1)),
                    question=question,
                    answers=answers,
                    category=category,
                    category_name=_category_name(category),
                    evidence_ids=[str(item) for item in raw_question.get("evidence", [])],
                )
            )
        return questions


class LoCoMoCognitiveSystem:
    name = "cognitive_memory_layer"

    def __init__(
        self,
        extract: bool = False,
        retrieval_mode: str = "governed",
        extractor_mode: str = "rule-based",
        answer_mode: str = "normal",
        recall_boost: bool = False,
        llm_provider: Optional[Callable[[Dict[str, Any]], object]] = None,
        llm_answer_provider: Optional[Callable[[str], str]] = None,
    ) -> None:
        if retrieval_mode not in ("governed", "hybrid"):
            raise LoCoMoEvaluationError("Unsupported LoCoMo retrieval mode: %s" % retrieval_mode)
        if extractor_mode not in ("rule-based", "llm"):
            raise LoCoMoEvaluationError("Unsupported LoCoMo extractor: %s" % extractor_mode)
        if answer_mode not in ANSWER_MODES:
            raise LoCoMoEvaluationError("Unsupported LoCoMo answer mode: %s" % answer_mode)
        if not extract and extractor_mode != "rule-based":
            raise LoCoMoEvaluationError("--extractor %s requires --extract." % extractor_mode)
        if recall_boost and retrieval_mode != "hybrid":
            raise LoCoMoEvaluationError("--recall-boost requires --retrieval-mode hybrid.")
        extractor = _locomo_extractor(extract=extract, extractor_mode=extractor_mode, llm_provider=llm_provider)
        self.controller = MemoryController(extractor=extractor)
        if retrieval_mode == "hybrid":
            if recall_boost:
                # opt-in BM25 + verbatim-turn indexing with recalibrated thresholds
                self.retrieval = OpenConversationRetrievalPlanner(
                    self.controller.store,
                    self.controller.policy,
                    include_verbatim=True,
                    use_bm25=True,
                    min_top_score=0.78,
                    min_confidence=0.40,
                )
            else:
                self.retrieval = OpenConversationRetrievalPlanner(self.controller.store, self.controller.policy)
        else:
            self.retrieval = RetrievalPlanner(self.controller.store, self.controller.policy)
        self.extract = extract
        self.retrieval_mode = retrieval_mode
        self.extractor_mode = extractor_mode if extract else "none"
        self.answer_mode = answer_mode
        self.ingested_candidates = 0
        self._llm_answerer = None
        if answer_mode == "llm":
            from .answerer import ExtractiveAnswerer, LLMAnswerer

            provider = llm_answer_provider
            if provider is None:
                from .llm_answer_provider import OpenAIAnswerProvider

                provider = OpenAIAnswerProvider.from_env()
            self._llm_answerer = LLMAnswerer(provider, fallback=ExtractiveAnswerer())

    def ingest(self, episode: Episode) -> None:
        candidates = self.controller.ingest_episode(episode)
        self.ingested_candidates += len(candidates)

    def _evidence_text_for(self, record: Dict[str, Any]) -> str:
        """Best available verbatim text for a selected memory.

        Verbatim episodes carry the turn text in ``claim``; extracted facts point
        at their source episode(s), whose raw text we fetch from the store.
        """
        if record.get("memory_type") == "episode":
            return str(record.get("claim") or "")
        for episode_id in record.get("evidence", []):
            episode = self.controller.store.get_episode(str(episode_id))
            if episode is not None and episode.content:
                return episode.content
        return str(record.get("claim") or "")

    def _synthesize_answer(self, question, selected_records, result):
        """Template synthesis first, then a concise extractive span fallback.

        Converts the retrieval layer's verbatim hits into short answer spans that
        token-F1 / substring scoring can credit, instead of emitting whole turns.
        """
        from .answer import extractive_span, question_type

        fields: Dict[str, Any] = {}
        synthesis = _synthesize_diagnostic_answer_result(question, selected_records)
        fields["synthesis_question_type"] = synthesis.question_type
        qtype = synthesis.question_type or question_type(question)

        # yes/no needs semantic entailment we do not attempt -> abstain safely.
        if qtype == "yes_no":
            return "ABSTAIN", "yes_no_requires_semantic_entailment", fields

        if result.abstain_recommended:
            return "ABSTAIN", result.abstain_reason or "retrieval_abstained", fields

        if synthesis.answer != "ABSTAIN" and synthesis.confidence >= DIAGNOSTIC_ANSWER_MIN_CONFIDENCE:
            fields["synthesis_source"] = "template"
            return synthesis.answer, "", fields

        # extractive fallback: pull a short span from the highest-scored evidence
        for record in selected_records:
            text = self._evidence_text_for(record)
            span = extractive_span(question, text, qtype=qtype)
            if span:
                fields["synthesis_source"] = "extractive"
                fields["synthesis_source_memory_id"] = str(record.get("id") or "")
                return span, "", fields

        return "ABSTAIN", synthesis.reason or "no_synthesizable_answer", fields

    def answer(self, request: RetrievalRequest) -> BaselineResult:
        result = self.retrieval.retrieve(request)
        answer = result.answer_text()
        abstain_reason = result.abstain_reason
        normalized_fields = {
            "confidence": result.confidence,
            "retrieval_mode": self.retrieval_mode,
            "answer_mode": self.answer_mode,
        }
        normalized_fields.update(result.metadata)
        selected_memories = [memory.to_dict() for memory in result.selected_memories]
        if self.answer_mode == "diagnostic-synthesis":
            selected_records = [
                _selected_memory_record(self.controller.store, selected)
                for selected in selected_memories
            ]
            synthesis = _synthesize_diagnostic_answer_result(request.query, selected_records)
            normalized_fields.update(
                {
                    "diagnostic_synthesized_answer": synthesis.answer,
                    "diagnostic_synthesis_confidence": synthesis.confidence,
                    "diagnostic_synthesis_reason": synthesis.reason,
                    "diagnostic_synthesis_question_type": synthesis.question_type,
                    "diagnostic_synthesis_relation": synthesis.matched_relation,
                    "diagnostic_synthesis_source_memory_id": synthesis.source_memory_id,
                }
            )
            if result.abstain_recommended:
                answer = "ABSTAIN"
            elif synthesis.answer != "ABSTAIN" and synthesis.confidence >= DIAGNOSTIC_ANSWER_MIN_CONFIDENCE:
                answer = synthesis.answer
                abstain_reason = ""
            else:
                answer = "ABSTAIN"
                abstain_reason = synthesis.reason or "diagnostic_synthesis_no_direct_answer"
        elif self.answer_mode == "synthesis":
            selected_records = [
                _selected_memory_record(self.controller.store, selected)
                for selected in selected_memories
            ]
            answer, abstain_reason, synth_fields = self._synthesize_answer(
                request.query, selected_records, result
            )
            normalized_fields.update(synth_fields)
        elif self.answer_mode == "llm":
            selected_records = [
                _selected_memory_record(self.controller.store, selected)
                for selected in selected_memories
            ]
            if result.abstain_recommended or not selected_records:
                answer = "ABSTAIN"
                abstain_reason = result.abstain_reason or "retrieval_abstained"
            else:
                # Feed the LLM the governed, cleared evidence turns only.
                memories = [
                    {"text": self._evidence_text_for(record), "object": record.get("object", "")}
                    for record in selected_records
                ]
                llm_answer = self._llm_answerer.answer(request.query, memories)
                if llm_answer and llm_answer.upper() != "ABSTAIN":
                    answer = llm_answer
                    abstain_reason = ""
                else:
                    answer = "ABSTAIN"
                    abstain_reason = "llm_no_answer"
                normalized_fields["synthesis_source"] = "llm"
        return BaselineResult(
            answer,
            result.retrieval_trace,
            provenance=result.provenance,
            abstain_reason=abstain_reason,
            selected_memories=selected_memories,
            normalized_fields=normalized_fields,
        )


def _locomo_extractor(
    extract: bool,
    extractor_mode: str,
    llm_provider: Optional[Callable[[Dict[str, Any]], object]] = None,
) -> Optional[Any]:
    if not extract:
        return None
    if extractor_mode == "rule-based":
        return GenericConversationExtractor()
    if extractor_mode != "llm":
        raise LoCoMoEvaluationError("Unsupported LoCoMo extractor: %s" % extractor_mode)
    try:
        provider = llm_provider or OpenAIResponsesExtractionProvider.from_env()
    except ValueError as exc:
        raise LoCoMoEvaluationError(str(exc)) from exc
    return OpenConversationLLMExtractor(provider)


def locomo_path_from_manifest(manifest_path: str) -> str:
    """Resolve a reviewed local LoCoMo dataset path from an external manifest."""

    manifest = load_validation_manifest(manifest_path)
    locomo_datasets = [
        dataset
        for dataset in manifest.datasets
        if (dataset.loader.lower() or dataset.expected_schema.lower()) in ("locomo_json", "locomo")
    ]
    if not locomo_datasets:
        raise LoCoMoEvaluationError("Manifest has no dataset with expected_schema or loader 'locomo_json'.")
    if len(locomo_datasets) > 1:
        raise LoCoMoEvaluationError("Manifest has multiple LoCoMo datasets; use one per manifest for now.")
    dataset = locomo_datasets[0]
    base = Path(manifest.manifest_path).parent
    local_path = Path(dataset.local_path)
    if not local_path.is_absolute():
        local_path = base / local_path
    return str(local_path.resolve())


def _select_samples(samples: Sequence[LoCoMoSample], sample_ids: Optional[Sequence[str]]) -> List[LoCoMoSample]:
    if not sample_ids:
        return list(samples)
    wanted = [str(sample_id).strip() for sample_id in sample_ids if str(sample_id).strip()]
    if not wanted:
        return list(samples)
    wanted_set = set(wanted)
    selected = [sample for sample in samples if sample.sample_id in wanted_set]
    missing = sorted(wanted_set - {sample.sample_id for sample in selected})
    if missing:
        raise LoCoMoEvaluationError("Unknown LoCoMo sample id(s): %s" % ", ".join(missing))
    return selected


def _apply_turn_limit(samples: Sequence[LoCoMoSample], max_turns: Optional[int]) -> List[LoCoMoSample]:
    if max_turns is None:
        return list(samples)
    remaining = max(max_turns, 0)
    limited: List[LoCoMoSample] = []
    for sample in samples:
        kept = sample.episodes[:remaining] if remaining > 0 else []
        remaining -= len(kept)
        limited.append(
            LoCoMoSample(
                sample_id=sample.sample_id,
                episodes=kept,
                questions=list(sample.questions),
                ignored_image_count=sample.ignored_image_count,
            )
        )
    return limited


def _filter_qas_to_evidence_window(
    samples: Sequence[LoCoMoSample],
) -> Tuple[List[LoCoMoSample], Dict[str, Any]]:
    before = sum(len(sample.questions) for sample in samples)
    samples_before = len(samples)
    filtered: List[LoCoMoSample] = []
    dropped_question_ids: List[str] = []
    for sample in samples:
        selected_episode_ids = {episode.id for episode in sample.episodes}
        kept_questions = [
            question
            for question in sample.questions
            if question.evidence_ids and set(question.evidence_ids).issubset(selected_episode_ids)
        ]
        dropped_question_ids.extend(
            question.question_id
            for question in sample.questions
            if question not in kept_questions
        )
        if kept_questions:
            filtered.append(
                LoCoMoSample(
                    sample_id=sample.sample_id,
                    episodes=list(sample.episodes),
                    questions=kept_questions,
                    ignored_image_count=sample.ignored_image_count,
                )
            )
    after = sum(len(sample.questions) for sample in filtered)
    if before > 0 and after == 0:
        raise LoCoMoEvaluationError(
            "--qa-evidence-in-window-only removed all QA items. Increase --max-turns, choose another sample, or disable the filter."
        )
    return filtered, {
        "enabled": True,
        "samples_before_window_filter": samples_before,
        "samples_after_window_filter": len(filtered),
        "qa_before_window_filter": before,
        "qa_after_window_filter": after,
        "qa_dropped_outside_window": before - after,
        "dropped_question_ids": dropped_question_ids[:25],
    }


def _qa_window_summary_disabled(samples: Sequence[LoCoMoSample]) -> Dict[str, Any]:
    qa_count = sum(len(sample.questions) for sample in samples)
    return {
        "enabled": False,
        "samples_before_window_filter": len(samples),
        "samples_after_window_filter": len(samples),
        "qa_before_window_filter": qa_count,
        "qa_after_window_filter": qa_count,
        "qa_dropped_outside_window": 0,
        "dropped_question_ids": [],
    }


def _llm_model_name(llm_provider: Optional[Callable[[Dict[str, Any]], object]]) -> str:
    if llm_provider is not None:
        return str(getattr(llm_provider, "model", "") or "injected")
    return (
        OpenAIResponsesExtractionProvider.from_env_model()
        if hasattr(OpenAIResponsesExtractionProvider, "from_env_model")
        else ""
    ) or "unconfigured"


def _plan_llm_extraction(
    samples: Sequence[LoCoMoSample],
    cache_dir: str,
    model: str,
    dry_run: bool,
) -> Dict[str, Any]:
    cache_probe = CachedLLMExtractionProvider(
        lambda source_turn: {"memories": []},
        cache_dir=cache_dir,
        model=model,
        prompt_schema_version=OPEN_CONVERSATION_LLM_PROMPT_VERSION,
    )
    turns = [episode for sample in samples for episode in sample.episodes]
    cache_hits = 0
    cache_misses = 0
    for episode in turns:
        source_turn = open_conversation_source_turn(episode)
        if cache_probe.has_valid_cache(source_turn):
            cache_hits += 1
        else:
            cache_misses += 1
    return {
        "enabled": True,
        "dry_run": dry_run,
        "model": model,
        "prompt_schema_version": OPEN_CONVERSATION_LLM_PROMPT_VERSION,
        "cache_dir": str(Path(cache_dir)),
        "samples_selected": len(samples),
        "sample_ids": [sample.sample_id for sample in samples],
        "turns_selected": len(turns),
        "api_calls_planned": cache_misses,
        "api_calls_made": 0,
        "cache_hits_planned": cache_hits,
        "cache_misses_planned": cache_misses,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_read_errors": cache_probe.cache_read_errors,
        "rejected_llm_outputs": 0,
        "accepted_memories": 0,
        "accepted_facts": 0,
        "accepted_events": 0,
        "evidence_memory_recall": 0.0,
        "top_k_evidence_hit_rate": 0.0,
        "unsafe_answer_rate": 0.0,
        "accuracy": 0.0,
    }


def _validate_llm_call_budget(llm_report: Dict[str, Any], max_api_calls: Optional[int]) -> None:
    if llm_report.get("dry_run"):
        if max_api_calls is not None:
            llm_report["would_exceed_max_api_calls"] = int(llm_report.get("api_calls_planned", 0)) > max_api_calls
        return
    planned = int(llm_report.get("api_calls_planned", 0))
    turns = int(llm_report.get("turns_selected", 0))
    if max_api_calls is not None and planned > max_api_calls:
        raise LoCoMoEvaluationError(
            "LLM extraction would require %s uncached API calls, exceeding --max-api-calls %s. "
            "Use a smaller subset or warm the cache first." % (planned, max_api_calls)
        )
    if max_api_calls is None and planned > 0 and turns > 500:
        raise LoCoMoEvaluationError(
            "Refusing an unbounded LLM extraction run over %s turns with %s uncached calls. "
            "Use --max-samples, --sample-ids, --max-turns, --dry-run-cost-estimate, or --max-api-calls."
            % (turns, planned)
        )


def _dry_run_locomo_report(
    path: str,
    samples: Sequence[LoCoMoSample],
    extract: bool,
    retrieval_mode: str,
    answer_mode: str,
    qa_window_summary: Dict[str, Any],
    llm_extraction_report: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "dataset": Path(path).name,
        "sample_count": len(samples),
        "qa_count": sum(len(sample.questions) for sample in samples),
        "qa_evaluated": 0,
        "ignored_image_count": sum(sample.ignored_image_count for sample in samples),
        "extraction_enabled": extract,
        "extractor": "llm",
        "retrieval_mode": retrieval_mode,
        "answer_mode": answer_mode,
        "qa_window_filter": qa_window_summary,
        "summary": {},
        "scores": [],
        "diagnostics": {},
        "llm_extraction": llm_extraction_report,
    }


def _update_llm_extraction_report(
    llm_report: Dict[str, Any],
    diagnostics: Iterable[Dict[str, Any]],
    report: Dict[str, Any],
) -> None:
    totals = _diagnostic_totals(diagnostics)
    stage = dict(report.get("stage_summary", {}).get("cognitive_memory_layer", {}))
    cml = dict(report.get("summary", {}).get("cognitive_memory_layer", {}))
    llm_report["rejected_llm_outputs"] = int(totals.get("llm_rejections", 0))
    llm_report["accepted_facts"] = int(totals.get("accepted_facts", 0))
    llm_report["accepted_events"] = int(totals.get("accepted_events", 0))
    llm_report["accepted_memories"] = int(totals.get("accepted_facts", 0)) + int(totals.get("accepted_events", 0))
    llm_report["evidence_memory_recall"] = float(stage.get("evidence_memory_recall", 0.0))
    llm_report["top_k_evidence_hit_rate"] = float(stage.get("top_k_evidence_hit_rate", 0.0))
    llm_report["unsafe_answer_rate"] = float(stage.get("unsafe_answer_rate", 0.0))
    llm_report["accuracy"] = float(cml.get("locomo_qa_accuracy", 0.0))


def evaluate_locomo(
    path: str,
    limit_samples: Optional[int] = None,
    max_samples: Optional[int] = None,
    limit_qa: Optional[int] = None,
    max_turns: Optional[int] = None,
    max_api_calls: Optional[int] = None,
    sample_ids: Optional[Sequence[str]] = None,
    dry_run_cost_estimate: bool = False,
    extract: bool = False,
    diagnostics: bool = False,
    stage_report: bool = False,
    retrieval_mode: str = "governed",
    extractor_mode: str = "rule-based",
    answer_mode: str = "normal",
    recall_boost: bool = False,
    qa_evidence_in_window_only: bool = False,
    llm_provider: Optional[Callable[[Dict[str, Any]], object]] = None,
    llm_answer_provider: Optional[Callable[[str], str]] = None,
    llm_cache_dir: str = DEFAULT_LLM_EXTRACT_CACHE_DIR,
) -> Dict[str, Any]:
    if retrieval_mode not in ("governed", "hybrid"):
        raise LoCoMoEvaluationError("Unsupported LoCoMo retrieval mode: %s" % retrieval_mode)
    if extractor_mode not in ("rule-based", "llm"):
        raise LoCoMoEvaluationError("Unsupported LoCoMo extractor: %s" % extractor_mode)
    if answer_mode not in ANSWER_MODES:
        raise LoCoMoEvaluationError("Unsupported LoCoMo answer mode: %s" % answer_mode)
    if not extract and extractor_mode != "rule-based":
        raise LoCoMoEvaluationError("--extractor %s requires --extract." % extractor_mode)
    if max_samples is not None and limit_samples is not None:
        raise LoCoMoEvaluationError("Use either --limit-samples or --max-samples, not both.")
    for name, value in (
        ("limit_samples", limit_samples),
        ("max_samples", max_samples),
        ("limit_qa", limit_qa),
        ("max_turns", max_turns),
        ("max_api_calls", max_api_calls),
    ):
        if value is not None and value < 0:
            raise LoCoMoEvaluationError("%s must be non-negative." % name)
    if dry_run_cost_estimate and not (extract and extractor_mode == "llm"):
        raise LoCoMoEvaluationError("--dry-run-cost-estimate requires --extract --extractor llm.")
    samples = LoCoMoLoader().load(path)
    samples = _select_samples(samples, sample_ids)
    sample_limit = max_samples if max_samples is not None else limit_samples
    if sample_limit is not None:
        samples = samples[: max(sample_limit, 0)]
    samples = _apply_turn_limit(samples, max_turns)
    if qa_evidence_in_window_only:
        samples, qa_window_summary = _filter_qas_to_evidence_window(samples)
    else:
        qa_window_summary = _qa_window_summary_disabled(samples)

    llm_extraction_report: Optional[Dict[str, Any]] = None
    llm_cache_provider: Optional[CachedLLMExtractionProvider] = None
    if extract and extractor_mode == "llm":
        model = _llm_model_name(llm_provider)
        llm_extraction_report = _plan_llm_extraction(
            samples=samples,
            cache_dir=llm_cache_dir,
            model=model,
            dry_run=dry_run_cost_estimate,
        )
        llm_extraction_report["max_api_calls"] = max_api_calls
        _validate_llm_call_budget(llm_extraction_report, max_api_calls)
        if dry_run_cost_estimate:
            return _dry_run_locomo_report(
                path=path,
                samples=samples,
                extract=extract,
                retrieval_mode=retrieval_mode,
                answer_mode=answer_mode,
                qa_window_summary=qa_window_summary,
                llm_extraction_report=llm_extraction_report,
            )
        try:
            raw_provider = llm_provider or OpenAIResponsesExtractionProvider.from_env()
        except ValueError as exc:
            raise LoCoMoEvaluationError(str(exc)) from exc
        model = str(getattr(raw_provider, "model", model) or model)
        llm_cache_provider = CachedLLMExtractionProvider(
            raw_provider,
            cache_dir=llm_cache_dir,
            model=model,
            prompt_schema_version=OPEN_CONVERSATION_LLM_PROMPT_VERSION,
        )
        llm_provider = llm_cache_provider

    system_factories: List[Callable[[], Any]] = [
        NoMemoryBaseline,
        FlatLexicalRagBaseline,
        LongContextLatestBaseline,
        lambda: LoCoMoCognitiveSystem(
            extract=extract,
            retrieval_mode=retrieval_mode,
            extractor_mode=extractor_mode,
            answer_mode=answer_mode,
            recall_boost=recall_boost,
            llm_provider=llm_provider,
            llm_answer_provider=llm_answer_provider,
        ),
    ]
    scores: List[Dict[str, Any]] = []
    diagnostics_by_sample: Dict[str, Dict[str, Any]] = {}
    llm_diagnostics_by_sample: Dict[str, Dict[str, Any]] = {}
    stage_diagnostics: Dict[str, List[Dict[str, Any]]] = {"cognitive_memory_layer": []}
    qa_evaluated = 0
    for sample in samples:
        questions = sample.questions
        if limit_qa is not None:
            remaining = max(limit_qa - qa_evaluated, 0)
            questions = questions[:remaining]
        if not questions:
            continue
        for factory in system_factories:
            system = factory()
            for episode in sample.episodes:
                system.ingest(deepcopy(episode))
            if (diagnostics or llm_extraction_report is not None) and system.name == "cognitive_memory_layer":
                sample_diagnostic = _sample_diagnostics(sample, system)
                if diagnostics:
                    diagnostics_by_sample[sample.sample_id] = sample_diagnostic
                if llm_extraction_report is not None:
                    llm_diagnostics_by_sample[sample.sample_id] = sample_diagnostic
            for question in questions:
                request = RetrievalRequest(
                    query=question.question,
                    user_id=sample.sample_id,
                    project_id="locomo",
                    task_type="temporal" if question.category_name == "temporal" else "general",
                    top_k=3,
                )
                started = time.perf_counter()
                result = system.answer(request)
                latency_ms = (time.perf_counter() - started) * 1000.0
                score = _score_question(system.name, sample, question, result, latency_ms)
                scores.append(score)
                if stage_report and system.name == "cognitive_memory_layer":
                    stage_diagnostics["cognitive_memory_layer"].append(
                        _stage_diagnostic(sample, question, result, score, system)
                    )
                if diagnostics:
                    _update_diagnostics_from_score(diagnostics_by_sample, score)
        qa_evaluated += len(questions)
        if limit_qa is not None and qa_evaluated >= limit_qa:
            break

    report = {
        "dataset": Path(path).name,
        "sample_count": len(samples),
        "qa_count": sum(len(sample.questions) for sample in samples),
        "qa_evaluated": qa_evaluated,
        "ignored_image_count": sum(sample.ignored_image_count for sample in samples),
        "extraction_enabled": extract,
        "extractor": extractor_mode if extract else "none",
        "retrieval_mode": retrieval_mode,
        "answer_mode": answer_mode,
        "qa_window_filter": qa_window_summary,
        "summary": _summarize(scores),
        "scores": scores,
        "diagnostics": diagnostics_by_sample if diagnostics else {},
    }
    if stage_report:
        report["stage_diagnostics"] = stage_diagnostics
        report["stage_summary"] = _summarize_stage(stage_diagnostics)
    if stage_report or diagnostics:
        report["extractor_summary"] = _extractor_summary(report)
    if llm_extraction_report is not None:
        if llm_cache_provider is not None:
            llm_extraction_report.update(llm_cache_provider.stats())
        _update_llm_extraction_report(
            llm_extraction_report,
            diagnostics=llm_diagnostics_by_sample.values(),
            report=report,
        )
        report["llm_extraction"] = llm_extraction_report
    return report


def dumps_locomo_report(report: Dict[str, Any], as_json: bool = False) -> str:
    if as_json:
        return json.dumps(report, indent=2, sort_keys=True)
    lines = [
        "LoCoMo evaluation",
        "dataset: %s" % report.get("dataset", ""),
        "samples: %s qa_evaluated: %s ignored_image_fields: %s"
        % (report.get("sample_count", 0), report.get("qa_evaluated", 0), report.get("ignored_image_count", 0)),
        "mode: text-only QA; images and BLIP captions ignored",
        "generic_extraction: %s" % ("enabled" if report.get("extraction_enabled") else "disabled"),
        "extractor: %s" % report.get("extractor", "none"),
        "retrieval_mode: %s" % report.get("retrieval_mode", "governed"),
        "answer_mode: %s" % report.get("answer_mode", "normal"),
        "",
    ]
    qa_window = report.get("qa_window_filter") or {}
    if qa_window.get("enabled"):
        lines.extend(
            [
                "Evidence-window QA filter:",
                "- qa_before_window_filter: %s qa_after_window_filter: %s dropped: %s"
                % (
                    qa_window.get("qa_before_window_filter", 0),
                    qa_window.get("qa_after_window_filter", 0),
                    qa_window.get("qa_dropped_outside_window", 0),
                ),
                "",
            ]
        )
    llm_extraction = report.get("llm_extraction") or {}
    if llm_extraction:
        lines.extend(
            [
                "LLM extraction cost/safety:",
                "- dry_run: %s" % llm_extraction.get("dry_run", False),
                "- model: %s" % llm_extraction.get("model", ""),
                "- cache_dir: %s" % llm_extraction.get("cache_dir", ""),
                "- samples_selected: %s turns_selected: %s"
                % (llm_extraction.get("samples_selected", 0), llm_extraction.get("turns_selected", 0)),
                "- api_calls_planned: %s api_calls_made: %s"
                % (llm_extraction.get("api_calls_planned", 0), llm_extraction.get("api_calls_made", 0)),
                "- max_api_calls: %s would_exceed_max_api_calls: %s"
                % (llm_extraction.get("max_api_calls", ""), llm_extraction.get("would_exceed_max_api_calls", False)),
                "- cache_hits: %s cache_misses: %s planned_hits: %s planned_misses: %s"
                % (
                    llm_extraction.get("cache_hits", 0),
                    llm_extraction.get("cache_misses", 0),
                    llm_extraction.get("cache_hits_planned", 0),
                    llm_extraction.get("cache_misses_planned", 0),
                ),
                "- rejected_llm_outputs: %s accepted_memories: %s"
                % (llm_extraction.get("rejected_llm_outputs", 0), llm_extraction.get("accepted_memories", 0)),
                "- evidence_memory_recall: %.2f top_k_hit: %.2f unsafe_answer: %.2f accuracy: %.2f"
                % (
                    llm_extraction.get("evidence_memory_recall", 0.0),
                    llm_extraction.get("top_k_evidence_hit_rate", 0.0),
                    llm_extraction.get("unsafe_answer_rate", 0.0),
                    llm_extraction.get("accuracy", 0.0),
                ),
                "",
            ]
        )
    for system, metrics in sorted(report.get("summary", {}).items()):
        lines.append(
            "- %s: accuracy %.2f, f1 %.2f, evidence %.2f, temporal %.2f, multi_session %.2f, abstention %.2f, provenance %.2f, p95 %.3f ms"
            % (
                system,
                metrics.get("locomo_qa_accuracy", 0.0),
                metrics.get("mean_token_f1", 0.0),
                metrics.get("evidence_recall", 0.0),
                metrics.get("temporal_question_accuracy", 0.0),
                metrics.get("multi_session_question_accuracy", 0.0),
                metrics.get("abstention_accuracy", 0.0),
                metrics.get("provenance_coverage", 0.0),
                metrics.get("p95_latency_ms", 0.0),
            )
        )
    diagnostics = report.get("diagnostics") or {}
    if diagnostics:
        totals = _diagnostic_totals(diagnostics.values())
        lines.extend(
            [
                "",
                "Diagnostics:",
                "- episodes_created: %s" % totals["episodes_created"],
                "- candidates_extracted: %s" % totals["candidates_extracted"],
                "- accepted_facts: %s" % totals["accepted_facts"],
                "- accepted_events: %s" % totals["accepted_events"],
                "- ignored_or_rejected_candidates: %s" % totals["ignored_or_rejected_candidates"],
                "- llm_candidates: %s" % totals["llm_candidates"],
                "- llm_rejections: %s" % totals["llm_rejections"],
                "- retrieval_attempts: %s" % totals["retrieval_attempts"],
                "- abstentions: %s" % totals["abstentions"],
                "- relations: %s" % ", ".join("%s=%s" % item for item in sorted(totals["relations"].items())[:12]),
                "- candidate_types: %s" % ", ".join("%s=%s" % item for item in sorted(totals["candidate_types"].items())[:12]),
                "- llm_rejection_reasons: %s"
                % ", ".join("%s=%s" % item for item in sorted(totals["llm_rejection_reasons"].items())[:12]),
            ]
        )
    stage_summary = report.get("stage_summary") or {}
    if stage_summary:
        lines.append("")
        lines.append("Stage diagnostics:")
        for system, metrics in sorted(stage_summary.items()):
            lines.append(
                "- %s: locomo %.2f, evidence_memory_recall %.2f, retrieval_evidence_recall %.2f, top_k_hit %.2f, synth_success %.2f, correct_abstention %.2f, missing_extraction %.2f, retrieval_miss %.2f, unsafe_answer %.2f"
                % (
                    system,
                    metrics.get("locomo_accuracy", 0.0),
                    metrics.get("evidence_memory_recall", 0.0),
                    metrics.get("retrieval_evidence_recall", 0.0),
                    metrics.get("top_k_evidence_hit_rate", 0.0),
                    metrics.get("answer_synthesis_success_rate", 0.0),
                    metrics.get("correct_abstention_rate", 0.0),
                    metrics.get("missing_extraction_rate", 0.0),
                    metrics.get("retrieval_miss_rate", 0.0),
                    metrics.get("unsafe_answer_rate", 0.0),
                )
            )
    extractor_summary = report.get("extractor_summary") or {}
    if extractor_summary:
        lines.extend(["", "Extractor diagnostics:"])
        lines.append(
            "- mode: %s final_accuracy %.2f evidence_memory_recall %.2f precision %.2f hallucination_rejection %.2f unsupported_rejection %.2f unsafe_answer %.2f"
            % (
                extractor_summary.get("extractor", "none"),
                extractor_summary.get("final_accuracy", 0.0),
                extractor_summary.get("evidence_memory_recall", 0.0),
                extractor_summary.get("extraction_precision_sample", 0.0),
                extractor_summary.get("hallucination_rejection_rate", 0.0),
                extractor_summary.get("unsupported_memory_rejection_rate", 0.0),
                extractor_summary.get("unsafe_answer_rate", 0.0),
            )
        )
    return "\n".join(lines)


def _score_question(
    system_name: str,
    sample: LoCoMoSample,
    question: LoCoMoQuestion,
    result: BaselineResult,
    latency_ms: float,
) -> Dict[str, Any]:
    answer = result.answer
    actual_abstain = answer.strip().lower() == "abstain"
    f1 = 1.0 if question.expected_abstain and actual_abstain else _best_token_f1(answer, question.answers)
    substring_match = _has_answer_substring(answer, question.answers)
    passed = actual_abstain if question.expected_abstain else (substring_match or f1 >= 0.5)
    evidence_recall = _evidence_recall(result.provenance, question.evidence_ids)
    return {
        "sample_id": sample.sample_id,
        "question_id": question.question_id,
        "question": question.question,
        "category": question.category,
        "category_name": question.category_name,
        "system": system_name,
        "passed": passed,
        "answer": answer,
        "expected_answers": list(question.answers),
        "expected_abstain": question.expected_abstain,
        "actual_abstain": actual_abstain,
        "token_f1": f1,
        "evidence_ids": list(question.evidence_ids),
        "provenance": list(result.provenance),
        "evidence_recall": evidence_recall,
        "multi_session": _evidence_spans_sessions(question.evidence_ids),
        "latency_ms": latency_ms,
        "trace": result.trace,
        "abstain_reason": result.abstain_reason,
        "retrieval_mode": str(result.normalized_fields.get("retrieval_mode") or ""),
        "answer_mode": str(result.normalized_fields.get("answer_mode") or "normal"),
    }


def _sample_diagnostics(sample: LoCoMoSample, system: LoCoMoCognitiveSystem) -> Dict[str, Any]:
    store = system.controller.store
    candidates = list(store.candidates.values())
    facts = store.list_facts(user_id=sample.sample_id, project_id="locomo")
    events = store.list_events(user_id=sample.sample_id, project_id="locomo")
    ignored = [
        candidate
        for candidate in candidates
        if candidate.recommended_action in ("ignore", "ask_consent")
        or "quarantine_reason" in candidate.metadata
        or candidate.confidence < 0.3
    ]
    llm_candidates = [
        candidate
        for candidate in candidates
        if candidate.created_by == "llm" or candidate.metadata.get("llm_extractor")
    ]
    llm_rejections = [
        candidate
        for candidate in llm_candidates
        if candidate.metadata.get("llm_rejection_reason")
    ]
    return {
        "sample_id": sample.sample_id,
        "episodes_created": len(sample.episodes),
        "candidates_extracted": len(candidates),
        "accepted_facts": len(facts),
        "accepted_events": len(events),
        "ignored_or_rejected_candidates": len(ignored),
        "llm_candidates": len(llm_candidates),
        "llm_rejections": len(llm_rejections),
        "llm_rejection_reasons": _count_by(
            llm_rejections,
            lambda candidate: str(candidate.metadata.get("llm_rejection_reason") or ""),
        ),
        "candidate_types": _count_by(candidates, lambda candidate: candidate.type),
        "relations": _count_by(facts, lambda fact: fact.relation),
        "retrieval_attempts": 0,
        "abstentions": 0,
        "abstain_reasons": {},
        "evidence_ids": sorted({evidence for fact in facts for evidence in fact.evidence})[:25],
    }


def _update_diagnostics_from_score(diagnostics: Dict[str, Dict[str, Any]], score: Dict[str, Any]) -> None:
    if score["system"] != "cognitive_memory_layer":
        return
    item = diagnostics.get(str(score["sample_id"]))
    if item is None:
        return
    item["retrieval_attempts"] = int(item.get("retrieval_attempts", 0)) + 1
    if score["actual_abstain"]:
        item["abstentions"] = int(item.get("abstentions", 0)) + 1
    reason = str(score.get("abstain_reason") or "")
    if reason:
        reasons = dict(item.get("abstain_reasons", {}))
        reasons[reason] = reasons.get(reason, 0) + 1
        item["abstain_reasons"] = reasons


def _stage_diagnostic(
    sample: LoCoMoSample,
    question: LoCoMoQuestion,
    result: BaselineResult,
    score: Dict[str, Any],
    system: LoCoMoCognitiveSystem,
) -> Dict[str, Any]:
    evidence_ids = set(question.evidence_ids)
    stored_records = _stored_memory_records_for_evidence(
        system.controller.store,
        user_id=sample.sample_id,
        project_id="locomo",
        evidence_ids=evidence_ids,
    )
    selected_records = [
        _selected_memory_record(system.controller.store, selected)
        for selected in result.selected_memories
    ]
    selected_with_evidence = [
        record for record in selected_records if _record_evidence_overlap(record, evidence_ids)
    ]
    stored_evidence_seen = _covered_evidence_ids(stored_records, evidence_ids)
    selected_evidence_seen = _covered_evidence_ids(selected_records, evidence_ids)
    synthesis = _synthesize_diagnostic_answer_result(question.question, selected_records)
    synthesized_answer = synthesis.answer
    synthesized_passed = _diagnostic_answer_passed(synthesized_answer, question)
    evidence_memory_exists = bool(stored_records) if evidence_ids else None
    evidence_texts = _evidence_texts_for_ids(sample, evidence_ids)
    missing_extraction_cues = _missing_extraction_cues(evidence_texts)
    sample_precision = _sample_evidence_memory_precision(sample, system)
    top_k_evidence_hit = bool(selected_with_evidence) if evidence_ids else None
    confidence = float(result.normalized_fields.get("confidence", 0.0))
    abstain_reason = str(score.get("abstain_reason") or "")
    actual_abstain = bool(score.get("actual_abstain"))
    unsafe_answer = bool(
        not question.expected_abstain
        and not actual_abstain
        and evidence_ids
        and not stored_records
    )
    selected_wrong_memory = bool(
        not question.expected_abstain
        and bool(selected_records)
        and evidence_ids
        and not selected_with_evidence
    )
    abstention_with_evidence = bool(
        not question.expected_abstain
        and actual_abstain
        and bool(stored_records)
    )
    extraction_diagnostic = {
        "evidence_ids": list(question.evidence_ids),
        "has_evidence_ids": bool(evidence_ids),
        "evidence_memory_exists": evidence_memory_exists,
        "evidence_memory_count": len(stored_records),
        "evidence_fact_count": sum(1 for record in stored_records if record["memory_type"] == "temporal_fact"),
        "evidence_event_count": sum(1 for record in stored_records if record["memory_type"] == "memory_event"),
        "evidence_reflection_count": sum(1 for record in stored_records if record["memory_type"] == "reflection"),
        "evidence_memory_ids": [record["id"] for record in stored_records[:10]],
        "covered_evidence_ids": stored_evidence_seen,
        "evidence_memory_recall": _coverage_ratio(stored_evidence_seen, evidence_ids),
        "sample_evidence_memory_precision": sample_precision,
        "missing_extraction_cues": missing_extraction_cues,
    }
    retrieval_diagnostic = {
        "retrieval_mode": system.retrieval_mode,
        "selected_memory_count": len(selected_records),
        "selected_evidence_memory_count": len(selected_with_evidence),
        "selected_memory_ids": [record["id"] for record in selected_records],
        "selected_evidence_ids": selected_evidence_seen,
        "top_k_evidence_hit": top_k_evidence_hit,
        "retrieval_evidence_recall": _coverage_ratio(selected_evidence_seen, evidence_ids),
        "selected_wrong_memory": selected_wrong_memory,
        "selected_non_evidence_memory_count": len(selected_records) - len(selected_with_evidence),
        "top_score": float(result.normalized_fields.get("top_score", 0.0)),
        "second_score": float(result.normalized_fields.get("second_score", 0.0)),
        "score_margin": float(result.normalized_fields.get("score_margin", 0.0)),
        "top_relation_match": float(result.normalized_fields.get("top_relation_match", 0.0)),
        "top_temporal_match": float(result.normalized_fields.get("top_temporal_match", 0.0)),
        "top_content_overlap": float(result.normalized_fields.get("top_content_overlap", 0.0)),
    }
    answer_format_mismatch = bool(
        top_k_evidence_hit
        and synthesized_answer != "ABSTAIN"
        and _normalized_answer_text(str(score.get("answer", ""))) != _normalized_answer_text(synthesized_answer)
    )
    answer_diagnostic = {
        "normal_answer": score.get("answer", ""),
        "synthesized_answer": synthesized_answer,
        "synthesis_confidence": synthesis.confidence,
        "synthesis_reason": synthesis.reason,
        "synthesis_question_type": synthesis.question_type,
        "synthesis_matched_relation": synthesis.matched_relation,
        "synthesis_source_memory_id": synthesis.source_memory_id,
        "direct_answer_available": synthesized_answer != "ABSTAIN",
        "synthesized_passed": synthesized_passed,
        "normal_answer_passed": bool(score.get("passed")),
        "answer_format_mismatch": answer_format_mismatch,
        "synthesis_failed_relation": _synthesis_failed_relation(selected_with_evidence, synthesized_answer),
    }
    abstention_diagnostic = {
        "actual_abstain": actual_abstain,
        "expected_abstain": question.expected_abstain,
        "confidence": confidence,
        "safe_to_answer_from_selected_evidence": bool(
            not question.expected_abstain
            and top_k_evidence_hit
            and confidence >= 0.2
        ),
        "correct_abstention": bool(question.expected_abstain and actual_abstain),
        "unsafe_answer": unsafe_answer,
        "wrong_memory_answer": bool(selected_wrong_memory and not actual_abstain),
        "abstention_with_evidence": abstention_with_evidence,
        "decision": _abstention_decision_label(
            expected_abstain=question.expected_abstain,
            actual_abstain=actual_abstain,
            evidence_memory_exists=bool(stored_records),
            top_k_evidence_hit=bool(selected_with_evidence),
            confidence=confidence,
        ),
    }
    failure_labels = _stage_failure_labels(
        question=question,
        evidence_ids=evidence_ids,
        stored_records=stored_records,
        selected_with_evidence=selected_with_evidence,
        actual_abstain=actual_abstain,
        synthesized_answer=synthesized_answer,
        answer_format_mismatch=answer_format_mismatch,
        unsafe_answer=unsafe_answer,
        abstain_reason=abstain_reason,
    )
    return {
        "sample_id": sample.sample_id,
        "question_id": question.question_id,
        "question": question.question,
        "retrieval_mode": system.retrieval_mode,
        "category": question.category,
        "category_name": question.category_name,
        "expected_abstain": question.expected_abstain,
        "passed": bool(score.get("passed")),
        "extraction_diagnostic": extraction_diagnostic,
        "retrieval_diagnostic": retrieval_diagnostic,
        "answer_diagnostic": answer_diagnostic,
        "abstention_diagnostic": abstention_diagnostic,
        "failure_labels": failure_labels,
    }


def _stored_memory_records_for_evidence(
    store: Any,
    user_id: str,
    project_id: str,
    evidence_ids: set,
) -> List[Dict[str, Any]]:
    if not evidence_ids:
        return []
    records: List[Dict[str, Any]] = []
    for fact in store.list_facts(user_id=user_id, project_id=project_id):
        overlap = sorted(evidence_ids & set(fact.evidence))
        if overlap:
            records.append(
                {
                    "id": fact.id,
                    "memory_type": "temporal_fact",
                    "claim": fact.claim_text,
                    "subject": fact.subject,
                    "relation": fact.relation,
                    "object": fact.object,
                    "confidence": fact.confidence,
                    "evidence": list(fact.evidence),
                    "evidence_overlap": overlap,
                }
            )
    for event in store.list_events(user_id=user_id, project_id=project_id):
        overlap = sorted(evidence_ids & set(event.evidence_episode_ids))
        if overlap:
            relation = event.relations[0].relation if event.relations else ""
            object_value = event.relations[0].value if event.relations else ""
            records.append(
                {
                    "id": event.id,
                    "memory_type": "memory_event",
                    "claim": event.claim_text,
                    "subject": event.context.subject_id,
                    "relation": relation,
                    "object": object_value,
                    "confidence": event.confidence,
                    "evidence": list(event.evidence_episode_ids),
                    "evidence_overlap": overlap,
                }
            )
    for reflection in store.list_reflections(user_id=user_id, project_id=project_id):
        overlap = sorted(evidence_ids & set(reflection.supporting_evidence))
        if overlap:
            records.append(
                {
                    "id": reflection.id,
                    "memory_type": "reflection",
                    "claim": reflection.claim,
                    "subject": reflection.subject_id,
                    "relation": reflection.relation_type,
                    "object": "",
                    "confidence": reflection.confidence,
                    "evidence": list(reflection.supporting_evidence),
                    "evidence_overlap": overlap,
                }
            )
    return records


def _sample_evidence_memory_precision(sample: LoCoMoSample, system: LoCoMoCognitiveSystem) -> Optional[float]:
    qa_evidence_ids = {
        evidence_id
        for question in sample.questions
        for evidence_id in question.evidence_ids
    }
    if not qa_evidence_ids:
        return None
    store = system.controller.store
    facts = store.list_facts(user_id=sample.sample_id, project_id="locomo")
    events = store.list_events(user_id=sample.sample_id, project_id="locomo")
    reflections = store.list_reflections(user_id=sample.sample_id, project_id="locomo")
    total = len(facts) + len(events) + len(reflections)
    if total == 0:
        return None
    linked = 0
    linked += sum(1 for fact in facts if qa_evidence_ids & set(fact.evidence))
    linked += sum(1 for event in events if qa_evidence_ids & set(event.evidence_episode_ids))
    linked += sum(1 for reflection in reflections if qa_evidence_ids & set(reflection.supporting_evidence))
    return linked / float(total)


def _selected_memory_record(store: Any, selected: Dict[str, Any]) -> Dict[str, Any]:
    memory_id = str(selected.get("id", ""))
    fact = store.get_fact(memory_id)
    if fact is not None:
        return {
            "id": fact.id,
            "memory_type": "temporal_fact",
            "claim": fact.claim_text,
            "subject": fact.subject,
            "relation": fact.relation,
            "object": fact.object,
            "confidence": fact.confidence,
            "score": float(selected.get("score", 0.0)),
            "evidence": list(fact.evidence),
        }
    event = store.get_event(memory_id)
    if event is not None:
        relation = event.relations[0].relation if event.relations else ""
        object_value = event.relations[0].value if event.relations else ""
        return {
            "id": event.id,
            "memory_type": "memory_event",
            "claim": event.claim_text,
            "subject": event.context.subject_id,
            "relation": relation,
            "object": object_value,
            "confidence": event.confidence,
            "score": float(selected.get("score", 0.0)),
            "evidence": list(event.evidence_episode_ids),
        }
    reflection = store.get_reflection(memory_id)
    if reflection is not None:
        return {
            "id": reflection.id,
            "memory_type": "reflection",
            "claim": reflection.claim,
            "subject": reflection.subject_id,
            "relation": reflection.relation_type,
            "object": "",
            "confidence": reflection.confidence,
            "score": float(selected.get("score", 0.0)),
            "evidence": list(reflection.supporting_evidence),
        }
    return {
        "id": memory_id,
        "memory_type": str(selected.get("memory_type", "unknown")),
        "claim": str(selected.get("claim", "")),
        "subject": "",
        "relation": "",
        "object": _object_from_claim(str(selected.get("claim", ""))),
        "confidence": float(selected.get("confidence", 0.0)),
        "score": float(selected.get("score", 0.0)),
        "evidence": [str(item) for item in selected.get("evidence", [])],
    }


def _record_evidence_overlap(record: Dict[str, Any], evidence_ids: set) -> List[str]:
    if not evidence_ids:
        return []
    return sorted(evidence_ids & set(str(item) for item in record.get("evidence", [])))


def _covered_evidence_ids(records: Sequence[Dict[str, Any]], evidence_ids: set) -> List[str]:
    covered = set()
    for record in records:
        covered.update(_record_evidence_overlap(record, evidence_ids))
    return sorted(covered)


def _coverage_ratio(covered_evidence_ids: Sequence[str], evidence_ids: set) -> Optional[float]:
    if not evidence_ids:
        return None
    return len(set(covered_evidence_ids)) / float(len(evidence_ids))


def _evidence_texts_for_ids(sample: LoCoMoSample, evidence_ids: set) -> List[str]:
    if not evidence_ids:
        return []
    return [episode.content for episode in sample.episodes if episode.id in evidence_ids]


def _missing_extraction_cues(evidence_texts: Sequence[str]) -> Dict[str, bool]:
    text = " ".join(evidence_texts).lower()
    return {
        "event": bool(
            re.search(
                r"\b(?:i|we)\s+(?:ran|signed up|researched|looked into|talked|spoke|gave|delivered|took|went|visited|attended|met up|started|finished|completed)\b",
                text,
            )
            or re.search(r"\b(?:researching|camping|de-?stress|charity race|school event|pottery class)\b", text)
        ),
        "temporal": bool(
            re.search(
                r"\b(?:today|tomorrow|yesterday|tonight|last|next)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|weekend|month|year)?\b",
                text,
            )
            or re.search(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text)
            or re.search(r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?:days?|weeks?|months?|years?)\s+ago\b", text)
        ),
        "location": bool(re.search(r"\b(?:in|at|near|to)\s+[A-Z]?[a-z][A-Za-z0-9_-]{2,}\b", " ".join(evidence_texts))),
        "place_reference": bool(re.search(r"\b(?:there|that place|this place|the city|the town|the park|the museum|the restaurant|the hotel|the cabin|the campsite)\b", text)),
        "event_summary": bool(re.search(r"\b(?:i|we)\s+(?:took|brought|went|visited|attended|ran|completed|finished|signed up|gave|delivered|talked|spoke|met up)\b", text)),
        "participant": bool(re.search(r"\b(?:with|my friend|my sister|my brother|my mother|my mom|my father|my dad|my partner|my husband|my wife|kids|family|fam)\b", text)),
        "identity": bool(re.search(r"\b(?:transgender|transitioning|identity|journey|gay|lesbian|bisexual|queer|nonbinary|non-binary)\b", text)),
    }


def _synthesize_diagnostic_answer(question: str, selected_records: Sequence[Dict[str, Any]]) -> str:
    return _synthesize_diagnostic_answer_result(question, selected_records).answer


def _synthesize_diagnostic_answer_result(
    question: str,
    selected_records: Sequence[Dict[str, Any]],
) -> DiagnosticAnswerSynthesis:
    query_tokens = tokenize(question)
    if not query_tokens:
        return DiagnosticAnswerSynthesis(reason="empty_question")
    question_type = _question_answer_type(question)
    if question_type == "yes_no":
        return DiagnosticAnswerSynthesis(
            reason="yes_no_requires_semantic_entailment",
            question_type=question_type,
        )
    scored: List[Tuple[float, Dict[str, Any], str]] = []
    for record in selected_records:
        claim = str(record.get("claim") or "")
        object_value = str(record.get("object") or "").strip()
        if not object_value:
            object_value = _object_from_claim(claim)
        if not object_value:
            continue
        relation = str(record.get("relation") or "")
        subject = str(record.get("subject") or "")
        template_text = "%s %s %s %s" % (subject, relation, object_value, claim)
        score = lexical_score(question, template_text)
        score += _relation_template_bonus(query_tokens, relation)
        score += _question_type_relation_bonus(question_type, relation)
        if relation and tokenize(relation) & query_tokens:
            score += 0.4
        score += min(0.25, float(record.get("score", 0.0)) * 0.1)
        if score > 0:
            answer_object = _answer_object_for_question(object_value, relation, question, claim=claim)
            if not answer_object:
                continue
            enriched = dict(record)
            enriched["object"] = answer_object
            scored.append((score, enriched, answer_object))
    if not scored:
        fallback = _single_record_overlap_answer(question, selected_records)
        if fallback == "ABSTAIN":
            return DiagnosticAnswerSynthesis(reason="no_selected_record_answer", question_type=question_type)
        return DiagnosticAnswerSynthesis(
            answer=fallback,
            confidence=0.46,
            reason="single_record_overlap",
            question_type=question_type,
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_record, best_answer = scored[0]
    if best_score < 0.1:
        fallback = _single_record_overlap_answer(question, selected_records)
        if fallback == "ABSTAIN":
            return DiagnosticAnswerSynthesis(reason="low_synthesis_score", question_type=question_type)
        return DiagnosticAnswerSynthesis(
            answer=fallback,
            confidence=0.46,
            reason="single_record_overlap",
            question_type=question_type,
        )
    answer = _trim_contextual_answer(str(best_answer), question)
    if not answer:
        return DiagnosticAnswerSynthesis(reason="empty_trimmed_answer", question_type=question_type)
    confidence = _synthesis_confidence(best_score, float(best_record.get("confidence", 0.0)))
    return DiagnosticAnswerSynthesis(
        answer=answer,
        confidence=confidence,
        reason="selected_memory_template",
        question_type=question_type,
        matched_relation=str(best_record.get("relation") or ""),
        source_memory_id=str(best_record.get("id") or ""),
    )


def _question_answer_type(question: str) -> str:
    lowered = question.lower().strip()
    tokens = tokenize(question)
    if "where" in tokens:
        return "location"
    if tokens & {"who", "whom"}:
        return "person"
    if "when" in tokens or re.search(r"\bwhat\s+(?:day|date|time)\b", lowered):
        return "temporal"
    if re.search(r"\bhow\s+many\b", lowered) or "number" in tokens or "count" in tokens:
        return "count"
    if re.match(r"^(?:is|are|was|were|do|does|did|has|have|had|can|could|will|would|should)\b", lowered):
        return "yes_no"
    if tokens & {"relationship", "status", "dating", "married", "partner"}:
        return "relationship"
    return "object"


def _question_type_relation_bonus(question_type: str, relation: str) -> float:
    relation_tokens = tokenize(relation)
    if question_type == "location":
        if _is_location_relation(relation):
            return 0.25
        if relation in ("event", "event_summary", "activity", "plan", "commitment"):
            return 0.35
    if question_type == "person":
        if relation.startswith("relationship_") or relation in {"relationship", "relationship_status", "relationship_to_speaker"}:
            return 0.8
        if relation_tokens & {"person", "speaker", "participant", "friend", "partner"}:
            return 0.4
    if question_type == "temporal":
        if relation in {"time", "date", "day", "temporal_change"} or relation.endswith("_time"):
            return 0.8
        if relation in {"event", "event_summary", "plan", "commitment", "activity"}:
            return 0.35
    if question_type == "count" and (relation_tokens & {"count", "number"}):
        return 0.7
    if question_type == "relationship":
        if relation.startswith("relationship_") or relation in {"relationship", "relationship_status", "relationship_to_speaker"}:
            return 0.85
    if question_type == "object" and relation in {
        "question_answerable_fact",
        "preference",
        "likes",
        "dislikes",
        "habit",
        "routine",
        "person_attribute",
        "attribute",
        "object_location",
        "preference_topic",
        "researched_topic",
        "speech_topic",
        "destress_activity",
    }:
        return 0.45
    return 0.0


def _synthesis_confidence(score: float, memory_confidence: float) -> float:
    return max(0.0, min(1.0, 0.3 + min(score, 1.0) * 0.45 + max(0.0, min(memory_confidence, 1.0)) * 0.25))


def _relation_template_bonus(query_tokens: set, relation: str) -> float:
    relation_tokens = tokenize(relation)
    if not relation_tokens:
        return 0.0
    if _is_location_relation(relation) and query_tokens & {"where", "live", "lives", "stay", "stays", "from", "born", "grow", "grew", "camp", "camping"}:
        return 0.7
    if relation in ("time", "date") or relation.endswith("_time"):
        if query_tokens & {"when", "day", "date", "time", "now", "current"}:
            return 0.7
    if relation in ("plan", "event", "event_summary", "activity") and query_tokens & {"when", "day", "date", "time", "where", "what", "do", "did", "happen", "happened"}:
        return 0.45
    if relation in ("researched_topic", "speech_topic", "destress_activity") and query_tokens & {"what", "topic", "research", "researched", "about", "do", "did"}:
        return 0.65
    if relation in ("relationship_status", "relationship_to_speaker") or relation.startswith("relationship_"):
        if query_tokens & {"relationship", "status", "dating", "married", "single", "partner", "friend", "who", "whom"}:
            return 0.65
    if relation in ("career", "career_goal", "education", "employer", "job", "field"):
        if query_tokens & {"career", "job", "work", "works", "field", "study", "studies", "education", "employer", "company"}:
            return 0.6
    if relation in ("habit", "routine") and query_tokens & {"drink", "drinks", "eat", "eats", "read", "reads", "play", "plays", "practice", "practices"}:
        return 0.6
    if relation in ("likes", "dislikes") and query_tokens & {"like", "likes", "love", "loves", "enjoy", "enjoys", "hate", "hates"}:
        return 0.5
    if query_tokens & {"when", "day", "date", "time"} and relation_tokens:
        return 0.2
    return 0.0


def _answer_object_for_question(object_value: str, relation: str, question: str, claim: str = "") -> str:
    question_type = _question_answer_type(question)
    source_text = object_value or claim
    if question_type == "location":
        if _is_location_relation(relation):
            return object_value
        location = _location_answer_from_text(source_text) or _location_answer_from_text(claim)
        if location:
            return location
    if question_type == "person":
        person = _person_answer_from_text(source_text, relation) or _person_answer_from_text(claim, relation)
        if person:
            return person
        if relation.startswith("relationship_") or relation in ("relationship_status", "relationship_to_speaker"):
            return object_value
    if question_type == "temporal":
        temporal = _temporal_phrase_from_text(source_text) or _temporal_phrase_from_text(claim)
        if temporal:
            return temporal
        if relation in ("time", "date") or relation.endswith("_time"):
            return object_value
    if question_type == "count":
        count = _count_answer_from_text(source_text) or _count_answer_from_text(claim)
        if count:
            return count
    if question_type == "relationship":
        if relation.startswith("relationship_") or relation in ("relationship", "relationship_status", "relationship_to_speaker"):
            return object_value
    if question_type == "object":
        if relation in (
            "activity",
            "attribute",
            "event",
            "event_summary",
            "habit",
            "person_attribute",
            "preference",
            "question_answerable_fact",
            "researched_topic",
            "routine",
            "speech_topic",
            "destress_activity",
            "visited_place",
            "camping_location",
            "likes",
            "dislikes",
        ):
            return object_value
    return object_value


def _is_location_relation(relation: str) -> bool:
    return relation in {
        "location",
        "origin_location",
        "birth_location",
        "stay_location",
        "trip_location",
        "visited_place",
        "camping_location",
    } or relation.endswith("_location")


def _location_answer_from_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip(" ,;:'\""))
    if not cleaned:
        return ""
    for pattern in (
        r"\b(?:to|in|at|near)\s+(?:the\s+|a\s+|an\s+)?(.+?)(?:\s+(?:today|tomorrow|yesterday|tonight|last|next|with|for|because|before|after|during|while)\b|[.!?]|$)",
        r"\b(?:hotel|cabin|campsite|campground|airbnb|apartment|house|room)\s+(?:in|at|near)\s+(?:the\s+|a\s+|an\s+)?(.+?)(?:\s+(?:today|tomorrow|yesterday|tonight|last|next|with|for|because|before|after|during|while)\b|[.!?]|$)",
    ):
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" ,;:'\"")
    return ""


def _person_answer_from_text(text: str, relation: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip(" ,;:'\""))
    if not cleaned:
        return ""
    if relation == "relationship_status":
        match = re.search(r"\b(?:dating|married to|engaged to|with|started dating|broke up with)\s+([A-Z][A-Za-z0-9_-]+)\b", cleaned)
        if match:
            return match.group(1)
    if relation.startswith("relationship_") or relation == "relationship_to_speaker":
        match = re.search(r"\b([A-Z][A-Za-z0-9_-]+)\b", cleaned)
        if match:
            return match.group(1)
    return ""


def _count_answer_from_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip(" ,;:'\""))
    if not cleaned:
        return ""
    match = re.search(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b", cleaned, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _temporal_phrase_from_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip(" ,;:'\""))
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    temporal_patterns = [
        r"\b(?:today|tomorrow|yesterday|tonight)\b",
        r"\b(?:this|next|last)\s+(?:morning|afternoon|evening|week|weekend|month|year)\b",
        r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}\b",
        r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
        r"\b(?:on|at|by|before|after)\s+([A-Za-z0-9: ]{2,30})$",
    ]
    for pattern in temporal_patterns:
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if match:
            phrase = match.group(1) if match.groups() else match.group(0)
            return phrase.strip(" ,;:'\"")
    return cleaned if tokenize(cleaned) & {"morning", "afternoon", "evening", "weekend"} else ""


def _single_record_overlap_answer(question: str, selected_records: Sequence[Dict[str, Any]]) -> str:
    if len(selected_records) != 1:
        return "ABSTAIN"
    record = selected_records[0]
    claim = str(record.get("claim", ""))
    if lexical_score(question, claim) < 0.35:
        return "ABSTAIN"
    object_value = str(record.get("object") or "").strip() or _object_from_claim(claim)
    return _answer_object_for_question(
        object_value,
        str(record.get("relation") or ""),
        question,
        claim=claim,
    ) or "ABSTAIN"


def _synthesis_failed_relation(selected_with_evidence: Sequence[Dict[str, Any]], synthesized_answer: str) -> str:
    if synthesized_answer != "ABSTAIN" or not selected_with_evidence:
        return ""
    relation_counts: Dict[str, int] = {}
    for record in selected_with_evidence:
        relation = str(record.get("relation") or "unknown")
        relation_counts[relation] = relation_counts.get(relation, 0) + 1
    return max(relation_counts.items(), key=lambda item: item[1])[0] if relation_counts else ""


def _object_from_claim(claim: str) -> str:
    parts = claim.strip().split()
    if len(parts) >= 3:
        return " ".join(parts[2:])
    return ""


def _trim_contextual_answer(answer: str, question: str) -> str:
    cleaned = re.sub(r"\s+", " ", answer.strip(" ,;:'\""))
    lowered = cleaned.lower()
    question_lowered = question.lower()
    for marker in (" before ", " after ", " during ", " while "):
        index = lowered.find(marker)
        if index > 0 and lowered[index:].strip() in question_lowered:
            cleaned = cleaned[:index].strip(" ,;:'\"")
            break
    return cleaned


def _diagnostic_answer_passed(answer: str, question: LoCoMoQuestion) -> bool:
    actual_abstain = answer.strip().lower() == "abstain"
    if question.expected_abstain:
        return actual_abstain
    return _has_answer_substring(answer, question.answers) or _best_token_f1(answer, question.answers) >= 0.5


def _normalized_answer_text(answer: str) -> str:
    normalized = answer.lower().replace("_", " ").replace("-", " ")
    return " ".join(re.findall(r"[a-z0-9]+", normalized))


def _abstention_decision_label(
    expected_abstain: bool,
    actual_abstain: bool,
    evidence_memory_exists: bool,
    top_k_evidence_hit: bool,
    confidence: float,
) -> str:
    if expected_abstain:
        return "correct_abstention" if actual_abstain else "answered_expected_abstain"
    if not evidence_memory_exists:
        return "safe_abstention_without_evidence_memory" if actual_abstain else "unsafe_answer_without_evidence_memory"
    if not top_k_evidence_hit:
        return "safe_abstention_after_retrieval_miss" if actual_abstain else "wrong_memory_answer"
    if confidence < 0.2:
        return "low_confidence_abstention" if actual_abstain else "low_confidence_answer"
    return "abstention_with_evidence" if actual_abstain else "answered_with_evidence"


def _stage_failure_labels(
    question: LoCoMoQuestion,
    evidence_ids: set,
    stored_records: Sequence[Dict[str, Any]],
    selected_with_evidence: Sequence[Dict[str, Any]],
    actual_abstain: bool,
    synthesized_answer: str,
    answer_format_mismatch: bool,
    unsafe_answer: bool,
    abstain_reason: str,
) -> List[str]:
    if question.expected_abstain:
        return []
    labels: List[str] = []
    if evidence_ids and not stored_records:
        labels.append("missing_extraction")
    if stored_records and not selected_with_evidence:
        labels.append("retrieval_miss")
    if actual_abstain and (
        "low_confidence" in abstain_reason
        or "low_margin" in abstain_reason
        or "relation_mismatch" in abstain_reason
    ):
        labels.append("low_confidence_abstention")
    if selected_with_evidence and synthesized_answer == "ABSTAIN":
        labels.append("answer_synthesis_failed")
    if unsafe_answer:
        labels.append("unsafe_answer_without_evidence")
    if answer_format_mismatch:
        labels.append("format_mismatch")
    return labels


def _summarize_stage(stage_diagnostics: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    return {system: _stage_system_metrics(items) for system, items in stage_diagnostics.items()}


def _extractor_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    extractor = str(report.get("extractor") or "none")
    summary = dict(report.get("summary", {}).get("cognitive_memory_layer", {}))
    stage = dict(report.get("stage_summary", {}).get("cognitive_memory_layer", {}))
    diagnostics = _diagnostic_totals((report.get("diagnostics") or {}).values())
    llm_candidates = float(diagnostics.get("llm_candidates", 0))
    rejection_reasons = dict(diagnostics.get("llm_rejection_reasons", {}))

    evidence_memory_recall = float(stage.get("evidence_memory_recall", 0.0))
    result: Dict[str, Any] = {
        "extractor": extractor,
        "final_accuracy": float(summary.get("locomo_qa_accuracy", 0.0)),
        "evidence_memory_recall": evidence_memory_recall,
        "rule_based_evidence_memory_recall": evidence_memory_recall if extractor == "rule-based" else 0.0,
        "llm_evidence_memory_recall": evidence_memory_recall if extractor == "llm" else 0.0,
        "extraction_precision_sample": float(stage.get("sample_evidence_memory_precision", 0.0)),
        "hallucination_rejection_rate": _rejection_rate(rejection_reasons, llm_candidates, "hallucinated_entity"),
        "unsupported_memory_rejection_rate": _rejection_rate(rejection_reasons, llm_candidates, "unsupported_memory"),
        "low_confidence_rejection_rate": _rejection_rate(rejection_reasons, llm_candidates, "low_confidence"),
        "ambiguous_reference_rejection_rate": _rejection_rate(rejection_reasons, llm_candidates, "ambiguous_reference"),
        "top_k_evidence_hit_rate": float(stage.get("top_k_evidence_hit_rate", 0.0)),
        "unsafe_answer_rate": float(stage.get("unsafe_answer_rate", 0.0)),
        "answer_synthesis_success_rate": float(stage.get("answer_synthesis_success_rate", 0.0)),
    }
    return result


def _rejection_rate(reasons: Dict[str, int], total: float, name: str) -> float:
    if total <= 0:
        return 0.0
    return float(reasons.get(name, 0)) / total


def _stage_system_metrics(items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    evidence_items = [
        item for item in items
        if item["extraction_diagnostic"]["has_evidence_ids"]
    ]
    non_abstain = [item for item in items if not item["expected_abstain"]]
    expected_abstain = [item for item in items if item["expected_abstain"]]
    direct_answer_items = [
        item for item in non_abstain
        if item["answer_diagnostic"]["direct_answer_available"]
    ]
    evidence_memory_items = [
        item for item in non_abstain
        if item["extraction_diagnostic"]["evidence_memory_exists"]
    ]
    sample_precisions: Dict[str, float] = {}
    for item in items:
        precision = item["extraction_diagnostic"].get("sample_evidence_memory_precision")
        if precision is not None:
            sample_precisions[str(item["sample_id"])] = float(precision)
    failed_relations: Dict[str, int] = {}
    failed_question_types: Dict[str, int] = {}
    for item in non_abstain:
        relation = str(item["answer_diagnostic"].get("synthesis_failed_relation") or "")
        if relation:
            failed_relations[relation] = failed_relations.get(relation, 0) + 1
        if item["answer_diagnostic"].get("direct_answer_available") is False:
            question_type = str(item["answer_diagnostic"].get("synthesis_question_type") or "unknown")
            failed_question_types[question_type] = failed_question_types.get(question_type, 0) + 1
    metrics = {
        "total": float(len(items)),
        "locomo_accuracy": _accuracy(items),
        "evidence_memory_recall": _mean(
            float(item["extraction_diagnostic"]["evidence_memory_recall"])
            for item in evidence_items
            if item["extraction_diagnostic"]["evidence_memory_recall"] is not None
        ),
        "sample_evidence_memory_precision": _mean(sample_precisions.values()),
        "retrieval_evidence_recall": _mean(
            float(item["retrieval_diagnostic"]["retrieval_evidence_recall"])
            for item in evidence_items
            if item["retrieval_diagnostic"]["retrieval_evidence_recall"] is not None
        ),
        "top_k_evidence_hit_rate": _mean(
            1.0 if item["retrieval_diagnostic"]["top_k_evidence_hit"] else 0.0
            for item in evidence_items
        ),
        "answer_synthesis_success_rate": _mean(
            1.0 if item["answer_diagnostic"]["synthesized_passed"] else 0.0
            for item in non_abstain
        ),
        "answer_format_mismatch_rate": _mean(
            1.0 if item["answer_diagnostic"]["answer_format_mismatch"] else 0.0
            for item in direct_answer_items
        ),
        "correct_abstention_rate": _mean(
            1.0 if item["abstention_diagnostic"]["correct_abstention"] else 0.0
            for item in expected_abstain
        ),
        "unsafe_answer_rate": _mean(
            1.0 if item["abstention_diagnostic"]["unsafe_answer"] else 0.0
            for item in items
        ),
        "abstention_with_evidence_rate": _mean(
            1.0 if item["abstention_diagnostic"]["abstention_with_evidence"] else 0.0
            for item in evidence_memory_items
        ),
        "selected_wrong_memory_rate": _mean(
            1.0 if item["retrieval_diagnostic"]["selected_wrong_memory"] else 0.0
            for item in non_abstain
        ),
        "answer_synthesis_failed_by_relation": dict(sorted(failed_relations.items())),
        "answer_synthesis_failed_by_question_type": dict(sorted(failed_question_types.items())),
    }
    for label in (
        "missing_extraction",
        "retrieval_miss",
        "low_confidence_abstention",
        "answer_synthesis_failed",
        "unsafe_answer_without_evidence",
        "format_mismatch",
    ):
        metrics["%s_rate" % label] = _mean(
            1.0 if label in item.get("failure_labels", []) else 0.0
            for item in non_abstain
        )
    for cue in ("event", "temporal", "location", "place_reference", "event_summary", "participant", "identity"):
        metrics["missing_extraction_with_%s_cue_rate" % cue] = _mean(
            1.0
            if (
                "missing_extraction" in item.get("failure_labels", [])
                and item["extraction_diagnostic"]["missing_extraction_cues"].get(cue)
            )
            else 0.0
            for item in non_abstain
        )
    return metrics


def _count_by(items: Sequence[Any], key_fn: Callable[[Any], str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        key = key_fn(item) or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _diagnostic_totals(items: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    totals: Dict[str, Any] = {
        "episodes_created": 0,
        "candidates_extracted": 0,
        "accepted_facts": 0,
        "accepted_events": 0,
        "ignored_or_rejected_candidates": 0,
        "llm_candidates": 0,
        "llm_rejections": 0,
        "retrieval_attempts": 0,
        "abstentions": 0,
        "candidate_types": {},
        "relations": {},
        "llm_rejection_reasons": {},
    }
    for item in items:
        for key in (
            "episodes_created",
            "candidates_extracted",
            "accepted_facts",
            "accepted_events",
            "ignored_or_rejected_candidates",
            "llm_candidates",
            "llm_rejections",
            "retrieval_attempts",
            "abstentions",
        ):
            totals[key] += int(item.get(key, 0))
        for key in ("candidate_types", "relations", "llm_rejection_reasons"):
            for name, count in dict(item.get(key, {})).items():
                totals[key][name] = totals[key].get(name, 0) + int(count)
    return totals


def _summarize(scores: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    by_system: Dict[str, List[Dict[str, Any]]] = {}
    for score in scores:
        by_system.setdefault(str(score["system"]), []).append(score)
    return {system: _system_metrics(items) for system, items in by_system.items()}


def _system_metrics(scores: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    temporal = [score for score in scores if score["category_name"] == "temporal"]
    multi_session = [score for score in scores if score["multi_session"] or score["category_name"] == "multi_hop"]
    abstention = [score for score in scores if score["expected_abstain"]]
    evidence = [score for score in scores if score["evidence_recall"] is not None]
    answered = [score for score in scores if not score["actual_abstain"]]
    latencies = [float(score["latency_ms"]) for score in scores]
    return {
        "total": float(len(scores)),
        "passed": float(sum(1 for score in scores if score["passed"])),
        "locomo_qa_accuracy": _accuracy(scores),
        "mean_token_f1": _mean(float(score["token_f1"]) for score in scores),
        "evidence_recall": _mean(float(score["evidence_recall"]) for score in evidence),
        "temporal_question_accuracy": _accuracy(temporal),
        "multi_session_question_accuracy": _accuracy(multi_session),
        "abstention_accuracy": _accuracy([score for score in abstention if score["actual_abstain"] == score["expected_abstain"]], len(abstention)),
        "provenance_coverage": _mean(1.0 if score["provenance"] else 0.0 for score in answered),
        "p50_latency_ms": _percentile(latencies, 50),
        "p95_latency_ms": _percentile(latencies, 95),
    }


def _answers_from_raw(raw_question: Dict[str, Any]) -> List[str]:
    value = raw_question.get("answer", raw_question.get("answers", ""))
    if isinstance(value, list):
        answers = [str(item) for item in value]
    else:
        answers = [str(value)]
    return [answer for answer in answers if answer is not None]


def _category_name(category: str) -> str:
    if not category:
        return "unknown"
    normalized = str(category).strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in CATEGORY_NAMES:
        return CATEGORY_NAMES[normalized]
    if normalized in set(CATEGORY_NAMES.values()):
        return normalized
    return normalized


def _session_keys(conversation: Dict[str, Any]) -> List[str]:
    keys = [
        key
        for key, value in conversation.items()
        if key.startswith("session_") and not key.endswith("_date_time") and isinstance(value, list)
    ]
    return sorted(keys, key=_session_number)


def _session_number(session_key: str) -> int:
    match = re.search(r"session_(\d+)", session_key)
    return int(match.group(1)) if match else 0


def _parse_locomo_datetime(value: Any, session_num: int) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        normalized = re.sub(r"\s+", " ", raw).replace(" am ", " AM ").replace(" pm ", " PM ")
        for pattern in (
            "%I:%M %p on %d %B, %Y",
            "%I:%M %p on %B %d, %Y",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                parsed = datetime.strptime(normalized, pattern)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc) + timedelta(days=max(session_num - 1, 0))


def _best_token_f1(answer: str, expected_answers: Sequence[str]) -> float:
    return max((_token_f1(answer, expected) for expected in expected_answers), default=0.0)


def _token_f1(answer: str, expected: str) -> float:
    predicted_tokens = tokenize(answer)
    expected_tokens = tokenize(expected)
    if not predicted_tokens and not expected_tokens:
        return 1.0
    if not predicted_tokens or not expected_tokens:
        return 0.0
    overlap = len(predicted_tokens & expected_tokens)
    if overlap == 0:
        return 0.0
    precision = overlap / float(len(predicted_tokens))
    recall = overlap / float(len(expected_tokens))
    return 2.0 * precision * recall / (precision + recall)


def _has_answer_substring(answer: str, expected_answers: Sequence[str]) -> bool:
    normalized_answer = answer.lower()
    return any(expected.strip() and expected.strip().lower() in normalized_answer for expected in expected_answers)


def _evidence_recall(provenance: Sequence[str], evidence_ids: Sequence[str]) -> Optional[float]:
    if not evidence_ids:
        return None
    evidence = set(evidence_ids)
    selected = set(provenance)
    return len(evidence & selected) / float(len(evidence))


def _evidence_spans_sessions(evidence_ids: Sequence[str]) -> bool:
    sessions = set()
    for evidence_id in evidence_ids:
        match = re.match(r"D(\d+)[:_-]", evidence_id)
        if match:
            sessions.add(match.group(1))
    return len(sessions) > 1


def _accuracy(scores: Sequence[Dict[str, Any]], total_override: Optional[int] = None) -> float:
    total = total_override if total_override is not None else len(scores)
    if not total:
        return 0.0
    return sum(1 for score in scores if score.get("passed")) / float(total)


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        return 0.0
    return sum(items) / float(len(items))


def _percentile(values: Sequence[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (percentile / 100.0) * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction
