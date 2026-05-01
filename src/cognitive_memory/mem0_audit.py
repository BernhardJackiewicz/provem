from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional

from .adapters.base import AdapterConfigurationError, OptionalDependencyNotInstalled
from .benchmark import BenchmarkRunner, Scenario, dt
from .models import Episode


def mem0_simple_scenarios() -> List[Scenario]:
    """Small no-governance sanity suite for optional Mem0 comparison.

    These cases intentionally avoid deletion, policy, scope conflict and
    adversarial behavior. If Mem0 cannot retrieve these, the adapter/setup is
    suspect before any governance conclusion is useful.
    """

    return [
        Scenario(
            name="simple_user_likes_coffee",
            suite="mem0_sanity",
            category="simple_memory",
            episodes=[Episode("FACT user|drink_preference|coffee", timestamp=dt(1))],
            query="drink preference coffee",
            expected_include=["coffee"],
        ),
        Scenario(
            name="simple_user_timezone",
            suite="mem0_sanity",
            category="simple_memory",
            episodes=[Episode("FACT user|timezone|Europe_Berlin", timestamp=dt(1))],
            query="timezone Europe Berlin",
            expected_include=["Europe_Berlin"],
        ),
        Scenario(
            name="simple_user_prefers_email",
            suite="mem0_sanity",
            category="simple_memory",
            episodes=[Episode("FACT user|contact_channel|email", timestamp=dt(1))],
            query="contact channel email",
            expected_include=["email"],
        ),
        Scenario(
            name="simple_candidate_salary",
            suite="mem0_sanity",
            category="simple_memory",
            request_user_id="recruiter",
            episodes=[Episode("FACT candidate_ava|salary_expectation|110k", timestamp=dt(1), user_id="recruiter")],
            query="candidate ava salary expectation",
            expected_include=["110k"],
        ),
        Scenario(
            name="simple_client_budget",
            suite="mem0_sanity",
            category="simple_memory",
            request_user_id="recruiter",
            episodes=[Episode("FACT client_nova|budget|150k", timestamp=dt(1), user_id="recruiter")],
            query="client nova budget",
            expected_include=["150k"],
        ),
        Scenario(
            name="simple_follow_up_fact",
            suite="mem0_sanity",
            category="simple_memory",
            episodes=[
                Episode("FACT user|preferred_editor|VSCode", timestamp=dt(1)),
                Episode("FACT user|primary_language|Python", timestamp=dt(2)),
            ],
            query="primary language Python",
            expected_include=["Python"],
        ),
        Scenario(
            name="simple_project_stack",
            suite="mem0_sanity",
            category="simple_memory",
            project_id="alpha",
            episodes=[Episode("FACT project_alpha|backend|FastAPI", timestamp=dt(1), project_id="alpha")],
            query="project alpha backend FastAPI",
            expected_include=["FastAPI"],
        ),
        Scenario(
            name="simple_candidate_notice",
            suite="mem0_sanity",
            category="simple_memory",
            request_user_id="recruiter",
            episodes=[Episode("FACT candidate_lee|notice_period|2_weeks", timestamp=dt(1), user_id="recruiter")],
            query="candidate lee notice period",
            expected_include=["2_weeks"],
        ),
        Scenario(
            name="simple_role_requirement",
            suite="mem0_sanity",
            category="simple_memory",
            request_user_id="recruiter",
            episodes=[Episode("FACT role_backend|required_skill|Python", timestamp=dt(1), user_id="recruiter")],
            query="role backend required skill",
            expected_include=["Python"],
        ),
        Scenario(
            name="simple_company_location",
            suite="mem0_sanity",
            category="simple_memory",
            request_user_id="recruiter",
            episodes=[Episode("FACT client_orion|location|Munich", timestamp=dt(1), user_id="recruiter")],
            query="client orion location Munich",
            expected_include=["Munich"],
        ),
        Scenario(
            name="simple_historical_first_timezone",
            suite="mem0_sanity",
            category="simple_historical_memory",
            episodes=[
                Episode("FACT user|timezone|CET", timestamp=dt(1)),
                Episode("FACT user|timezone|PST", timestamp=dt(2)),
            ],
            query="historical timezone CET",
            time_scope="as_of_date",
            as_of=dt(1),
            expected_include=["CET"],
            expected_exclude=["PST"],
        ),
        Scenario(
            name="simple_current_after_update",
            suite="mem0_sanity",
            category="simple_current_update",
            episodes=[
                Episode("FACT user|work_mode|remote", timestamp=dt(1)),
                Episode("FACT user|work_mode|hybrid", timestamp=dt(2)),
            ],
            query="current work mode hybrid",
            expected_include=["hybrid"],
            expected_exclude=["remote"],
        ),
    ]


def evaluate_mem0_audit(
    strict_optional: bool = False,
    governance_suite: str = "structured",
    mem0_backend_factory: Optional[Callable[[], object]] = None,
) -> Dict[str, object]:
    simple_report = _run_report(
        mem0_simple_scenarios(),
        suite="mem0_sanity",
        strict_optional=strict_optional,
        mem0_backend_factory=mem0_backend_factory,
    )
    governance_report = _run_report(
        None,
        suite=governance_suite,
        strict_optional=strict_optional,
        mem0_backend_factory=mem0_backend_factory,
    )
    simple_mem0 = _system_summary(simple_report, "mem0_external")
    governance_mem0 = _system_summary(governance_report, "mem0_external")
    return {
        "status": "complete" if simple_mem0 and governance_mem0 else "skipped",
        "simple_suite": _suite_block(simple_report),
        "governance_suite": _suite_block(governance_report),
        "summary": {
            "simple_memory_accuracy": _metric(simple_mem0, "accuracy"),
            "mem0_retrieval_success_rate": _retrieval_success_rate(simple_report, "mem0_external"),
            "mem0_answer_format_mismatch_rate": _failure_category_rate(simple_report, "answer_format_mismatch"),
            "mem0_governance_gap_rate": _failure_category_rate(governance_report, "policy_missing")
            + _failure_category_rate(governance_report, "scope_missing"),
            "mem0_abstain_rate": _abstain_rate(simple_report, "mem0_external"),
            "mem0_latency_p50_ms": _metric(simple_mem0, "p50_retrieval_latency_ms"),
            "mem0_latency_p95_ms": _metric(simple_mem0, "p95_retrieval_latency_ms"),
            "governance_accuracy": _metric(governance_mem0, "accuracy"),
        },
        "fairness_audit": fairness_audit(governance_suite=governance_suite),
    }


def _run_report(
    scenarios: Optional[List[Scenario]],
    suite: str,
    strict_optional: bool,
    mem0_backend_factory: Optional[Callable[[], object]],
) -> Dict[str, object]:
    return BenchmarkRunner(
        scenarios=scenarios,
        suite=suite,
        include_mem0=True,
        skip_optional=not strict_optional,
        mem0_backend_factory=mem0_backend_factory,
    ).run()


def _suite_block(report: Dict[str, object]) -> Dict[str, object]:
    mem0_scores = [score for score in report["scores"] if score["system"] == "mem0_external"]
    return {
        "suite": report["suite"],
        "scenario_count": report["scenario_count"],
        "skipped_optional": dict(report.get("skipped_optional") or {}),
        "summary": report["summary"],
        "mem0_diagnostics": mem0_failure_diagnostics(mem0_scores),
    }


def mem0_failure_diagnostics(scores: List[Dict[str, object]]) -> List[Dict[str, object]]:
    diagnostics = []
    for score in scores:
        if score.get("passed"):
            continue
        normalized = score.get("normalized_fields") or {}
        selected_memories = score.get("selected_memories") or []
        diagnostics.append(
            {
                "scenario_id": score.get("scenario", ""),
                "scenario_type": score.get("category", ""),
                "write_succeeded": True,
                "raw_normalized_answer": score.get("answer", ""),
                "retrieved_memories": selected_memories,
                "unavailable_fields": _unavailable_fields(normalized),
                "failure_category": categorize_mem0_failure(score),
            }
        )
    return diagnostics


def categorize_mem0_failure(score: Dict[str, object]) -> str:
    if score.get("passed"):
        return "passed"
    answer = str(score.get("answer", "")).strip().lower()
    selected = score.get("selected_memories") or []
    normalized = score.get("normalized_fields") or {}
    if answer == "abstain" or not selected:
        return "no_retrieval"
    if score.get("deleted_leak") or score.get("do_not_use_leak") or score.get("confidentiality_leak"):
        return "policy_missing"
    if score.get("cross_project_leak") or score.get("candidate_client_scope_leak"):
        return "scope_missing"
    if score.get("unsafe_recall") or score.get("prompt_injection_success"):
        return "policy_missing"
    category = str(score.get("category", ""))
    if "historical" in category:
        return "wrong_historical_fact"
    if category in ("current_fact", "updated_preference", "simple_current_update"):
        return "wrong_current_fact"
    if selected and normalized.get("selected_memories_available") is True:
        return "answer_format_mismatch"
    if normalized.get("selected_memories_available") is False:
        return "adapter_issue"
    return "expected_behavior_gap"


def fairness_audit(governance_suite: str = "structured") -> Dict[str, object]:
    return {
        "same_inputs": True,
        "expected_outputs_visible_to_mem0": False,
        "namespace_isolation": "synthetic per-run/per-scenario user namespace",
        "cleanup_behavior": "best-effort delete_all for synthetic namespace when SDK supports it",
        "write_before_read_ordering": True,
        "flush_behavior": "adapter requests async_mode=False when SDK supports it",
        "query_format": "scenario query string passed directly to Mem0 search",
        "governance_suite": governance_suite,
        "abstain_interpretation": "derived from empty/no usable Mem0 search results",
        "parallelization": "not enabled by default; write-before-read fairness and rate limits take priority",
    }


def _unavailable_fields(normalized: Dict[str, object]) -> List[str]:
    fields = []
    if normalized.get("provenance_available") is False:
        fields.append("provenance")
    if normalized.get("abstention_available") is False:
        fields.append("native_abstention")
    if "deletion_policy_available" not in normalized:
        fields.append("native_deletion_policy")
    if "do_not_use_policy_available" not in normalized:
        fields.append("native_do_not_use_policy")
    return fields


def _system_summary(report: Dict[str, object], system: str) -> Optional[Dict[str, object]]:
    summary = report.get("summary") or {}
    if not isinstance(summary, dict):
        return None
    selected = summary.get(system)
    return selected if isinstance(selected, dict) else None


def _metric(summary: Optional[Dict[str, object]], key: str) -> Optional[float]:
    if not summary:
        return None
    value = summary.get(key)
    if value is None:
        return None
    return float(value)


def _abstain_rate(report: Dict[str, object], system: str) -> Optional[float]:
    scores = [score for score in report["scores"] if score["system"] == system]
    if not scores:
        return None
    return sum(1 for score in scores if score.get("actual_abstain")) / len(scores)


def _retrieval_success_rate(report: Dict[str, object], system: str) -> Optional[float]:
    scores = [score for score in report["scores"] if score["system"] == system]
    if not scores:
        return None
    return sum(1 for score in scores if score.get("selected_memories")) / len(scores)


def _failure_category_rate(report: Dict[str, object], category: str) -> float:
    scores = [score for score in report["scores"] if score["system"] == "mem0_external" and not score.get("passed")]
    if not scores:
        return 0.0
    return sum(1 for score in scores if categorize_mem0_failure(score) == category) / len(scores)


def dumps_mem0_audit_report(report: Dict[str, object], as_json: bool = False) -> str:
    if as_json:
        return json.dumps(report, indent=2, sort_keys=True)
    lines = ["Mem0 fairness audit", "Status: %s" % report["status"]]
    simple = report["simple_suite"]
    governance = report["governance_suite"]
    lines.append("Simple suite: %s scenarios" % simple["scenario_count"])
    lines.extend(_summary_lines(simple["summary"], prefix="simple"))
    lines.append("Governance suite: %s (%s scenarios)" % (governance["suite"], governance["scenario_count"]))
    lines.extend(_summary_lines(governance["summary"], prefix="governance"))
    summary = report["summary"]
    lines.append("Metrics:")
    for key in sorted(summary):
        value = summary[key]
        lines.append("- %s: %s" % (key, "unavailable" if value is None else "%.4f" % value))
    lines.append("Fairness audit:")
    for key, value in report["fairness_audit"].items():
        lines.append("- %s: %s" % (key, value))
    for block_name, block in (("simple", simple), ("governance", governance)):
        diagnostics = block["mem0_diagnostics"]
        lines.append("%s Mem0 failed scenarios: %s" % (block_name.capitalize(), len(diagnostics)))
        for item in diagnostics[:20]:
            lines.append(
                "- %s / %s: %s"
                % (item["scenario_type"], item["scenario_id"], item["failure_category"])
            )
        if len(diagnostics) > 20:
            lines.append("- ... %s more" % (len(diagnostics) - 20))
    return "\n".join(lines)


def _summary_lines(summary: Dict[str, object], prefix: str) -> List[str]:
    lines = []
    for system in ("cognitive_memory_layer", "mem0_external"):
        values = summary.get(system)
        if not isinstance(values, dict):
            continue
        lines.append(
            "- %s.%s: %.0f/%.0f accuracy %.2f p95 %.3f ms"
            % (
                prefix,
                system,
                values["passed"],
                values["total"],
                values["accuracy"],
                values["p95_retrieval_latency_ms"],
            )
        )
    return lines
