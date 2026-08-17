"""Acceptance tests for the transport-free DSAR gateway core.

GatewayCore is the whole REST surface minus the socket: routing, auth,
body limits and error mapping are all testable here without ever binding
a port. The HTTP shell (a later commit) stays a thin adapter, so these
tests are the behavioural truth of the gateway.
"""

import argparse
import contextlib
import io
import json
import threading
import unittest
import urllib.error
import urllib.request

from cognitive_memory.cli import build_parser, run_dsar_serve
from cognitive_memory.dsar import DSARRequest, DSARService
from cognitive_memory.dsar_gateway import (
    DSARHTTPServer,
    GatewayConfig,
    GatewayCore,
)
from cognitive_memory.mcp_server import GovernedMemoryService, ServerConfig


class FakeDSARService:
    """Records calls and returns canned payloads; raises on demand."""

    def __init__(self):
        self.calls = []
        self.raise_on = {}

    def _maybe_raise(self, name):
        exc = self.raise_on.get(name)
        if exc is not None:
            raise exc

    def plan(self, request):
        self.calls.append(("plan", request))
        self._maybe_raise("plan")
        return {"request_id": request.request_id, "planned": True}

    def execute(self, request):
        self.calls.append(("execute", request))
        self._maybe_raise("execute")
        return {"request_id": request.request_id, "status": "executed"}

    def verify(self, request_id, tenant):
        self.calls.append(("verify", request_id, tenant))
        self._maybe_raise("verify")
        return {"request_id": request_id, "passed": True}

    def approve(self, request_id, tenant, reviewer):
        self.calls.append(("approve", request_id, tenant, reviewer))
        self._maybe_raise("approve")
        return {"request_id": request_id, "status": "approved"}

    def reject(self, request_id, tenant, reviewer, reason=""):
        self.calls.append(("reject", request_id, tenant, reviewer, reason))
        self._maybe_raise("reject")
        return {"request_id": request_id, "status": "rejected"}

    def status(self, request_id, tenant):
        self.calls.append(("status", request_id, tenant))
        self._maybe_raise("status")
        return {"request_id": request_id, "status": "executed"}

    def metrics(self):
        return {"requests": {"erasure": {"executed": 1}}}


def _core(token="", service=None):
    config = GatewayConfig(token=token)
    return GatewayCore(service or FakeDSARService(), config)


def _post(core, path, payload, headers=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    return core.handle_raw("POST", path, headers or {}, body)


def _plan_payload(**overrides):
    payload = {
        "request_id": "req-g1", "kind": "erasure", "tenant": "acme",
        "requester": "dpo@acme.example", "term": "alice",
    }
    payload.update(overrides)
    return payload


class GatewayConfigTests(unittest.TestCase):
    def test_defaults_bind_loopback_with_auth_off(self):
        config = GatewayConfig()
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8321)
        self.assertEqual(config.token, "")
        self.assertEqual(config.max_body_bytes, 1_000_000)
        self.assertIsInstance(config.server, ServerConfig)

    def test_from_dict_full_shape_with_nested_server(self):
        config = GatewayConfig.from_dict({
            "host": "127.0.0.1", "port": "9000", "token": "s3cret",
            "max_body_bytes": 4096,
            "server": {"dsar_signing_secret": "k", "default_profile": "default"},
            "vendor_noise": True,
        })
        self.assertEqual(config.port, 9000)
        self.assertEqual(config.token, "s3cret")
        self.assertEqual(config.server.dsar_signing_secret, "k")

    def test_from_dict_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            GatewayConfig.from_dict(["not", "a", "dict"])
        with self.assertRaises(ValueError):
            GatewayConfig.from_dict({"port": "not-a-port"})
        with self.assertRaises(ValueError):
            GatewayConfig.from_dict({"max_body_bytes": 0})


class GatewayAuthTests(unittest.TestCase):
    def test_without_configured_token_no_header_is_needed(self):
        status, payload = _post(_core(), "/dsar/plan", _plan_payload())
        self.assertEqual(status, 200)
        self.assertTrue(payload["planned"])

    def test_matching_token_passes(self):
        core = _core(token="s3cret")
        status, _ = _post(core, "/dsar/plan", _plan_payload(),
                          headers={"X-DSAR-Token": "s3cret"})
        self.assertEqual(status, 200)

    def test_missing_or_wrong_token_is_unauthorized(self):
        core = _core(token="s3cret")
        status, payload = _post(core, "/dsar/plan", _plan_payload())
        self.assertEqual(status, 401)
        self.assertEqual(payload, {"error": "unauthorized"})
        status, _ = _post(core, "/dsar/plan", _plan_payload(),
                          headers={"X-DSAR-Token": "wrong"})
        self.assertEqual(status, 401)
        _, metrics = core.handle_raw(
            "GET", "/metrics", {"X-DSAR-Token": "s3cret"}, b"")
        self.assertEqual(metrics["gateway"]["unauthorized_total"], 2)

    def test_health_is_exempt_from_auth(self):
        core = _core(token="s3cret")
        status, payload = core.handle_raw("GET", "/health", {}, b"")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})

    def test_token_header_lookup_is_case_insensitive(self):
        core = _core(token="s3cret")
        status, _ = _post(core, "/dsar/plan", _plan_payload(),
                          headers={"x-dsar-token": "s3cret"})
        self.assertEqual(status, 200)


class GatewayRoutingTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeDSARService()
        self.core = _core(service=self.service)

    def test_plan_builds_request_and_delegates(self):
        status, payload = _post(self.core, "/dsar/plan", _plan_payload())
        self.assertEqual(status, 200)
        name, request = self.service.calls[-1]
        self.assertEqual(name, "plan")
        self.assertIsInstance(request, DSARRequest)
        self.assertEqual(request.term, "alice")
        self.assertEqual(payload["request_id"], "req-g1")

    def test_execute_delegates(self):
        status, payload = _post(self.core, "/dsar/execute", _plan_payload())
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "executed")

    def test_verify_delegates_request_id_and_tenant(self):
        status, _ = _post(self.core, "/dsar/verify",
                          {"request_id": "req-g1", "tenant": "acme"})
        self.assertEqual(status, 200)
        self.assertEqual(self.service.calls[-1], ("verify", "req-g1", "acme"))
        status, _ = _post(self.core, "/dsar/verify", {"tenant": "acme"})
        self.assertEqual(status, 400)

    def test_approve_delegates_and_maps_key_error_to_404(self):
        status, _ = _post(self.core, "/dsar/approve", {
            "request_id": "req-g1", "tenant": "acme", "reviewer": "dpo",
        })
        self.assertEqual(status, 200)
        self.assertEqual(
            self.service.calls[-1], ("approve", "req-g1", "acme", "dpo"))
        self.service.raise_on["approve"] = KeyError("unknown request")
        status, payload = _post(self.core, "/dsar/approve", {
            "request_id": "nope", "tenant": "acme", "reviewer": "dpo",
        })
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "not_found"})

    def test_reject_carries_reason(self):
        status, _ = _post(self.core, "/dsar/reject", {
            "request_id": "req-g1", "tenant": "acme", "reviewer": "dpo",
            "reason": "no proof",
        })
        self.assertEqual(status, 200)
        self.assertEqual(
            self.service.calls[-1],
            ("reject", "req-g1", "acme", "dpo", "no proof"))

    def test_status_route_uses_query_tenant(self):
        status, payload = self.core.handle_raw(
            "GET", "/dsar/requests/req-g1?tenant=acme", {}, b"")
        self.assertEqual(status, 200)
        self.assertEqual(self.service.calls[-1], ("status", "req-g1", "acme"))
        self.assertEqual(payload["status"], "executed")
        status, _ = self.core.handle_raw(
            "GET", "/dsar/requests/req-g1", {}, b"")
        self.assertEqual(status, 400)
        self.service.raise_on["status"] = KeyError("unknown")
        status, _ = self.core.handle_raw(
            "GET", "/dsar/requests/nope?tenant=acme", {}, b"")
        self.assertEqual(status, 404)

    def test_unknown_path_and_wrong_method(self):
        status, payload = self.core.handle_raw("GET", "/nope", {}, b"")
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "not_found"})
        status, payload = self.core.handle_raw("GET", "/dsar/plan", {}, b"")
        self.assertEqual(status, 405)
        self.assertEqual(payload, {"error": "method_not_allowed"})
        status, _ = self.core.handle_raw("POST", "/health", {}, b"")
        self.assertEqual(status, 405)


class GatewayBodyTests(unittest.TestCase):
    def setUp(self):
        self.core = _core()

    def test_oversize_body_is_413(self):
        config = GatewayConfig(max_body_bytes=64)
        core = GatewayCore(FakeDSARService(), config)
        status, payload = core.handle_raw(
            "POST", "/dsar/plan", {}, b"x" * 65)
        self.assertEqual(status, 413)
        self.assertEqual(payload, {"error": "body_too_large"})

    def test_bad_json_is_400(self):
        status, payload = self.core.handle_raw(
            "POST", "/dsar/plan", {}, b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(payload, {"error": "invalid_json"})

    def test_non_object_payload_is_400(self):
        status, payload = self.core.handle_raw(
            "POST", "/dsar/plan", {}, json.dumps([1, 2]).encode("utf-8"))
        self.assertEqual(status, 400)
        self.assertEqual(payload, {"error": "payload_must_be_object"})

    def test_missing_reviewer_is_400(self):
        status, _ = _post(self.core, "/dsar/approve",
                          {"request_id": "req-g1", "tenant": "acme"})
        self.assertEqual(status, 400)


class GatewayErrorMappingTests(unittest.TestCase):
    def test_service_crash_is_opaque_500(self):
        service = FakeDSARService()
        service.raise_on["execute"] = RuntimeError("secret internal detail")
        core = _core(service=service)
        status, payload = _post(core, "/dsar/execute", _plan_payload())
        self.assertEqual(status, 500)
        self.assertEqual(payload, {"error": "internal_error"})
        self.assertNotIn("secret internal detail", json.dumps(payload))
        _, metrics = core.handle_raw("GET", "/metrics", {}, b"")
        self.assertEqual(metrics["gateway"]["errors_total"], 1)

    def test_validation_error_is_400_with_message(self):
        core = _core()
        status, payload = _post(
            core, "/dsar/plan", _plan_payload(requester=""))
        self.assertEqual(status, 400)
        self.assertIn("requester", payload["error"])


class GatewayMetricsAndIntegrationTests(unittest.TestCase):
    def test_metrics_merge_gateway_and_service(self):
        core = _core()
        core.handle_raw("GET", "/health", {}, b"")
        status, payload = core.handle_raw("GET", "/metrics", {}, b"")
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["service"], {"requests": {"erasure": {"executed": 1}}})
        self.assertGreaterEqual(payload["gateway"]["requests_total"], 2)
        self.assertEqual(payload["gateway"]["errors_total"], 0)

    def test_real_service_plan_execute_verify_roundtrip(self):
        svc = GovernedMemoryService()
        mem = svc.memory_for("acme")
        mem.remember("alice likes tea", subject="alice", relation="likes",
                     object="tea", tenant="acme", source="crm")
        core = GatewayCore(DSARService(svc), GatewayConfig())
        status, plan = core.dispatch("POST", "/dsar/plan", _plan_payload())
        self.assertEqual(status, 200)
        self.assertEqual(plan["matched_count"], 1)
        status, executed = core.dispatch(
            "POST", "/dsar/execute", _plan_payload())
        self.assertEqual(status, 200)
        self.assertEqual(
            executed["certificate"]["details"]["targeted_count"], 1)
        status, verified = core.dispatch(
            "POST", "/dsar/verify",
            {"request_id": "req-g1", "tenant": "acme"})
        self.assertEqual(status, 200)
        self.assertTrue(verified["passed"])


def _seeded_dsar_service():
    svc = GovernedMemoryService()
    mem = svc.memory_for("acme")
    mem.remember("alice likes tea", subject="alice", relation="likes",
                 object="tea", tenant="acme", source="crm")
    return DSARService(svc)


class HTTPShellTests(unittest.TestCase):
    def _serve(self, config=None, service=None):
        server = DSARHTTPServer(
            config or GatewayConfig(port=0),
            service or _seeded_dsar_service(),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d" % server.server_address[1]

    def _get(self, url, headers=None):
        request = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def _post(self, url, payload, headers=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers or {})
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_and_plan_over_real_socket(self):
        base = self._serve()
        status, payload = self._get(base + "/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})
        status, payload = self._post(base + "/dsar/plan", _plan_payload())
        self.assertEqual(status, 200)
        self.assertEqual(payload["matched_count"], 1)

    def test_oversize_body_is_http_413(self):
        base = self._serve(config=GatewayConfig(port=0, max_body_bytes=64))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._post(base + "/dsar/plan", b"x" * 65)
        self.assertEqual(caught.exception.code, 413)

    def test_bad_json_is_400_with_json_content_type(self):
        base = self._serve()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._post(base + "/dsar/plan", b"{not json")
        self.assertEqual(caught.exception.code, 400)
        self.assertIn(
            "application/json", caught.exception.headers.get("Content-Type", ""))
        body = json.loads(caught.exception.read().decode("utf-8"))
        self.assertEqual(body, {"error": "invalid_json"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._get(base + "/nope")
        self.assertEqual(caught.exception.code, 404)

    def test_token_auth_over_http(self):
        base = self._serve(config=GatewayConfig(port=0, token="s3cret"))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._post(base + "/dsar/plan", _plan_payload())
        self.assertEqual(caught.exception.code, 401)
        status, _ = self._post(base + "/dsar/plan", _plan_payload(),
                               headers={"X-DSAR-Token": "s3cret"})
        self.assertEqual(status, 200)


class DsarServeCliTests(unittest.TestCase):
    def test_bad_config_returns_2_without_serving(self):
        called = []
        args = argparse.Namespace(
            config="/nonexistent/dsar-gateway.json", host="", port=None,
            token="")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = run_dsar_serve(args, serve_fn=lambda cfg: called.append(cfg))
        self.assertEqual(code, 2)
        self.assertEqual(called, [])

    def test_banner_hides_token_and_overrides_apply(self):
        served = []
        args = argparse.Namespace(
            config="", host="", port=9999, token="s3cret")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = run_dsar_serve(args, serve_fn=lambda cfg: served.append(cfg))
        self.assertEqual(code, 0)
        self.assertEqual(len(served), 1)
        self.assertEqual(served[0].port, 9999)
        self.assertEqual(served[0].token, "s3cret")
        banner = stderr.getvalue()
        self.assertIn("9999", banner)
        self.assertNotIn("s3cret", banner)
        parser = build_parser()
        parsed = parser.parse_args(["dsar-serve", "--port", "0"])
        self.assertIs(parsed.func, run_dsar_serve)


if __name__ == "__main__":
    unittest.main()
