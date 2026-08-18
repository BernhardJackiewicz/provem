#!/usr/bin/env python3
"""Run the local DSAR stack for the ServiceNow E2E validation.

Starts the DSAR HTTP gateway on the configured port with a per-run
API token, seeds the "acme" tenant with the fictional fixtures, and
writes the runtime facts (token, port, base URL) into
``.runtime/state.json`` so the other QA scripts and the Workflow Studio
flow configuration can pick them up. The token is generated per run and
never committed; the signing secret in ``gateway_config.json`` is a
documented placeholder that only signs test certificates.

Blocking by design (serve_forever); run it in the background and stop
it with SIGTERM/Ctrl-C when the E2E session is over.
"""

import json
import os
import secrets
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_SRC = os.path.join(_REPO, "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from cognitive_memory.dsar_gateway import DSARHTTPServer, GatewayConfig  # noqa: E402
from cognitive_memory.mcp_server import GovernedMemoryService  # noqa: E402

SEED = (
    ("alice likes tea", "alice", "likes", "tea", "crm"),
    ("alice works in berlin", "alice", "works_in", "berlin", "hr"),
    ("bob likes coffee", "bob", "likes", "coffee", "crm"),
)

TENANT = "acme"


def main() -> int:
    with open(os.path.join(_HERE, "gateway_config.json"), encoding="utf-8") as handle:
        config = GatewayConfig.from_dict(json.load(handle))

    # A fresh shared secret per run: the gateway spends part of its life
    # behind a public tunnel URL, so it never runs unauthenticated here.
    config.token = os.environ.get("PROVEM_DSAR_TOKEN") or secrets.token_hex(16)

    service = GovernedMemoryService(config.server)
    for text, subject, relation, obj, source in SEED:
        stored = service.remember({
            "text": text, "subject": subject, "relation": relation,
            "object": obj, "tenant": TENANT, "source": source,
        })
        if not stored.get("stored") or stored.get("quarantined"):
            print("seed failed for %r: %s" % (text, stored), file=sys.stderr)
            return 1

    server = DSARHTTPServer(config, service._dsar_service())
    host, port = server.server_address[0], server.server_address[1]

    runtime_dir = os.path.join(_HERE, ".runtime")
    os.makedirs(runtime_dir, exist_ok=True)
    state = {
        "base_url": "http://%s:%d" % (host, port),
        "token": config.token,
        "tenant": TENANT,
        "pid": os.getpid(),
    }
    with open(os.path.join(runtime_dir, "state.json"), "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)

    print("qa gateway ready on http://%s:%d (token auth on, seeded tenant %r)"
          % (host, port, TENANT), file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
