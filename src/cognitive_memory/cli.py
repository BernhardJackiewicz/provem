from __future__ import annotations

import argparse
import json
import sys

from .adapters.base import AdapterConfigurationError, OptionalDependencyNotInstalled
from .benchmark import BenchmarkRunner, dumps_report
from .controller import MemoryController
from .models import Episode, RetrievalRequest
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

    for content in [
        "FACT user|work_mode|remote",
        "FACT user|work_mode|hybrid",
        "FACT user|domain|AI memory systems",
        "DELETE remote",
    ]:
        controller.ingest_episode(Episode(content))

    SleepCycle(controller.store).consolidate()
    result = retrieval.retrieve(RetrievalRequest(query="current work mode and domain", top_k=5))
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cml", description="Cognitive Memory Layer prototype")
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
    return parser


def main(argv: list = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
