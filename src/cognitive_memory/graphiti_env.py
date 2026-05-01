from __future__ import annotations

import importlib.util
import json
import os
import sys
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .mem0_env import _read_dotenv


Check = Dict[str, Any]


def check_graphiti_environment(
    root: str = ".",
    env: Optional[Mapping[str, str]] = None,
    include_dotenv: bool = True,
    python_version: Optional[Tuple[int, int, int]] = None,
    find_spec: Optional[Callable[[str], object]] = None,
) -> Dict[str, Any]:
    merged_env = _merged_env(root, env, include_dotenv)
    checks: List[Check] = []

    version = python_version or sys.version_info[:3]
    checks.append(
        _check(
            "python_version",
            version >= (3, 10, 0),
            "%s; Graphiti live integration readiness expects Python >= 3.10" % _format_version(version),
        )
    )

    spec_finder = find_spec or importlib.util.find_spec
    graphiti_installed = _has_module(spec_finder, "graphiti") or _has_module(spec_finder, "graphiti_core")
    checks.append(
        _check(
            "graphiti_import",
            graphiti_installed,
            "graphiti or graphiti_core importable" if graphiti_installed else "graphiti/graphiti_core not installed",
        )
    )

    uri = merged_env.get("GRAPHITI_NEO4J_URI") or merged_env.get("NEO4J_URI")
    user = merged_env.get("GRAPHITI_NEO4J_USER") or merged_env.get("NEO4J_USER")
    password = merged_env.get("GRAPHITI_NEO4J_PASSWORD") or merged_env.get("NEO4J_PASSWORD")
    checks.extend(
        [
            _env_check("GRAPHITI_NEO4J_URI or NEO4J_URI", bool(uri)),
            _env_check("GRAPHITI_NEO4J_USER or NEO4J_USER", bool(user)),
            _env_check("GRAPHITI_NEO4J_PASSWORD or NEO4J_PASSWORD", bool(password)),
        ]
    )

    ready = all(item["passed"] for item in checks if item["required"])
    status = "ready" if ready else ("not_installed" if not graphiti_installed else "not_configured")
    return {
        "ready": ready,
        "status": status,
        "checks": checks,
        "notes": [
            "No secret values are printed.",
            "This is setup readiness only, not live Graphiti validation.",
        ],
    }


def graphiti_connection_config(
    root: str = ".",
    env: Optional[Mapping[str, str]] = None,
    include_dotenv: bool = True,
) -> Dict[str, str]:
    """Return Graphiti connection config for local setup scripts.

    Callers must not print this dictionary directly because it can contain a
    password loaded from the shell or ignored `.env`.
    """

    merged_env = _merged_env(root, env, include_dotenv)
    return {
        "neo4j_uri": merged_env.get("GRAPHITI_NEO4J_URI") or merged_env.get("NEO4J_URI") or "",
        "neo4j_user": merged_env.get("GRAPHITI_NEO4J_USER") or merged_env.get("NEO4J_USER") or "",
        "neo4j_password": merged_env.get("GRAPHITI_NEO4J_PASSWORD") or merged_env.get("NEO4J_PASSWORD") or "",
    }


def dumps_graphiti_env_report(report: Dict[str, Any], as_json: bool = False) -> str:
    if as_json:
        return json.dumps(report, indent=2, sort_keys=True)
    lines = ["Graphiti environment check: %s" % ("PASS" if report["ready"] else "FAIL")]
    lines.append("Status: %s" % report["status"])
    for item in report["checks"]:
        status = "PASS" if item["passed"] else ("WARN" if not item["required"] else "FAIL")
        lines.append("- %s: %s (%s)" % (item["name"], status, item["detail"]))
    for note in report.get("notes", []):
        lines.append("Note: %s" % note)
    return "\n".join(lines)


def _merged_env(root: str, env: Optional[Mapping[str, str]], include_dotenv: bool) -> Dict[str, str]:
    merged = dict(os.environ if env is None else env)
    if include_dotenv:
        for key, value in _read_dotenv_path(root).items():
            if not merged.get(key):
                merged[key] = value
    return merged


def _read_dotenv_path(root: str) -> Dict[str, str]:
    from pathlib import Path

    return _read_dotenv(Path(root) / ".env")


def _has_module(find_spec: Callable[[str], object], name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _env_check(name: str, present: bool) -> Check:
    return _check(name, present, "set" if present else "missing")


def _check(name: str, passed: bool, detail: str, required: bool = True) -> Check:
    return {"name": name, "passed": bool(passed), "detail": detail, "required": required}


def _format_version(version: Iterable[int]) -> str:
    parts = list(version)
    return ".".join(str(part) for part in parts[:3])
