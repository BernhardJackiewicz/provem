from __future__ import annotations

import argparse
import json
import sys
import tempfile

from .adapters.base import AdapterConfigurationError, OptionalDependencyNotInstalled
from .benchmark import BenchmarkRunner, dumps_report
from .controller import MemoryController
from .models import Episode, RetrievalRequest
from .persistence import load_snapshot, retrieval_trace_record, save_snapshot
from .reflection import SleepCycle
from .retrieval import RetrievalPlanner
from .transcript_eval import dumps_transcript_report, evaluate_transcripts, load_transcripts


def run_benchmark(args: argparse.Namespace) -> int:
    try:
        report = BenchmarkRunner(
            suite=args.suite,
            include_mem0=args.include_mem0,
            skip_optional=not args.strict_optional,
        ).run()
    except (OptionalDependencyNotInstalled, AdapterConfigurationError) as exc:
        print("Optional benchmark setup failed: %s" % exc, file=sys.stderr)
        return 2
    print(dumps_report(report, as_json=args.json))
    return 0


def run_demo(args: argparse.Namespace) -> int:
    controller = MemoryController()
    retrieval = RetrievalPlanner(controller.store, controller.policy)

    _load_demo_memory(controller)

    SleepCycle(controller.store).consolidate()
    result = retrieval.retrieve(RetrievalRequest(query="current work mode and domain", top_k=5))
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0


def run_export_memory(args: argparse.Namespace) -> int:
    controller = MemoryController()
    traces = []
    if args.demo:
        _load_demo_memory(controller)
        result = RetrievalPlanner(controller.store, controller.policy).retrieve(
            RetrievalRequest(query="current work mode and domain", top_k=5)
        )
        traces.append(retrieval_trace_record(result, query="current work mode and domain"))

    save_snapshot(args.path, controller.store, controller.policy, retrieval_traces=traces)
    print(
        json.dumps(
            {
                "path": args.path,
                "episodes": len(controller.store.episodes),
                "facts": len(controller.store.facts),
                "events": len(controller.store.events),
                "reflections": len(controller.store.reflections),
                "retrieval_traces": len(traces),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def run_import_memory(args: argparse.Namespace) -> int:
    snapshot = load_snapshot(args.path)
    result = RetrievalPlanner(snapshot.store, snapshot.policy).retrieve(
        RetrievalRequest(
            query=args.query,
            user_id=args.user_id,
            project_id=args.project_id,
            task_type=args.task_type,
            top_k=args.top_k,
        )
    )
    snapshot.store.add_retrieval_trace(retrieval_trace_record(result, query=args.query))
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0


def run_transcript_eval(args: argparse.Namespace) -> int:
    report = evaluate_transcripts(args.input, persist_path=args.persist_path or "")
    redaction_terms = []
    for transcript in load_transcripts(args.input):
        redaction_terms.extend(transcript.redaction_terms)
        if transcript.caller_identity.stated_name:
            redaction_terms.append(transcript.caller_identity.stated_name)
        redaction_terms.extend(participant.name for participant in transcript.participants if participant.name)
    print(
        dumps_transcript_report(
            report,
            as_json=args.json,
            redaction_terms=redaction_terms,
            redact_salaries=args.redact_salaries,
            redact_companies=args.redact_companies,
        )
    )
    return 0


def run_quality_gate(args: argparse.Namespace) -> int:
    report = {
        "passed": True,
        "checks": {},
        "note": "unittest is not run inside quality-gate; run python3 -m unittest separately",
    }

    benchmark_report = BenchmarkRunner(suite="all").run()
    cml = benchmark_report["summary"]["cognitive_memory_layer"]
    benchmark_passed = (
        benchmark_report["scenario_count"] >= 221
        and cml["passed"] >= 216
        and cml["deleted_memory_leakage"] == 0.0
        and cml["do_not_use_leakage"] == 0.0
        and cml["unsafe_recall_rate"] == 0.0
        and cml["prompt_injection_memory_success_rate"] == 0.0
    )
    report["checks"]["benchmark_all"] = {
        "passed": benchmark_passed,
        "cognitive_memory_layer_passed": cml["passed"],
        "scenario_count": benchmark_report["scenario_count"],
        "accuracy": cml["accuracy"],
    }

    transcript_report = evaluate_transcripts(args.transcript_input)
    transcript_summary = transcript_report["summary"]
    transcript_passed = (
        transcript_report["transcript_count"] >= 11
        and transcript_report["labeled_count"] >= 10
        and transcript_summary.get("do_not_use_leakage", 1.0) == 0.0
        and transcript_summary.get("sensitive_storage_violation_rate", 1.0) == 0.0
        and transcript_summary.get("extraction_recall", 0.0) >= 0.9
        and transcript_summary.get("current_truth_accuracy", 0.0) >= 0.9
    )
    report["checks"]["transcript_eval"] = {
        "passed": transcript_passed,
        "transcript_count": transcript_report["transcript_count"],
        "labeled_count": transcript_report["labeled_count"],
        "extraction_recall": transcript_summary.get("extraction_recall", 0.0),
        "current_truth_accuracy": transcript_summary.get("current_truth_accuracy", 0.0),
        "known_failures": [
            result["transcript_id"]
            for result in transcript_report["results"]
            if result["failures"] and result["failures"] != ["unlabeled_diagnostic_only"]
        ],
    }

    persistence_passed = _quality_gate_persistence_smoke()
    report["checks"]["persistence_smoke"] = {"passed": persistence_passed}

    report["passed"] = all(check["passed"] for check in report["checks"].values())
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Quality gate: %s" % ("PASS" if report["passed"] else "FAIL"))
        for name, check in report["checks"].items():
            print("- %s: %s" % (name, "PASS" if check["passed"] else "FAIL"))
        print(report["note"])
    return 0 if report["passed"] else 1


def _load_demo_memory(controller: MemoryController) -> None:
    for content in [
        "FACT user|work_mode|remote",
        "FACT user|work_mode|hybrid",
        "FACT user|domain|AI memory systems",
        "DELETE remote",
    ]:
        controller.ingest_episode(Episode(content))


def _quality_gate_persistence_smoke() -> bool:
    controller = MemoryController()
    _load_demo_memory(controller)
    request = RetrievalRequest(query="current work mode", task_type="temporal")
    before = RetrievalPlanner(controller.store, controller.policy).retrieve(request)
    with tempfile.TemporaryDirectory() as directory:
        path = "%s/quality-gate.memory.jsonl" % directory
        save_snapshot(path, controller.store, controller.policy, retrieval_traces=[retrieval_trace_record(before, query=request.query)])
        snapshot = load_snapshot(path)
    after = RetrievalPlanner(snapshot.store, snapshot.policy).retrieve(request)
    return (
        before.answer_text() == after.answer_text()
        and "hybrid" in after.answer_text()
        and "remote" not in after.answer_text()
        and after.abstain_recommended is False
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cml", description="Engram hippocampal memory layer prototype")
    subparsers = parser.add_subparsers(dest="command", required=True)

    benchmark = subparsers.add_parser("benchmark", help="Run synthetic benchmark baselines")
    benchmark.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    benchmark.add_argument(
        "--suite",
        choices=("structured", "noisy", "recruiting", "adversarial", "all"),
        default="structured",
        help="Benchmark suite to run (default: structured)",
    )
    benchmark.add_argument("--include-mem0", action="store_true", help="Include optional Mem0 baseline when installed/configured")
    benchmark.add_argument("--strict-optional", action="store_true", help="Fail instead of skipping unavailable optional baselines")
    benchmark.set_defaults(func=run_benchmark)

    demo = subparsers.add_parser("demo", help="Run a small governed-memory demo")
    demo.set_defaults(func=run_demo)

    export_memory = subparsers.add_parser("export-memory", help="Export a local JSONL memory snapshot")
    export_memory.add_argument("--path", required=True, help="Snapshot path to write")
    export_memory.add_argument("--demo", action="store_true", help="Export the built-in demo memory instead of an empty snapshot")
    export_memory.set_defaults(func=run_export_memory)

    import_memory = subparsers.add_parser("import-memory", help="Load a JSONL snapshot and run a retrieval query")
    import_memory.add_argument("--path", required=True, help="Snapshot path to read")
    import_memory.add_argument("--query", required=True, help="Retrieval query to run")
    import_memory.add_argument("--user-id", default="user", help="User scope for retrieval")
    import_memory.add_argument("--project-id", default="default", help="Project scope for retrieval")
    import_memory.add_argument("--task-type", default="general", help="Retrieval task type")
    import_memory.add_argument("--top-k", type=int, default=5, help="Maximum selected memories")
    import_memory.set_defaults(func=run_import_memory)

    transcript_eval = subparsers.add_parser("transcript-eval", help="Evaluate local transcript fixtures or anonymized transcripts")
    transcript_eval.add_argument("--input", required=True, help="Transcript JSON/JSONL file or directory")
    transcript_eval.add_argument("--persist-path", default="", help="Optional JSONL memory snapshot path for a single transcript run")
    transcript_eval.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    transcript_eval.add_argument("--redact-salaries", action="store_true", help="Mask salary-like values in reports")
    transcript_eval.add_argument("--redact-companies", action="store_true", help="Mask simple company/client identifiers in reports")
    transcript_eval.set_defaults(func=run_transcript_eval)

    quality_gate = subparsers.add_parser("quality-gate", help="Run local benchmark, transcript, and persistence reliability checks")
    quality_gate.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    quality_gate.add_argument("--transcript-input", default="tests/fixtures/transcripts", help="Transcript fixture directory")
    quality_gate.set_defaults(func=run_quality_gate)
    return parser


def main(argv: list = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
