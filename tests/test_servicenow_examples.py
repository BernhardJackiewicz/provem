"""Validation tests that keep the ServiceNow examples and docs honest.

Shipped examples rot silently: an API change that nobody re-runs the
examples against turns the integration story into fiction. These tests
load the exact files a ServiceNow architect would copy and push them
through the real parsers, config loaders and the demo script, so a
breaking change fails here before it reaches a prospect.
"""

import json
import os
import re
import subprocess
import sys
import unittest

from cognitive_memory.dsar import DSARRequest
from cognitive_memory.dsar_gateway import GatewayConfig
from cognitive_memory.dsar_listener import (
    default_transform,
    parse_email_request,
    parse_kafka_message,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(REPO, "examples", "servicenow")
DOC = os.path.join(REPO, "docs", "servicenow_integration.md")


def _load(name):
    with open(os.path.join(EXAMPLES, name), "rb") as handle:
        return handle.read()


class RequestExampleTests(unittest.TestCase):
    def test_plan_and_execute_examples_are_valid_requests(self):
        for name in ("plan_request.json", "execute_request.json"):
            payload = json.loads(_load(name))
            request = DSARRequest.from_dict(payload)
            self.assertEqual(request.kind, "erasure")
            self.assertTrue(request.ticket)
            self.assertTrue(request.requester)

    def test_verify_example_addresses_a_request(self):
        payload = json.loads(_load("verify_request.json"))
        self.assertTrue(str(payload["request_id"]).strip())
        self.assertTrue(str(payload["tenant"]).strip())

    def test_email_example_parses_into_a_valid_request(self):
        payload = parse_email_request(_load("email_example.eml"))
        request = DSARRequest.from_dict(payload)
        self.assertEqual(request.kind, "erasure")
        self.assertTrue(request.ticket)

    def test_kafka_example_is_servicenow_shaped(self):
        raw = json.loads(_load("kafka_message.json"))
        self.assertIn("sys_id", raw)
        self.assertIn("number", raw)
        payload = default_transform(parse_kafka_message(_load("kafka_message.json")))
        request = DSARRequest.from_dict(payload)
        self.assertEqual(request.request_id, str(raw["sys_id"]))
        self.assertEqual(request.ticket, str(raw["number"]))

    def test_gateway_config_example_loads_with_signing(self):
        config = GatewayConfig.from_dict(json.loads(_load("gateway_config.json")))
        self.assertEqual(config.host, "127.0.0.1")
        self.assertTrue(config.server.dsar_signing_secret)


class DemoAndDocTests(unittest.TestCase):
    def _run_demo(self, *args):
        env = dict(os.environ, PYTHONPATH=os.path.join(REPO, "src"))
        return subprocess.run(
            [sys.executable, os.path.join(EXAMPLES, "demo.py"), *args],
            capture_output=True, text=True, timeout=120, env=env, cwd=REPO,
        )

    def test_demo_runs_in_process_and_over_socket(self):
        result = self._run_demo()
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("verify", result.stdout.lower())
        served = self._run_demo("--serve")
        self.assertEqual(served.returncode, 0, served.stderr or served.stdout)

    def test_doc_covers_the_story_and_stays_clean(self):
        with open(DOC, encoding="utf-8") as handle:
            text = handle.read()
        for needle in ("Outbound REST", "Inbound", "Kafka", "/dsar/plan",
                       "/dsar/approve", "at-least-once", "X-DSAR-Token"):
            self.assertIn(needle, text)
        self.assertIsNone(re.search("[–—]", text))
        self.assertNotIn("Claude", text)
        self.assertNotIn("Anthropic", text)


if __name__ == "__main__":
    unittest.main()
