import json
import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from cognitive_memory.cli import build_parser
from cognitive_memory.graphiti_env import check_graphiti_environment, dumps_graphiti_env_report


def fake_graphiti_find_spec(name):
    if name == "graphiti_core":
        return object()
    return None


def missing_find_spec(_name):
    return None


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


if __name__ == "__main__":
    unittest.main()
