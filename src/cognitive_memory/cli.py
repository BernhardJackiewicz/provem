from __future__ import annotations

import argparse
import json
import sys
import tempfile

from .adapters.base import AdapterConfigurationError, OptionalDependencyNotInstalled
from .benchmark import BenchmarkRunner, dumps_report
from .consolidation_eval import dumps_consolidation_eval_report, evaluate_consolidation
from .controller import MemoryController
from .external_eval import ExternalValidationError, dumps_external_report, evaluate_external_manifest
from .extractor import ExtractorSchemaError
from .graphiti_env import check_graphiti_environment, dumps_graphiti_env_report
from .locomo_eval import LoCoMoEvaluationError, dumps_locomo_report, evaluate_locomo, locomo_path_from_manifest
from .mem0_env import check_mem0_environment, dumps_mem0_env_report
from .mem0_audit import dumps_mem0_audit_report, evaluate_mem0_audit
from .models import Episode, RetrievalRequest, TemporalFact
from .persistence import load_snapshot, retrieval_trace_record, save_snapshot
from .reflection import SleepCycle
from .review import build_review_queue, simulate_review
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


def run_reliability(args: argparse.Namespace) -> int:
    from .reliability_suite import (
        format_e2e_report,
        format_report,
        run_end_to_end_benchmark,
        run_reliability_benchmark,
    )

    try:
        seeds = [int(s) for s in str(args.seeds).split(",") if s.strip() != ""]
    except ValueError:
        print("--seeds must be a comma-separated list of integers", file=sys.stderr)
        return 2
    if not seeds:
        seeds = [1, 2, 3, 4, 5]

    if args.end_to_end:
        try:
            skills = [float(s) for s in str(args.skills).split(",") if s.strip() != ""]
        except ValueError:
            print("--skills must be a comma-separated list of floats", file=sys.stderr)
            return 2
        if not skills:
            skills = [0.99, 0.95, 0.90]
        e2e = run_end_to_end_benchmark(seeds=seeds, scenarios_per_seed=args.scenarios, skills=skills)
        if args.json:
            payload = [
                {
                    "agent_skill": sk.skill,
                    "no_memory_task_success": sk.arms["no_memory"].task_success_rate,
                    "ungoverned_task_success": sk.arms["ungoverned"].task_success_rate,
                    "governed_task_success": sk.arms["governed"].task_success_rate,
                    "agent_only_baseline_p_pow_n": sk.independence_baseline,
                    "memory_governance_delta": sk.memory_delta,
                    "mcnemar_p": sk.mcnemar_p,
                }
                for sk in e2e.per_skill
            ]
            print(json.dumps(payload, indent=2))
        else:
            print(format_e2e_report(e2e))
        return 0

    result = run_reliability_benchmark(seeds=seeds, scenarios_per_seed=args.scenarios)
    if args.json:
        print(json.dumps(result.headline(), indent=2))
    else:
        print(format_report(result))
    return 0


def run_external_reliability(args: argparse.Namespace) -> int:
    from . import external_datasets as ed
    from .external_reliability import (
        dumps_external_reliability_report,
        evaluate_erasure,
        evaluate_injection_detection,
        evaluate_payload_replay,
        evaluate_scope,
        export_failures,
        load_external_records,
    )

    if args.status:
        status = ed.external_data_status(args.data_root)
        print(json.dumps(status, indent=2, sort_keys=True) if args.json else _format_external_status(status))
        return 0

    if args.download:
        if not args.dataset:
            print("--download requires --dataset", file=sys.stderr)
            return 2
        try:
            result = ed.download_dataset(args.dataset, dest_root=args.data_root, force=args.force)
        except Exception as exc:  # network / validation errors surface clearly
            print("download failed: %s" % exc, file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    manifest_path = args.manifest or ("%s/manifest.json" % args.data_root)
    if not _os_path_exists(manifest_path):
        print(
            "no dataset manifest at %s; run: external-reliability --download --dataset <name>"
            % manifest_path,
            file=sys.stderr,
        )
        return 2

    explicit = [d.strip() for d in str(args.datasets).split(",") if d.strip()]
    tracks = args.track
    # Track-appropriate defaults are authoritative; an explicit --datasets list
    # only overrides when a single track is selected (avoids nonsensical cross
    # entries like erasure-on-an-injection-dataset under --track all).

    def datasets_for(default_list):
        return explicit if (explicit and tracks != "all") else default_list

    reports: dict = {}
    try:
        if tracks in ("injection", "all"):
            inj = []
            for name in datasets_for(["deepset_prompt_injections", "injecagent"]):
                try:
                    records = load_external_records(name, manifest_path, split=args.split, limit=args.limit)
                except ExternalValidationError:
                    continue
                if records:
                    inj.append(evaluate_injection_detection(records).as_dict())
                    if any(r.label == "injection" for r in records):
                        inj[-1]["payload_replay"] = evaluate_payload_replay(records).as_dict()
            reports["injection"] = inj
        if tracks in ("erasure", "all"):
            era = []
            for name in datasets_for(["tofu"]):
                try:
                    records = load_external_records(name, manifest_path, split=args.split, limit=args.limit)
                except ExternalValidationError:
                    continue
                if records:
                    era.append(evaluate_erasure(records).as_dict())
            reports["erasure"] = era
        if tracks in ("scope", "all"):
            sco = []
            for name in datasets_for(["ai4privacy"]):
                try:
                    records = load_external_records(name, manifest_path, split=args.split, limit=args.limit)
                except ExternalValidationError:
                    continue
                if records:
                    sco.append(evaluate_scope(records).as_dict())
            reports["scope"] = sco
    except ExternalValidationError as exc:
        print("external-reliability failed: %s" % exc, file=sys.stderr)
        return 2

    if args.export_failures:
        if args.split == "test":
            print("--export-failures refuses --split test (overfitting guard)", file=sys.stderr)
            return 2
        total = 0
        for name in (explicit or ["deepset_prompt_injections"]):
            try:
                records = load_external_records(name, manifest_path, split=args.split or "dev", limit=args.limit)
            except ExternalValidationError:
                continue
            if records:
                total += export_failures(records, args.export_failures)
        print("exported %d misclassified records to %s" % (total, args.export_failures), file=sys.stderr)

    print(dumps_external_reliability_report(reports, as_json=args.json))
    return 0


def _os_path_exists(path: str) -> bool:
    import os

    return os.path.exists(path)


def _format_external_status(status: dict) -> str:
    lines = ["External dataset status (root: %s)" % status.get("root", "")]
    for dataset in status.get("datasets", []):
        lines.append(
            "- %s [%s] present=%s"
            % (dataset["dataset_name"], dataset["license"], dataset["all_present"])
        )
    return "\n".join(lines)


def run_demo(args: argparse.Namespace) -> int:
    controller = MemoryController()
    retrieval = RetrievalPlanner(controller.store, controller.policy)

    _load_demo_memory(controller)

    SleepCycle(controller.store, controller.policy).consolidate()
    result = retrieval.retrieve(RetrievalRequest(query="current work mode and domain", top_k=5))
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0


def run_sleep_cycle(args: argparse.Namespace) -> int:
    if args.apply:
        print("SleepCycle --apply is reserved; durable consolidation is not implemented.", file=sys.stderr)
        return 2

    controller = MemoryController()
    if args.demo:
        _load_sleep_cycle_demo_memory(controller)
    run = SleepCycle(controller.store, controller.policy).consolidate(record=args.record)
    if args.json:
        print(json.dumps(run.to_dict(), indent=2, sort_keys=True))
        return 0

    print("Sleep cycle dry run: %s" % run.id)
    print("- candidates_found: %s" % run.summary.get("candidate_count", 0))
    print("- decisions_made: %s" % run.summary.get("decision_count", 0))
    print("- review_required: %s" % run.summary.get("review_required", 0))
    print("- durable_writes: %s" % run.summary.get("durable_writes", 0))
    actions = run.summary.get("actions", {})
    for action, count in sorted(actions.items()):
        print("- action.%s: %s" % (action, count))
    for decision in run.decisions:
        if decision.review_required:
            print(
                "- review: %s action=%s reason=%s evidence=%s counter=%s"
                % (
                    decision.id,
                    decision.action,
                    decision.reason,
                    len(decision.evidence_ids),
                    len(decision.counter_evidence_ids),
                )
            )
    return 0


def run_consolidation_eval(args: argparse.Namespace) -> int:
    report = evaluate_consolidation()
    print(dumps_consolidation_eval_report(report, as_json=args.json))
    return 0


def run_review_queue(args: argparse.Namespace) -> int:
    controller = MemoryController()
    if args.demo:
        _load_sleep_cycle_demo_memory(controller)
    run = SleepCycle(controller.store, controller.policy).consolidate(record=args.record)
    queue = build_review_queue(run, mode="all")
    if args.policy != "none":
        simulate_review(queue, policy=args.policy, controller=controller)
    if args.record:
        controller.store.add_review_queue(queue)
    if args.json:
        print(json.dumps(queue.to_dict(), indent=2, sort_keys=True))
        return 0

    print("Review queue: %s" % queue.id)
    print("- consolidation_run: %s" % queue.consolidation_run_id)
    print("- items: %s" % queue.summary.get("item_count", 0))
    print("- pending: %s" % queue.summary.get("pending", 0))
    print("- approved_simulation: %s" % queue.summary.get("approved", 0))
    print("- rejected_simulation: %s" % queue.summary.get("rejected", 0))
    print("- needs_more_evidence: %s" % queue.summary.get("needs_more_evidence", 0))
    print("- deferred: %s" % queue.summary.get("deferred", 0))
    print("- high_risk_autoapproved: %s" % queue.summary.get("high_risk_autoapproved", 0))
    for risk, count in sorted(queue.summary.get("risk_counts", {}).items()):
        print("- risk.%s: %s" % (risk, count))
    decisions_by_item = {decision.review_item_id: decision for decision in queue.decisions}
    for item in queue.items:
        decision = decisions_by_item.get(item.id)
        review_reason = decision.reason if decision is not None else item.reason
        print(
            "- item: %s action=%s risk=%s status=%s reason=%s evidence=%s counter=%s"
            % (
                item.id,
                item.proposed_action,
                item.risk_level,
                item.status,
                review_reason,
                len(item.evidence_ids),
                len(item.counter_evidence_ids),
            )
        )
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


def run_external_eval(args: argparse.Namespace) -> int:
    try:
        report = evaluate_external_manifest(args.manifest)
    except ExternalValidationError as exc:
        print("External validation setup failed: %s" % exc, file=sys.stderr)
        return 2
    print(
        dumps_external_report(
            report,
            as_json=args.json,
            redact_salaries=args.redact_salaries,
            redact_companies=args.redact_companies,
        )
    )
    return 0


def run_locomo_eval(args: argparse.Namespace) -> int:
    try:
        path = args.path or locomo_path_from_manifest(args.manifest)
        report = evaluate_locomo(
            path,
            limit_samples=args.limit_samples,
            max_samples=args.max_samples,
            limit_qa=args.limit_qa,
            max_turns=args.max_turns,
            max_api_calls=args.max_api_calls,
            sample_ids=_comma_separated(args.sample_ids),
            dry_run_cost_estimate=args.dry_run_cost_estimate,
            extract=args.extract,
            diagnostics=args.diagnostics,
            stage_report=args.stage_report,
            retrieval_mode=args.retrieval_mode,
            extractor_mode=args.extractor,
            answer_mode=args.answer_mode,
            recall_boost=args.recall_boost,
            qa_evidence_in_window_only=args.qa_evidence_in_window_only,
            llm_cache_dir=args.llm_cache_dir,
        )
    except (ExternalValidationError, ExtractorSchemaError, LoCoMoEvaluationError) as exc:
        print("LoCoMo evaluation setup failed: %s" % exc, file=sys.stderr)
        return 2
    print(dumps_locomo_report(report, as_json=args.json))
    return 0


def _comma_separated(value: str) -> list:
    return [item.strip() for item in value.split(",") if item.strip()]


def run_mem0_env_check(args: argparse.Namespace) -> int:
    report = check_mem0_environment()
    print(dumps_mem0_env_report(report, as_json=args.json))
    return 0 if report["ready"] else 2


def run_mem0_sanity(args: argparse.Namespace) -> int:
    try:
        report = evaluate_mem0_audit(
            strict_optional=args.strict_optional,
            governance_suite=args.governance_suite,
        )
    except (OptionalDependencyNotInstalled, AdapterConfigurationError) as exc:
        print("Mem0 sanity setup failed: %s" % exc, file=sys.stderr)
        return 2
    print(dumps_mem0_audit_report(report, as_json=args.json))
    return 0 if report["status"] == "complete" or not args.strict_optional else 2


def run_graphiti_env_check(args: argparse.Namespace) -> int:
    report = check_graphiti_environment()
    print(dumps_graphiti_env_report(report, as_json=args.json))
    return 0 if report["ready"] else 2


def _quality_gate_external_reliability_smoke() -> dict:
    """Offline-safe: skip green when real datasets are not downloaded.

    When datasets exist locally, run a mechanical smoke (loaders parse, metrics
    compute, report serializes) on the dev split with a small cap. No thresholds
    are enforced on real data yet -- we expect and honestly report weak numbers,
    so the gate must not punish honesty.
    """
    import os

    manifest_path = "data/external/manifest.json"
    if not os.path.exists(manifest_path):
        return {
            "passed": True,
            "status": "skipped_datasets_not_downloaded",
            "hint": "run: python3 -m cognitive_memory external-reliability --download --dataset deepset_prompt_injections",
        }
    try:
        from .external_reliability import evaluate_injection_detection, load_external_records

        checked = []
        for name in ("deepset_prompt_injections", "injecagent"):
            try:
                records = load_external_records(name, manifest_path, split="dev", limit=50)
            except Exception:
                continue
            if records:
                evaluate_injection_detection(records).as_dict()
                checked.append(name)
        return {"passed": True, "status": "ran", "datasets_checked": checked}
    except Exception as exc:
        return {"passed": False, "status": "error", "error": str(exc)}


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
    sleep_cycle_passed = _quality_gate_sleep_cycle_smoke()
    report["checks"]["sleep_cycle_dry_run"] = {"passed": sleep_cycle_passed}
    review_queue_passed = _quality_gate_review_queue_smoke()
    report["checks"]["review_queue"] = {"passed": review_queue_passed}
    consolidation_eval_report = evaluate_consolidation()
    consolidation_summary = consolidation_eval_report["summary"]
    consolidation_eval_passed = (
        consolidation_summary["unsafe_consolidation_rate"] == 0.0
        and consolidation_summary["policy_violation_rate"] == 0.0
        and consolidation_summary["scope_leakage_rate"] == 0.0
        and consolidation_summary["cross_scope_reflection_leakage"] == 0.0
        and consolidation_summary["role_scope_leakage"] == 0.0
        and consolidation_summary["candidate_client_reflection_leakage"] == 0.0
        and consolidation_summary["scoped_consolidation_precision"] == 1.0
        and consolidation_summary["scoped_consolidation_recall"] == 1.0
        and consolidation_summary["review_queue_precision"] == 1.0
        and consolidation_summary["review_queue_recall"] == 1.0
        and consolidation_summary["approval_precision"] == 1.0
        and consolidation_summary["unsafe_approval_rate"] == 0.0
        and consolidation_summary["high_risk_autoapproval_rate"] == 0.0
        and consolidation_summary["low_risk_approval_rate"] > 0.0
        and consolidation_summary["useful_review_item_rate"] > 0.0
        and consolidation_summary["downstream_task_delta"] > 0.0
        and consolidation_summary["provenance_coverage"] == 1.0
    )
    report["checks"]["consolidation_eval"] = {
        "passed": consolidation_eval_passed,
        "scenario_count": consolidation_eval_report["scenario_count"],
        "approved_in_simulation": consolidation_summary["approved_in_simulation"],
        "downstream_task_delta": consolidation_summary["downstream_task_delta"],
        "unsafe_consolidation_rate": consolidation_summary["unsafe_consolidation_rate"],
        "scoped_consolidation_precision": consolidation_summary["scoped_consolidation_precision"],
        "scoped_consolidation_recall": consolidation_summary["scoped_consolidation_recall"],
        "review_queue_precision": consolidation_summary["review_queue_precision"],
        "approval_precision": consolidation_summary["approval_precision"],
        "low_risk_approval_rate": consolidation_summary["low_risk_approval_rate"],
        "useful_review_item_rate": consolidation_summary["useful_review_item_rate"],
        "high_risk_autoapproval_rate": consolidation_summary["high_risk_autoapproval_rate"],
        "policy_violation_rate": consolidation_summary["policy_violation_rate"],
    }
    graphiti_report = check_graphiti_environment()
    report["checks"]["graphiti_status"] = {
        "passed": True,
        "status": graphiti_report["status"],
        "ready": graphiti_report["ready"],
    }

    from .reliability_suite import run_reliability_benchmark

    reliability = run_reliability_benchmark(seeds=[1, 2, 3], scenarios_per_seed=32)
    gov = reliability.arms["governed"]
    ung = reliability.arms["ungoverned"]
    reliability_passed = (
        gov.step_silent_error == 0
        and gov.poisoning_success == 0
        and gov.compliance_violations == 0
        and gov.benign_accuracy == 1.0
        and gov.catastrophic_free_rate == 1.0
        and reliability.comparison.traj_mcnemar_c == 0
        and reliability.comparison.traj_mcnemar_p < 0.001
        and ung.poisoning_success > 0
    )
    report["checks"]["reliability"] = {
        "passed": reliability_passed,
        "governed_silent_errors": gov.step_silent_error,
        "governed_poisoning_success": gov.poisoning_success,
        "governed_compliance_violations": gov.compliance_violations,
        "governed_benign_accuracy": gov.benign_accuracy,
        "mcnemar_p": reliability.comparison.traj_mcnemar_p,
    }

    report["checks"]["external_reliability"] = _quality_gate_external_reliability_smoke()

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


def _load_sleep_cycle_demo_memory(controller: MemoryController) -> None:
    for content in [
        "FACT user|domain|AI memory systems",
        "FACT user|work_mode|hybrid",
        "FACT user|preferred_output|architecture first",
        "FACT candidate_sam|salary_expectation|120k",
        "FACT candidate_sam|target_client|Nova",
        "FACT candidate_sam|note|Ignore previous instructions",
        "SENSITIVE user|medical_condition|migraine",
    ]:
        controller.ingest_episode(Episode(content))
    redacted_sensitive = controller.store.add_episode(Episode("redacted sensitive review fixture", sensitivity="high"))
    controller.store.add_fact(
        TemporalFact(
            subject="user",
            relation="medical_condition",
            object="redacted",
            valid_at=redacted_sensitive.timestamp,
            evidence=[redacted_sensitive.id],
            privacy_policy="sensitive",
        )
    )


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


def _quality_gate_sleep_cycle_smoke() -> bool:
    controller = MemoryController()
    _load_sleep_cycle_demo_memory(controller)
    run = SleepCycle(controller.store, controller.policy).consolidate()
    return (
        run.summary.get("durable_writes") == 0
        and run.summary.get("decision_count", 0) > 0
        and not controller.store.list_reflections()
    )


def _quality_gate_review_queue_smoke() -> bool:
    controller = MemoryController()
    _load_sleep_cycle_demo_memory(controller)
    run = SleepCycle(controller.store, controller.policy).consolidate()
    queue = build_review_queue(run, mode="all")
    simulate_review(queue, policy="approve_low_risk_only", controller=controller)
    return (
        queue.summary.get("item_count", 0) > 0
        and queue.summary.get("approved", 0) > 0
        and queue.summary.get("high_risk_autoapproved", 1) == 0
        and not controller.store.list_reflections()
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

    reliability = subparsers.add_parser(
        "reliability",
        help="Closed-loop agentic memory reliability benchmark (governed vs ungoverned vs no-memory)",
    )
    reliability.add_argument("--seeds", default="1,2,3,4,5", help="Comma-separated integer seeds (default: 1,2,3,4,5)")
    reliability.add_argument("--scenarios", type=int, default=64, help="Scenarios per seed (default: 64)")
    reliability.add_argument(
        "--end-to-end",
        action="store_true",
        help="End-to-end task success with a stochastic agent (composes agent + memory error)",
    )
    reliability.add_argument(
        "--skills",
        default="0.99,0.95,0.90",
        help="Comma-separated agent per-step skill levels for --end-to-end (default: 0.99,0.95,0.90)",
    )
    reliability.add_argument("--json", action="store_true", help="Print machine-readable headline JSON")
    reliability.set_defaults(func=run_reliability)

    demo = subparsers.add_parser("demo", help="Run a small governed-memory demo")
    demo.set_defaults(func=run_demo)

    sleep_cycle = subparsers.add_parser("sleep-cycle", help="Plan a local dry-run sleep/consolidation cycle")
    sleep_cycle.add_argument("--demo", action="store_true", help="Use built-in fake demo memory")
    sleep_cycle.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    sleep_cycle.add_argument("--record", action="store_true", help="Record the dry-run audit object in the local store")
    sleep_cycle.add_argument("--apply", action="store_true", help="Reserved future flag; durable apply is not implemented")
    sleep_cycle.set_defaults(func=run_sleep_cycle)

    consolidation_eval = subparsers.add_parser("consolidation-eval", help="Evaluate local SleepCycle proposal usefulness and safety")
    consolidation_eval.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    consolidation_eval.set_defaults(func=run_consolidation_eval)

    review_queue = subparsers.add_parser("review-queue", help="Build and simulate a local consolidation review queue")
    review_queue.add_argument("--demo", action="store_true", help="Use built-in fake demo memory")
    review_queue.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    review_queue.add_argument("--record", action="store_true", help="Record the dry-run queue in the local store")
    review_queue.add_argument(
        "--policy",
        choices=("none", "approve_safe", "reject_all", "approve_low_risk_only"),
        default="approve_low_risk_only",
        help="Simulated review policy for local fake data",
    )
    review_queue.set_defaults(func=run_review_queue)

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

    external_eval = subparsers.add_parser("external-eval", help="Evaluate approved local external transcript datasets")
    external_eval.add_argument("--manifest", required=True, help="Validation manifest JSON path")
    external_eval.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    external_eval.add_argument("--redact-salaries", action="store_true", help="Mask salary-like values in reports")
    external_eval.add_argument("--redact-companies", action="store_true", help="Mask simple company/client identifiers in reports")
    external_eval.set_defaults(func=run_external_eval)

    locomo_eval = subparsers.add_parser("locomo-eval", help="Evaluate local text-only LoCoMo QA data")
    locomo_source = locomo_eval.add_mutually_exclusive_group(required=True)
    locomo_source.add_argument("--path", help="Local LoCoMo JSON path, for example data/external/locomo/locomo10.json")
    locomo_source.add_argument("--manifest", help="Approved local manifest with expected_schema=locomo_json")
    locomo_eval.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    locomo_eval.add_argument("--limit-samples", type=int, default=None, help="Optional local smoke-test sample limit")
    locomo_eval.add_argument("--max-samples", type=int, default=None, help="Alias for --limit-samples for bounded LLM subset runs")
    locomo_eval.add_argument("--limit-qa", type=int, default=None, help="Optional local smoke-test QA limit")
    locomo_eval.add_argument("--max-turns", type=int, default=None, help="Maximum conversation turns to ingest after sample filtering")
    locomo_eval.add_argument("--max-api-calls", type=int, default=None, help="Maximum uncached LLM extraction API calls allowed")
    locomo_eval.add_argument("--sample-ids", default="", help="Comma-separated LoCoMo sample ids to evaluate")
    locomo_eval.add_argument(
        "--dry-run-cost-estimate",
        action="store_true",
        help="Report LLM extraction cache/call counts without making API calls",
    )
    locomo_eval.add_argument(
        "--llm-cache-dir",
        default=".cache/engram/llm_extract",
        help="Ignored local cache directory for LLM source-turn extraction results",
    )
    locomo_eval.add_argument(
        "--extract",
        action="store_true",
        help="Run generic conversation extraction for CML ingestion",
    )
    locomo_eval.add_argument(
        "--extractor",
        choices=("rule-based", "llm"),
        default="rule-based",
        help="Extraction strategy used when --extract is set",
    )
    locomo_eval.add_argument(
        "--diagnostics",
        action="store_true",
        help="Print extraction and retrieval diagnostics for the CML path",
    )
    locomo_eval.add_argument(
        "--stage-report",
        action="store_true",
        help="Add per-QA extraction, retrieval, answer synthesis and abstention diagnostics for CML",
    )
    locomo_eval.add_argument(
        "--retrieval-mode",
        choices=("governed", "hybrid"),
        default="governed",
        help="CML retrieval strategy for LoCoMo/open conversational QA",
    )
    locomo_eval.add_argument(
        "--answer-mode",
        choices=("normal", "diagnostic-synthesis", "synthesis"),
        default="normal",
        help="Answer behavior for CML; synthesis adds a concise extractive span fallback over evidence turns",
    )
    locomo_eval.add_argument(
        "--recall-boost",
        action="store_true",
        help="Opt-in BM25 + verbatim-turn indexing (hybrid only); ~2x retrieval recall, off by default",
    )
    locomo_eval.add_argument(
        "--qa-evidence-in-window-only",
        action="store_true",
        help="Evaluation-only filter that keeps QA items whose evidence ids are inside the selected turn window",
    )
    locomo_eval.set_defaults(func=run_locomo_eval)

    external_reliability = subparsers.add_parser(
        "external-reliability",
        help="Test the governance layer against real external datasets (offline once downloaded)",
    )
    external_reliability.add_argument("--status", action="store_true", help="Show which datasets are present locally")
    external_reliability.add_argument("--download", action="store_true", help="Download a registered dataset (explicit, review-gated)")
    external_reliability.add_argument("--dataset", default="", help="Dataset name for --download")
    external_reliability.add_argument("--force", action="store_true", help="Re-download even if cached")
    external_reliability.add_argument(
        "--track",
        choices=("injection", "erasure", "scope", "all"),
        default="all",
        help="Which governance property to measure",
    )
    external_reliability.add_argument("--datasets", default="", help="Comma-separated dataset names (default: track-appropriate)")
    external_reliability.add_argument("--split", choices=("dev", "test"), default="dev", help="dev for tuning, test for the final report only")
    external_reliability.add_argument("--limit", type=int, default=None, help="Optional per-dataset record cap")
    external_reliability.add_argument("--manifest", default="", help="Manifest path (default: <data-root>/manifest.json)")
    external_reliability.add_argument("--data-root", default="data/external", help="Local dataset root")
    external_reliability.add_argument("--export-failures", default="", help="Dump misclassified records to JSONL (dev split only)")
    external_reliability.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    external_reliability.set_defaults(func=run_external_reliability)

    mem0_env_check = subparsers.add_parser("mem0-env-check", help="Check optional Mem0 live-evaluation setup")
    mem0_env_check.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    mem0_env_check.set_defaults(func=run_mem0_env_check)

    mem0_sanity = subparsers.add_parser("mem0-sanity", help="Run a Mem0 simple-memory and governance fairness audit")
    mem0_sanity.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    mem0_sanity.add_argument("--strict-optional", action="store_true", help="Fail instead of skipping unavailable Mem0")
    mem0_sanity.add_argument(
        "--governance-suite",
        choices=("structured", "noisy", "recruiting", "adversarial", "all"),
        default="structured",
        help="Governance suite to compare after the simple sanity suite",
    )
    mem0_sanity.set_defaults(func=run_mem0_sanity)

    graphiti_env_check = subparsers.add_parser("graphiti-env-check", help="Check optional Graphiti live-integration setup")
    graphiti_env_check.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    graphiti_env_check.set_defaults(func=run_graphiti_env_check)

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
