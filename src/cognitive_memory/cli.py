from __future__ import annotations

import argparse
import json
import sys

from .adapters.base import AdapterConfigurationError, OptionalDependencyNotInstalled
from .benchmark import BenchmarkRunner, dumps_report
from .controller import MemoryController
from .models import Episode, RetrievalRequest
from .persistence import load_snapshot, retrieval_trace_record, save_snapshot
from .reflection import SleepCycle
from .retrieval import RetrievalPlanner


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


def _load_demo_memory(controller: MemoryController) -> None:
    for content in [
        "FACT user|work_mode|remote",
        "FACT user|work_mode|hybrid",
        "FACT user|domain|AI memory systems",
        "DELETE remote",
    ]:
        controller.ingest_episode(Episode(content))


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
    return parser


def main(argv: list = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
