#!/usr/bin/env python3
"""Runnable ServiceNow integration demo: request, action, evidence, ticket.

This script walks the exact loop an ITSM integration performs, using the
same example files a ServiceNow architect copies out of this folder. It is
the executable half of ``docs/servicenow_integration.md``: if the doc and
the code ever disagree, this run fails.

Two modes, both stdlib only:

* default (in process): the push path through :class:`GatewayCore` (plan,
  execute, verify, status, ticket payload) followed by the pull path
  through :class:`DSARListener` reading a temporary drop folder.
* ``--serve``: the same push path over a real socket, against a
  :class:`DSARHTTPServer` bound on an ephemeral port, driven with urllib.

Every step checks its own result and the process exits non-zero on the
first violation, so the demo is a test an integrator can run rather than a
transcript they have to read carefully.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request

# Bootstrap: make the repository's src/ importable so the demo runs both
# with PYTHONPATH=src and from a bare checkout without any environment.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_SRC = os.path.join(_REPO, "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from cognitive_memory.dsar_gateway import (  # noqa: E402  (after bootstrap)
    DSARHTTPServer,
    GatewayConfig,
    GatewayCore,
)
from cognitive_memory.dsar_listener import (  # noqa: E402  (after bootstrap)
    DSARListener,
    FileDropSource,
)
from cognitive_memory.mcp_server import GovernedMemoryService  # noqa: E402

TENANT = "acme"

# Fictional seed data. Two records mention the subject of the request and
# one does not, so an erasure that removed everything would be as visible
# as an erasure that removed nothing.
SEED = (
    ("alice likes tea", "alice", "likes", "tea", "crm"),
    ("alice works in berlin", "alice", "works_in", "berlin", "hr"),
    ("bob likes coffee", "bob", "likes", "coffee", "crm"),
)


class DemoFailure(Exception):
    """A step produced a result the demo asserts it must never produce."""


def _require(condition, message):
    """Assert a demo expectation.

    A plain ``assert`` would be stripped under ``python3 -O``, and a demo
    whose self-checks vanish under an interpreter flag is a demo that can
    report success for a broken integration.
    """

    if not condition:
        raise DemoFailure(message)


# -- fixtures ---------------------------------------------------------------


def _load_json(name):
    with open(os.path.join(_HERE, name), encoding="utf-8") as handle:
        return json.load(handle)


def _config():
    """Load the shipped gateway config, signing settings included."""

    return GatewayConfig.from_dict(_load_json("gateway_config.json"))


def _seeded_service(config):
    """Build a governed memory service for the tenant and fill it.

    ``config.server`` carries the DSAR signing secret and key id, so the
    service built from it hands out signed certificates without the demo
    ever constructing a signer of its own.
    """

    service = GovernedMemoryService(config.server)
    for text, subject, relation, obj, source in SEED:
        stored = service.remember({
            "text": text, "subject": subject, "relation": relation,
            "object": obj, "tenant": TENANT, "source": source,
        })
        _require(stored.get("stored"), "seed record was not stored: %r" % (text,))
        _require(
            not stored.get("quarantined"),
            "seed record was quarantined: %r (%s)"
            % (text, stored.get("quarantine_reason", "")),
        )
    return service


def _dispatch(core, method, path, payload=None, query=None):
    """Call one gateway route in process and fail loudly on a non-200."""

    status, result = core.dispatch(method, path, payload, query)
    _require(
        status == 200,
        "%s %s answered %d: %s" % (method, path, status, json.dumps(result, sort_keys=True)),
    )
    return result


# -- push path, in process --------------------------------------------------


def run_in_process():
    """Plan, execute, verify, poll status and build the ticket attachment."""

    config = _config()
    service = _seeded_service(config)
    # The deployment's single DSAR service: one replay registry, and the
    # signer configured in gateway_config.json.
    dsar = service._dsar_service()
    core = GatewayCore(dsar, config)

    plan_request = _load_json("plan_request.json")
    request_id = str(plan_request["request_id"])

    plan = _dispatch(core, "POST", "/dsar/plan", plan_request)
    print("plan: matched=%d" % plan["matched_count"])
    _require(plan["matched_count"] >= 1, "plan matched no record to erase")
    _require(not plan["would_hold"], "plan reports a hold: %s" % plan["would_hold"])

    executed = _dispatch(core, "POST", "/dsar/execute", _load_json("execute_request.json"))
    print("execute: status=%s removed=%s" % (executed["status"], executed.get("removed")))
    _require(executed["status"] == "executed", "execute status is %r" % executed["status"])
    _require(not executed["replayed"], "the first execution reported a replay")
    _require(executed.get("certificate"), "execute issued no erasure certificate")

    verify = _dispatch(core, "POST", "/dsar/verify", _load_json("verify_request.json"))
    print("verify: passed=%s" % verify["passed"])
    _require(
        verify["passed"],
        "verify did not pass: %s" % json.dumps(verify.get("checks"), sort_keys=True),
    )

    # Wire form: GET /dsar/requests/<request_id>?tenant=acme
    state = _dispatch(
        core, "GET", "/dsar/requests/" + request_id, None, {"tenant": [TENANT]})
    print("status: %s" % state["status"])
    _require(state["status"] == "executed", "status is %r" % state["status"])
    _require(state["last_verify_passed"], "status reports the last verify as failed")

    payload = dsar.ticket_payload(request_id, TENANT)
    try:
        json.dumps(payload, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise DemoFailure("ticket payload is not JSON serializable: %s" % exc)
    signature = payload.get("signed_certificate", {}).get("signature", {})
    head_hash = str(payload["audit"]["head_hash"])
    print(
        "ticket_payload: signed key_id=%s audit_head=%s"
        % (signature.get("key_id", ""), head_hash[:12])
    )
    _require(
        signature.get("key_id") == config.server.dsar_signing_key_id,
        "ticket payload carries no signature under the configured key id",
    )
    _require(payload["audit"]["verified"], "the audit chain did not verify")


# -- pull path, drop folder -------------------------------------------------


def run_listener():
    """Drive the listener over an email and two copies of one Kafka record.

    A fresh service stack on purpose: the push section above already
    executed this request id, and replaying it here would hide the
    executions. Delivered against untouched memory, the mail and the Kafka
    record each execute, and the redelivery of the same ``sys_id`` replays
    the recorded outcome instead of erasing a second time. That is the
    at-least-once story the doc claims, shown rather than asserted.
    """

    config = _config()
    dsar = _seeded_service(config)._dsar_service()

    drop = tempfile.mkdtemp(prefix="dsar-drop-")
    try:
        for source, name in (
            ("email_example.eml", "email_example.eml"),
            ("kafka_message.json", "kafka_message.json"),
            ("kafka_message.json", "kafka_message_redelivery.json"),
        ):
            shutil.copyfile(os.path.join(_HERE, source), os.path.join(drop, name))
        results = DSARListener(dsar, [FileDropSource(drop)]).run(max_iterations=1)
    finally:
        shutil.rmtree(drop, ignore_errors=True)

    for result in results:
        _require(
            "error" not in result,
            "listener message %r failed: %s" % (result.get("name"), result.get("error")),
        )
        print(
            "listener: %s status=%s replayed=%s verify_passed=%s"
            % (result["name"], result["status"], result["replayed"],
               result["verify_passed"])
        )
        _require(
            result["status"] == "executed",
            "listener message %r ended as %r" % (result["name"], result["status"]),
        )
        _require(
            result["verify_passed"],
            "listener message %r did not verify" % (result["name"],),
        )

    _require(len(results) == 3, "expected 3 listener results, got %d" % len(results))
    replayed = [bool(result["replayed"]) for result in results]
    _require(
        replayed == [False, False, True],
        "expected the redelivered record to replay, got %r" % (replayed,),
    )


# -- push path, real socket -------------------------------------------------


def _http(url, payload=None, token=""):
    """One JSON request against the running gateway."""

    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-DSAR-Token"] = token
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        _require(response.status == 200, "%s answered %d" % (url, response.status))
        return json.loads(response.read().decode("utf-8"))


def run_over_http():
    """Run the push path against a real socket, then shut the server down."""

    config = _config()
    # Port 0: the operating system picks a free port, so the demo never
    # collides with a gateway that is already running on the configured one.
    config.port = 0
    server = DSARHTTPServer(config, _seeded_service(config)._dsar_service())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://%s:%d" % (config.host, server.server_address[1])
    print("serve: %s" % base)
    try:
        health = _http(base + "/health")
        print("health: %s" % health["status"])
        _require(health["status"] == "ok", "health reported %r" % health["status"])

        plan = _http(base + "/dsar/plan", _load_json("plan_request.json"), config.token)
        print("plan: matched=%d" % plan["matched_count"])
        _require(plan["matched_count"] >= 1, "plan matched no record to erase")

        executed = _http(
            base + "/dsar/execute", _load_json("execute_request.json"), config.token)
        print("execute: status=%s removed=%s"
              % (executed["status"], executed.get("removed")))
        _require(executed["status"] == "executed", "execute status is %r" % executed["status"])
        _require(not executed["replayed"], "the first execution reported a replay")

        verify = _http(
            base + "/dsar/verify", _load_json("verify_request.json"), config.token)
        print("verify: passed=%s" % verify["passed"])
        _require(
            verify["passed"],
            "verify did not pass: %s" % json.dumps(verify.get("checks"), sort_keys=True),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main(argv):
    args = list(argv[1:])
    unknown = [arg for arg in args if arg != "--serve"]
    if unknown:
        print("usage: demo.py [--serve]", file=sys.stderr)
        return 2
    try:
        if "--serve" in args:
            run_over_http()
            print("demo (http): OK")
        else:
            run_in_process()
            run_listener()
            print("demo: OK")
    except DemoFailure as exc:
        print("demo failed: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
