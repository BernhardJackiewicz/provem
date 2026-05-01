import json
import os
import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from cognitive_memory.cli import build_parser
from cognitive_memory.graphiti_env import (
    check_graphiti_environment,
    dumps_graphiti_env_report,
    graphiti_connection_config,
)


def fake_graphiti_find_spec(name):
    if name == "graphiti_core":
        return object()
    return None


def missing_find_spec(_name):
    return None


def _load_script(name):
    path = os.path.join(ROOT, "scripts", name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GraphitiEnvTests(unittest.TestCase):
    def test_ready_with_dependency_and_neo4j_config(self):
        report = check_graphiti_environment(
            env={
                "GRAPHITI_NEO4J_URI": "bolt://localhost:7687",
                "GRAPHITI_NEO4J_USER": "neo4j",
                "GRAPHITI_NEO4J_PASSWORD": "secret_password",
            },
            include_dotenv=False,
            python_version=(3, 10, 0),
            find_spec=fake_graphiti_find_spec,
        )

        rendered = dumps_graphiti_env_report(report)
        self.assertTrue(report["ready"])
        self.assertEqual(report["status"], "ready")
        self.assertIn("GRAPHITI_NEO4J_PASSWORD", rendered)
        self.assertNotIn("secret_password", rendered)

    def test_missing_dependency_and_config_fails_safely(self):
        report = check_graphiti_environment(
            env={},
            include_dotenv=False,
            python_version=(3, 10, 0),
            find_spec=missing_find_spec,
        )
        rendered = dumps_graphiti_env_report(report)

        self.assertFalse(report["ready"])
        self.assertEqual(report["status"], "not_installed")
        self.assertIn("graphiti_import", json.dumps(report))
        self.assertIn("GRAPHITI_NEO4J_URI", rendered)

    def test_dotenv_is_loaded_without_printing_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, ".env"), "w", encoding="utf-8") as handle:
                handle.write("GRAPHITI_NEO4J_URI=bolt://localhost:7687\n")
                handle.write("GRAPHITI_NEO4J_USER=neo4j\n")
                handle.write("GRAPHITI_NEO4J_PASSWORD=hidden_graphiti_secret\n")
            report = check_graphiti_environment(
                root=directory,
                env={},
                include_dotenv=True,
                python_version=(3, 10, 0),
                find_spec=fake_graphiti_find_spec,
            )

        rendered = dumps_graphiti_env_report(report)
        self.assertTrue(report["ready"])
        self.assertIn("GRAPHITI_NEO4J_PASSWORD", rendered)
        self.assertNotIn("hidden_graphiti_secret", rendered)

    def test_connection_config_loads_dotenv_for_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, ".env"), "w", encoding="utf-8") as handle:
                handle.write("GRAPHITI_NEO4J_URI=bolt://localhost:7687\n")
                handle.write("GRAPHITI_NEO4J_USER=neo4j\n")
                handle.write("GRAPHITI_NEO4J_PASSWORD=hidden_graphiti_secret\n")
            config = graphiti_connection_config(root=directory, env={}, include_dotenv=True)

        self.assertEqual(config["neo4j_uri"], "bolt://localhost:7687")
        self.assertEqual(config["neo4j_user"], "neo4j")
        self.assertEqual(config["neo4j_password"], "hidden_graphiti_secret")

    def test_python_version_is_required_for_live_readiness(self):
        report = check_graphiti_environment(
            env={
                "GRAPHITI_NEO4J_URI": "bolt://localhost:7687",
                "GRAPHITI_NEO4J_USER": "neo4j",
                "GRAPHITI_NEO4J_PASSWORD": "secret_password",
            },
            include_dotenv=False,
            python_version=(3, 9, 6),
            find_spec=fake_graphiti_find_spec,
        )

        self.assertFalse(report["ready"])
        self.assertEqual(report["status"], "not_configured")
        self.assertTrue(any(item["name"] == "python_version" and not item["passed"] for item in report["checks"]))

    def test_cli_accepts_graphiti_env_check_command(self):
        args = build_parser().parse_args(["graphiti-env-check", "--json"])

        self.assertEqual(args.command, "graphiti-env-check")
        self.assertTrue(args.json)

    def test_check_graphiti_env_script_uses_core_checker(self):
        module = _load_script("check_graphiti_env.py")
        fake_report = {"ready": True, "status": "ready", "checks": [], "notes": []}

        with mock.patch.object(module, "check_graphiti_environment", return_value=fake_report):
            with mock.patch.object(module, "dumps_graphiti_env_report", return_value="checked"):
                with mock.patch.object(sys, "argv", ["check_graphiti_env.py"]):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        exit_code = module.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue().strip(), "checked")

    def test_smoke_graphiti_missing_env_exits_with_setup_failure(self):
        module = _load_script("smoke_graphiti.py")
        fake_report = {
            "ready": False,
            "status": "not_installed",
            "checks": [
                {"name": "graphiti_import", "passed": False, "detail": "missing", "required": True},
                {"name": "GRAPHITI_NEO4J_URI or NEO4J_URI", "passed": False, "detail": "missing", "required": True},
            ],
            "notes": ["No secret values are printed."],
        }

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = module.run_smoke(
                environment_checker=lambda: fake_report,
                config_loader=lambda: {},
                backend_factory=lambda **_kwargs: object(),
            )

        rendered = output.getvalue()
        self.assertEqual(exit_code, 2)
        self.assertIn("Graphiti environment check: FAIL", rendered)
        self.assertIn("GRAPHITI_NEO4J_URI", rendered)

    def test_smoke_graphiti_ready_env_but_adapter_stub_exits_clearly(self):
        module = _load_script("smoke_graphiti.py")
        fake_report = {"ready": True, "status": "ready", "checks": [], "notes": []}

        class StubBackend:
            def __init__(self, **_kwargs):
                pass

            def write_episode(self, _episode):
                raise NotImplementedError("stub write")

            def write_temporal_fact(self, _fact):
                raise AssertionError("write_temporal_fact should not be reached after stub write")

            def query_current_facts(self, _request):
                raise AssertionError("query should not be reached after stub write")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = module.run_smoke(
                environment_checker=lambda: fake_report,
                config_loader=lambda: {
                    "neo4j_uri": "bolt://localhost:7687",
                    "neo4j_user": "neo4j",
                    "neo4j_password": "hidden_graphiti_secret",
                },
                backend_factory=StubBackend,
            )

        rendered = output.getvalue()
        self.assertEqual(exit_code, 2)
        self.assertIn("Graphiti adapter is not implemented", rendered)
        self.assertIn("No live Graphiti result was produced", rendered)
        self.assertNotIn("hidden_graphiti_secret", rendered)

    def test_optional_neo4j_compose_file_contains_no_real_secret_or_host_path(self):
        compose_path = os.path.join(ROOT, "docker-compose.graphiti.yml")
        with open(compose_path, "r", encoding="utf-8") as handle:
            content = handle.read()

        self.assertIn("${GRAPHITI_NEO4J_PASSWORD:-change-me-local-only}", content)
        self.assertIn("tmpfs:", content)
        self.assertNotIn("/Users/", content)
        self.assertNotIn("hidden_graphiti_secret", content)


if __name__ == "__main__":
    unittest.main()
