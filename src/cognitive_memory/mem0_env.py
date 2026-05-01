from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple


Check = Dict[str, Any]


def check_mem0_environment(
    root: str = ".",
    env: Optional[Mapping[str, str]] = None,
    include_dotenv: bool = True,
    python_version: Optional[Tuple[int, int, int]] = None,
    find_spec: Optional[Callable[[str], object]] = None,
    qdrant_checker: Optional[Callable[[str, int], bool]] = None,
    command_runner: Optional[Callable[[List[str]], Tuple[int, str, str]]] = None,
) -> Dict[str, Any]:
    """Return a secret-safe Mem0 environment readiness report."""

    merged_env = _merged_env(root, env, include_dotenv)
    checks: List[Check] = []
    mode = (merged_env.get("MEM0_MODE") or "platform").strip().lower() or "platform"

    version = python_version or sys.version_info[:3]
    checks.append(
        _check(
            "python_version",
            version >= (3, 10, 0),
            "%s; Mem0 live evaluation requires Python >= 3.10" % _format_version(version),
        )
    )

    spec_finder = find_spec or importlib.util.find_spec
    mem0_installed = _has_module(spec_finder, "mem0") or _has_module(spec_finder, "mem0ai")
    checks.append(
        _check(
            "mem0_import",
            mem0_installed,
            "mem0 or mem0ai importable" if mem0_installed else "mem0/mem0ai not installed",
        )
    )

    checks.append(_check("mode", mode in ("platform", "oss"), mode if mode in ("platform", "oss") else "unsupported mode"))

    if mode == "platform":
        checks.extend(_platform_checks(merged_env))
    elif mode == "oss":
        checks.extend(_oss_checks(merged_env, qdrant_checker or _tcp_reachable, command_runner or _run_command))

    ready = all(item["passed"] for item in checks if item["required"])
    return {
        "ready": ready,
        "mode": mode,
        "checks": checks,
        "notes": [
            "No secret values are printed.",
            "A skipped or failed setup is not Mem0 benchmark evidence.",
        ],
    }


def dumps_mem0_env_report(report: Dict[str, Any], as_json: bool = False) -> str:
    if as_json:
        return json.dumps(report, indent=2, sort_keys=True)
    lines = ["Mem0 environment check: %s" % ("PASS" if report["ready"] else "FAIL")]
    lines.append("Mode: %s" % report["mode"])
    for item in report["checks"]:
        status = "PASS" if item["passed"] else ("WARN" if not item["required"] else "FAIL")
        lines.append("- %s: %s (%s)" % (item["name"], status, item["detail"]))
    for note in report.get("notes", []):
        lines.append("Note: %s" % note)
    return "\n".join(lines)


def _platform_checks(env: Mapping[str, str]) -> List[Check]:
    checks = [
        _env_check("MEM0_API_KEY", bool(env.get("MEM0_API_KEY")), required=True),
    ]
    if env.get("api_key") and not env.get("MEM0_API_KEY"):
        checks.append(
            _check(
                "api_key_placeholder",
                False,
                "`api_key` is set but ignored by the Mem0 adapter; set MEM0_API_KEY instead",
                required=False,
            )
        )
    return checks


def _oss_checks(
    env: Mapping[str, str],
    qdrant_checker: Callable[[str, int], bool],
    command_runner: Callable[[List[str]], Tuple[int, str, str]],
) -> List[Check]:
    checks: List[Check] = []
    config_path = env.get("MEM0_OSS_CONFIG_PATH", "").strip()
    config_exists = bool(config_path and Path(config_path).expanduser().exists())
    checks.append(_env_check("MEM0_OSS_CONFIG_PATH", bool(config_path), required=True))
    if config_path:
        checks.append(_check("oss_config_file", config_exists, "configured path exists" if config_exists else "configured path missing"))

    host = env.get("QDRANT_HOST") or "127.0.0.1"
    try:
        port = int(env.get("QDRANT_PORT") or "6333")
    except ValueError:
        port = 6333
    qdrant_ok = qdrant_checker(host, port)
    checks.append(_check("qdrant", qdrant_ok, "%s:%s reachable" % (host, port) if qdrant_ok else "%s:%s not reachable" % (host, port)))

    configured_embedding_model = _configured_embedding_model(env, config_path if config_exists else "")
    available_models = _ollama_models(command_runner)
    if configured_embedding_model and available_models is not None:
        checks.append(
            _check(
                "embedding_model",
                configured_embedding_model in available_models,
                "configured model is available" if configured_embedding_model in available_models else "configured model not found in Ollama",
            )
        )
    elif configured_embedding_model:
        checks.append(_check("embedding_model", True, "configured model present; availability not detectable", required=False))
    else:
        checks.append(_check("embedding_model", True, "not configured or not detectable", required=False))
    return checks


def _merged_env(root: str, env: Optional[Mapping[str, str]], include_dotenv: bool) -> Dict[str, str]:
    merged = dict(os.environ if env is None else env)
    if include_dotenv:
        dotenv = Path(root) / ".env"
        for key, value in _read_dotenv(dotenv).items():
            if not merged.get(key):
                merged[key] = value
    return merged


def _read_dotenv(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    values: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :]
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _has_module(find_spec: Callable[[str], object], name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _env_check(name: str, present: bool, required: bool) -> Check:
    return _check(name, present, "set" if present else "missing", required=required)


def _check(name: str, passed: bool, detail: str, required: bool = True) -> Check:
    return {"name": name, "passed": bool(passed), "detail": detail, "required": required}


def _format_version(version: Iterable[int]) -> str:
    parts = list(version)
    return ".".join(str(part) for part in parts[:3])


def _tcp_reachable(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


def _run_command(command: List[str]) -> Tuple[int, str, str]:
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return result.returncode, result.stdout, result.stderr


def _ollama_models(command_runner: Callable[[List[str]], Tuple[int, str, str]]) -> Optional[List[str]]:
    returncode, stdout, _stderr = command_runner(["ollama", "list"])
    if returncode != 0:
        return None
    models = []
    for line in stdout.splitlines()[1:]:
        parts = line.split()
        if parts:
            models.append(parts[0])
    return models


def _configured_embedding_model(env: Mapping[str, str], config_path: str) -> str:
    for key in ("MEM0_OSS_EMBEDDING_MODEL", "MEM0_EMBEDDING_MODEL", "OLLAMA_EMBEDDING_MODEL"):
        if env.get(key):
            return str(env[key]).strip()
    if not config_path:
        return ""
    try:
        data = json.loads(Path(config_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return _find_embedding_model(data)


def _find_embedding_model(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("embedding_model", "model"):
            if key in value and isinstance(value[key], str):
                return value[key]
        for child_key, child_value in value.items():
            if "embed" in str(child_key).lower():
                found = _find_embedding_model(child_value)
                if found:
                    return found
        for child_value in value.values():
            found = _find_embedding_model(child_value)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_embedding_model(item)
            if found:
                return found
    return ""
