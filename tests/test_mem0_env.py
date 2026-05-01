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
from cognitive_memory.mem0_env import check_mem0_environment, dumps_mem0_env_report


def fake_find_spec(name):
    if name == "mem0":
        return object()
    return None


def missing_find_spec(_name):
    return None


class Mem0EnvTests(unittest.TestCase):
    def test_platform_mode_ready_with_key_and_dependency(self):
        report = check_mem0_environment(
            env={"MEM0_API_KEY": "secret_value"},
            include_dotenv=False,
            python_version=(3, 10, 0),
            find_spec=fake_find_spec,
        )

        self.assertTrue(report["ready"])
        self.assertEqual(report["mode"], "platform")
        self.assertNotIn("secret_value", dumps_mem0_env_report(report))

    def test_platform_mode_missing_dependency_and_key_fails_safely(self):
        report = check_mem0_environment(
            env={"api_key": "scratch_secret"},
            include_dotenv=False,
            python_version=(3, 10, 0),
            find_spec=missing_find_spec,
        )
        details = json.dumps(report)

        self.assertFalse(report["ready"])
        self.assertIn("mem0_import", details)
        self.assertIn("MEM0_API_KEY", details)
        self.assertIn("api_key_placeholder", details)
        self.assertNotIn("scratch_secret", dumps_mem0_env_report(report))

    def test_dotenv_is_loaded_without_printing_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, ".env"), "w", encoding="utf-8") as handle:
                handle.write("MEM0_API_KEY=hidden_from_output\n")
            report = check_mem0_environment(
                root=directory,
                env={},
                include_dotenv=True,
                python_version=(3, 10, 0),
                find_spec=fake_find_spec,
            )

        rendered = dumps_mem0_env_report(report)
        self.assertTrue(report["ready"])
        self.assertIn("MEM0_API_KEY", rendered)
        self.assertNotIn("hidden_from_output", rendered)

    def test_oss_mode_ready_with_config_qdrant_and_detected_embedding_model(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"embedder": {"provider": "ollama", "model": "nomic-embed-text"}}, handle)
            config_path = handle.name
        try:
            report = check_mem0_environment(
                env={"MEM0_MODE": "oss", "MEM0_OSS_CONFIG_PATH": config_path},
                include_dotenv=False,
                python_version=(3, 10, 0),
                find_spec=fake_find_spec,
                qdrant_checker=lambda _host, _port: True,
                command_runner=lambda _cmd: (0, "NAME ID SIZE MODIFIED\nnomic-embed-text abc 1 GB today\n", ""),
            )
        finally:
            os.unlink(config_path)

        self.assertTrue(report["ready"])
        self.assertEqual(report["mode"], "oss")
        self.assertTrue(any(item["name"] == "embedding_model" and item["passed"] for item in report["checks"]))

    def test_oss_mode_fails_when_qdrant_is_unreachable(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"embedder": {"provider": "ollama", "model": "nomic-embed-text"}}, handle)
            config_path = handle.name
        try:
            report = check_mem0_environment(
                env={"MEM0_MODE": "oss", "MEM0_OSS_CONFIG_PATH": config_path},
                include_dotenv=False,
                python_version=(3, 10, 0),
                find_spec=fake_find_spec,
                qdrant_checker=lambda _host, _port: False,
                command_runner=lambda _cmd: (0, "NAME ID SIZE MODIFIED\nnomic-embed-text abc 1 GB today\n", ""),
            )
        finally:
            os.unlink(config_path)

        self.assertFalse(report["ready"])
        self.assertTrue(any(item["name"] == "qdrant" and not item["passed"] for item in report["checks"]))

    def test_cli_accepts_mem0_env_check_command(self):
        args = build_parser().parse_args(["mem0-env-check", "--json"])

        self.assertEqual(args.command, "mem0-env-check")
        self.assertTrue(args.json)


if __name__ == "__main__":
    unittest.main()
