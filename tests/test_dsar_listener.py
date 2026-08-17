"""Acceptance tests for the DSAR pull listener (parsers, sources, loop).

The listener is the zero-ServiceNow-side-development path: ServiceNow's
native outbound email or a Kafka topic delivers the request, the listener
parses it into the one canonical DSARRequest shape and drives the full
plan, execute, verify loop per message. Reference quality on purpose:
at-least-once, serial, error-isolated, no retry machinery.
"""

import json
import os
import shutil
import tempfile
import unittest

from cognitive_memory import dsar
from cognitive_memory.dsar import DSARService
from cognitive_memory.dsar_listener import (
    KINDS,
    DSARListener,
    DSARRequest,
    FileDropSource,
    KafkaSource,
    default_transform,
    parse_email_request,
    parse_kafka_message,
)
from cognitive_memory.mcp_server import GovernedMemoryService


EMAIL_SIMPLE = b"""From: dpo@acme.example
To: dsar@provem.example
Subject: DSAR erasure RITM0010001

tenant: acme
term: alice
"""

EMAIL_MULTIPART = b"""From: dpo@acme.example
Subject: DSAR consent_withdrawal RITM0010002
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="XYZ"

--XYZ
Content-Type: text/html

<p>ignore me</p>
--XYZ
Content-Type: text/plain

tenant: acme
term: alice
purpose: marketing
requester: privacy-team@acme.example
--XYZ--
"""


class EmailParserTests(unittest.TestCase):
    def test_subject_and_body_fields_are_extracted(self):
        payload = parse_email_request(EMAIL_SIMPLE)
        self.assertEqual(payload["kind"], "erasure")
        self.assertEqual(payload["ticket"], "RITM0010001")
        self.assertEqual(payload["tenant"], "acme")
        self.assertEqual(payload["term"], "alice")

    def test_multipart_uses_text_plain_and_body_requester_wins(self):
        payload = parse_email_request(EMAIL_MULTIPART)
        self.assertEqual(payload["kind"], "consent_withdrawal")
        self.assertEqual(payload["purpose"], "marketing")
        self.assertEqual(payload["requester"], "privacy-team@acme.example")
        self.assertNotIn("ignore me", json.dumps(payload))

    def test_from_header_is_requester_fallback(self):
        payload = parse_email_request(EMAIL_SIMPLE)
        self.assertEqual(payload["requester"], "dpo@acme.example")

    def test_subject_without_dsar_form_is_rejected(self):
        bad = EMAIL_SIMPLE.replace(
            b"Subject: DSAR erasure RITM0010001", b"Subject: hello world")
        with self.assertRaises(ValueError):
            parse_email_request(bad)

    def test_binary_garbage_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_email_request(b"\x00\xff\xfe\x00 not an email at all")

    def test_ticket_is_optional_in_subject(self):
        raw = EMAIL_SIMPLE.replace(
            b"Subject: DSAR erasure RITM0010001", b"Subject: DSAR access")
        raw = raw.replace(b"term: alice", b"subject: alice")
        payload = parse_email_request(raw)
        self.assertEqual(payload["kind"], "access")
        self.assertEqual(payload["subject"], "alice")
        self.assertNotIn("ticket", payload)


class KafkaParserAndTransformTests(unittest.TestCase):
    def test_bytes_and_str_json_parse(self):
        message = {"kind": "erasure", "tenant": "acme"}
        self.assertEqual(
            parse_kafka_message(json.dumps(message).encode("utf-8")), message)
        self.assertEqual(parse_kafka_message(json.dumps(message)), message)

    def test_garbage_and_non_object_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_kafka_message(b"{not json")
        with self.assertRaises(ValueError):
            parse_kafka_message(json.dumps([1, 2]))

    def test_default_transform_maps_servicenow_shape(self):
        payload = {
            "sys_id": "abc123", "number": "RITM0010003",
            "kind": "erasure", "tenant": "acme",
            "requester": "dpo@acme.example", "term": "alice",
        }
        out = default_transform(payload)
        self.assertEqual(out["request_id"], "abc123")
        self.assertEqual(out["ticket"], "RITM0010003")
        explicit = default_transform(dict(payload, request_id="keep-me",
                                          ticket="KEEP-1"))
        self.assertEqual(explicit["request_id"], "keep-me")
        self.assertEqual(explicit["ticket"], "KEEP-1")
        self.assertEqual(payload.get("request_id"), None)

    def test_reexports_match_dsar_module(self):
        self.assertIs(KINDS, dsar.KINDS)
        self.assertIs(DSARRequest, dsar.DSARRequest)


class FileDropSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def _write(self, name, content=b"{}"):
        with open(os.path.join(self.tmp, name), "wb") as handle:
            handle.write(content)

    def test_polls_sorted_json_and_eml_only(self):
        self._write("b.json", b'{"b": 1}')
        self._write("a.json", b'{"a": 1}')
        self._write("notes.txt", b"leave me alone")
        self._write("c.eml", EMAIL_SIMPLE)
        items = FileDropSource(self.tmp).poll()
        self.assertEqual([name for name, _ in items],
                         ["a.json", "b.json", "c.eml"])
        self.assertEqual(items[0][1], b'{"a": 1}')
        self.assertTrue(
            os.path.exists(os.path.join(self.tmp, "notes.txt")))

    def test_consumed_once_via_processed_dir(self):
        self._write("a.json")
        source = FileDropSource(self.tmp)
        self.assertEqual(len(source.poll()), 1)
        self.assertEqual(source.poll(), [])
        self.assertTrue(
            os.path.exists(os.path.join(self.tmp, "processed", "a.json")))

    def test_missing_directory_is_rejected(self):
        with self.assertRaises(ValueError):
            FileDropSource(os.path.join(self.tmp, "does-not-exist"))


class _Record:
    def __init__(self, value, topic="dsar-requests"):
        self.value = value
        self.topic = topic


class KafkaSourceTests(unittest.TestCase):
    def test_dict_of_lists_consumer(self):
        class Consumer:
            def poll(self):
                return {"tp0": [_Record(b'{"kind": "erasure"}')],
                        "tp1": [_Record(b'{"kind": "access"}')]}

        items = KafkaSource(Consumer()).poll()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0][0], "dsar-requests")
        self.assertIsInstance(items[0][1], bytes)

    def test_iterable_consumer_with_str_values(self):
        class Consumer:
            def poll(self):
                return ['{"kind": "erasure"}']

        items = KafkaSource(Consumer()).poll()
        self.assertEqual(items, [("kafka", b'{"kind": "erasure"}')])


def _seeded_dsar():
    svc = GovernedMemoryService()
    mem = svc.memory_for("acme")
    mem.remember("alice likes tea", subject="alice", relation="likes",
                 object="tea", tenant="acme", source="crm")
    return DSARService(svc), mem


class ListenerLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def _drop(self, name, content):
        with open(os.path.join(self.tmp, name), "wb") as handle:
            handle.write(content)

    def _json_request(self, request_id="req-l1"):
        return json.dumps({
            "request_id": request_id, "kind": "erasure", "tenant": "acme",
            "requester": "dpo@acme.example", "term": "alice",
        }).encode("utf-8")

    def test_end_to_end_eml_drop_folder(self):
        service, mem = _seeded_dsar()
        self._drop("request.eml", EMAIL_SIMPLE)
        listener = DSARListener(service, [FileDropSource(self.tmp)],
                                sleep_fn=lambda seconds: None)
        results = listener.run(max_iterations=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "executed")
        self.assertTrue(results[0]["verify_passed"])
        self.assertEqual(
            [r.subject for r in mem.backend.all_records()], [])

    def test_json_redelivery_is_replayed(self):
        service, _ = _seeded_dsar()
        listener = DSARListener(service, [FileDropSource(self.tmp)],
                                sleep_fn=lambda seconds: None)
        self._drop("first.json", self._json_request())
        first = listener.run(max_iterations=1)
        self.assertFalse(first[0]["replayed"])
        self._drop("second.json", self._json_request())
        second = listener.run(max_iterations=1)
        self.assertTrue(second[0]["replayed"])

    def test_garbage_message_is_isolated(self):
        service, _ = _seeded_dsar()
        self._drop("a-garbage.json", b"{not json")
        self._drop("b-valid.json", self._json_request("req-l2"))
        listener = DSARListener(service, [FileDropSource(self.tmp)],
                                sleep_fn=lambda seconds: None)
        results = listener.run(max_iterations=1)
        self.assertEqual(len(results), 2)
        self.assertIn("error", results[0])
        self.assertEqual(results[1]["status"], "executed")

    def test_service_failure_and_poll_failure_are_isolated(self):
        class ExplodingService:
            def plan(self, request):
                return {}

            def execute(self, request):
                raise RuntimeError("backend down")

        class ExplodingSource:
            def poll(self):
                raise RuntimeError("broker down")

        self._drop("only.json", self._json_request("req-l3"))
        listener = DSARListener(
            ExplodingService(),
            [ExplodingSource(), FileDropSource(self.tmp)],
            sleep_fn=lambda seconds: None,
        )
        results = listener.run(max_iterations=1)
        self.assertEqual(len(results), 2)
        self.assertIn("error", results[0])
        self.assertIn("backend down", results[1]["error"])

    def test_max_iterations_and_injected_sleep(self):
        sleeps = []
        service, _ = _seeded_dsar()
        listener = DSARListener(service, [FileDropSource(self.tmp)],
                                sleep_fn=sleeps.append)
        results = listener.run(max_iterations=3, interval=0.5)
        self.assertEqual(results, [])
        self.assertEqual(sleeps, [0.5, 0.5])

    def test_custom_transform_is_applied(self):
        service, _ = _seeded_dsar()

        def transform(payload):
            mapped = default_transform(payload)
            mapped["term"] = mapped.pop("u_betroffener", "")
            return mapped

        self._drop("custom.json", json.dumps({
            "request_id": "req-l4", "kind": "erasure", "tenant": "acme",
            "requester": "dpo@acme.example", "u_betroffener": "alice",
        }).encode("utf-8"))
        listener = DSARListener(service, [FileDropSource(self.tmp)],
                                transform=transform,
                                sleep_fn=lambda seconds: None)
        results = listener.run(max_iterations=1)
        self.assertEqual(results[0]["status"], "executed")
        self.assertTrue(results[0]["verify_passed"])


if __name__ == "__main__":
    unittest.main()
