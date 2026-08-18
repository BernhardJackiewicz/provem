# ServiceNow integration: DSAR requests against governed memory

A data subject request arrives in the ticket system, an agent memory has to
act on it, and somebody has to be able to prove afterwards that it acted.
This document describes how Provem closes that loop with ServiceNow, and
how to run the whole thing locally before writing a single line of workflow
configuration.

Nothing here is ServiceNow specific at the protocol level. The gateway
speaks plain JSON over HTTP and the listener reads mail files or topic
records, so Jira Service Management, Freshservice or an in-house tool
integrate the same way. ServiceNow is the reference because it is the
platform this integration was designed against, and because it can drive
both directions (push and pull) without custom scripting.

## Validated against a real instance

Both directions were run end to end against a real ServiceNow Personal
Developer Instance (Australia release), not just against the local tests:

- **Pull.** A native ServiceNow notification on an incident produced a
  real DSAR mail; fed to `DSARListener`, it erased the two matched
  records, passed verification and issued a certificate, and a redelivery
  of the same mail replayed instead of erasing twice. One configuration
  point only the real instance surfaces: set the notification's content
  type to **text/plain**. ServiceNow notifications default to text/html,
  and an HTML-only mail has no text/plain part, which the parser reads as
  an empty body.
- **Push.** ServiceNow itself called the gateway (via `sn_ws.RESTMessageV2`,
  the same outbound mechanism the Workflow Studio REST step uses),
  running plan, execute and verify with the `X-DSAR-Token` header over
  TLS. The signed certificate came back to ServiceNow, the same request
  id executed exactly once (the retry replayed), and a call without the
  token was rejected with 401.

The repeatable harness and the full run notes live in
[`qa/servicenow_e2e/`](../qa/servicenow_e2e/) (`RESULTS.md`,
`run_stack.py`, `sn_push_probe.js`). What is not yet GUI-tested: the
Workflow Studio flow itself (Record-Trigger, the Ask-for-Approval step,
the work-note write-back); the outbound REST call it depends on is
proven, the approval gating and ticket write-back are documented but not
click-tested.

## Overview

The loop, end to end:

1. **Request.** A ticket is raised in ServiceNow (a request item, a case,
   an incident). It carries the subject term, the tenant and the acting
   requester.
2. **Plan.** Provem answers what the request *would* touch: matched
   records, their sources, the registered derivative stores, and whether a
   strict revocation profile would put the request on hold. Nothing is
   changed. This is the report a data protection officer approves.
3. **Action.** The request is executed exactly once per `request_id`: an
   erasure removes the records and issues a tamper evident certificate, a
   consent withdrawal closes the read gate, an access request exports the
   subject's package.
4. **Confirmation.** A verify probe re-checks the outcome against current
   state (residual scan, governed recall, tombstone registry, audit chain,
   backend sweep) and returns the evidence, not an acknowledgement.
5. **Certificate to the ticket.** `ticket_payload` bundles the request,
   its status, the signed certificate, the fresh verify report and the
   audit head hash into one attachable artifact. That is what gets
   attached to the ServiceNow ticket when it is closed.

### Run it locally first

```bash
# the full loop in process, plus the pull listener
PYTHONPATH=src python3 examples/servicenow/demo.py

# the same push path over a real socket on an ephemeral port
PYTHONPATH=src python3 examples/servicenow/demo.py --serve

# the gateway itself, on the configured host and port
PYTHONPATH=src python3 -m cognitive_memory dsar-serve \
  --config examples/servicenow/gateway_config.json
```

The demo exits non-zero if any step returns something other than what this
document promises, so it doubles as the integration's smoke test. Every
payload it sends is a file in `examples/servicenow/`, ready to be copied
into the body of an Outbound REST step.

## Push mode

Push mode is ServiceNow calling Provem. It is built entirely in Workflow
Studio (called Flow Designer before the Australia release), with no
scripting on the platform side and nothing to install on the Provem side.

**The flow.** A Record-Trigger on the request item table fires when a DSAR
ticket reaches its approved state. The flow then runs, in order:

1. An **Outbound REST step** against `POST /dsar/plan`.
2. An approval step. The plan report is the decision basis: the approver
   sees the matched count, the affected sources and the derivative stores
   before anything is erased. This step is the whole reason plan and
   execute are two calls instead of one.
3. An **Outbound REST step** against `POST /dsar/execute`.
4. An **Outbound REST step** against `POST /dsar/verify`.
5. A step that attaches the evidence to the ticket, either as a work note
   or as a JSON attachment.

**The idempotency anchor.** Send the request item's `sys_id` as
`request_id`. It is stable across retries, across flow restarts and across
a Provem restart, and it is the key that makes `execute` run exactly once:
a second call with the same `request_id` replays the recorded outcome
instead of erasing twice. The human readable `number` (for example
`RITM0010001`) goes into `ticket`, so the audit trail and the certificate
name the ticket a human can open. The example payloads in
`examples/servicenow/plan_request.json` and `execute_request.json` are
deliberately identical, because plan and execute describe the same request.

```bash
curl -sS -X POST http://127.0.0.1:8321/dsar/plan \
  -H 'Content-Type: application/json' \
  -H 'X-DSAR-Token: change-me-gateway-token' \
  --data @examples/servicenow/plan_request.json
```

The `X-DSAR-Token` header is only needed when the gateway config sets a
non-empty `token`. The shipped example leaves it empty, so the header is
shown once here and omitted in the calls below. It is a different secret
from `dsar_signing_secret`: the token guards the API, the signing secret
signs certificates.

```json
{
  "request_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
  "kind": "erasure",
  "tenant": "acme",
  "requester": "privacy-team@acme.example",
  "term": "alice",
  "ticket": "RITM0010001"
}
```

The plan answers with the read-only effect report:

```json
{
  "request_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
  "kind": "erasure",
  "tenant": "acme",
  "term": "alice",
  "matched_count": 2,
  "matched_ids": ["rec_1", "rec_2"],
  "sources": ["crm", "hr"],
  "derivative_stores": [],
  "would_hold": ""
}
```

Execute takes the same body:

```bash
curl -sS -X POST http://127.0.0.1:8321/dsar/execute \
  -H 'Content-Type: application/json' \
  --data @examples/servicenow/execute_request.json
```

```json
{
  "request_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
  "kind": "erasure",
  "tenant": "acme",
  "status": "executed",
  "replayed": false,
  "removed": 2,
  "certificate": {"seq": 7, "action": "erasure", "details": {"term": "alice"}},
  "signed_certificate": {"certificate": {}, "signature": {
    "algorithm": "HMAC-SHA256", "key_id": "sn-demo", "signature": "9f86d081..."}}
}
```

Verify addresses the request rather than repeating it, so the flow only
needs the two fields it already has:

```bash
curl -sS -X POST http://127.0.0.1:8321/dsar/verify \
  -H 'Content-Type: application/json' \
  --data @examples/servicenow/verify_request.json
```

```json
{
  "request_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
  "tenant": "acme",
  "kind": "erasure",
  "status": "executed",
  "passed": true,
  "checks": {
    "residual_scan": {"passed": true, "residual_count": 0},
    "recall_probe": {"passed": true, "reason": "no_match"},
    "tombstone_present": {"passed": true},
    "audit_chain": {"passed": true},
    "backend_verification": {"skipped": true, "passed": true}
  }
}
```

**The certificate on the ticket.** `DSARService.ticket_payload(request_id,
tenant)` builds the single artifact the closing step attaches: the request
as recorded, the status, the erasure certificate with its detached
signature, a freshly taken verify report and the audit head hash the whole
bundle is anchored to. Building it *is* a verification, so the probe it
reports is the probe it was built from. Attach it as a JSON file, or paste
the certificate and the head hash into a work note if the ticket should
stay readable without a download.

**MCP as the alternative.** In an agent environment the same push path is
available as MCP tools: `dsar_plan`, `dsar_execute` and `dsar_verify` on
the governed memory server (see [`mcp_server.md`](mcp_server.md)). Same
validation, same payloads, same audit entries. Use REST when a workflow
engine drives the loop, MCP when an agent does.

## Pull mode

Pull mode is the zero ServiceNow-side development path. Nothing is built
inside ServiceNow at all: the platform emits what it already knows how to
emit, and Provem picks it up.

**Intake by mail.** A native notification on the request item sends an
email to a mailbox that Provem watches. A mail rule (or a small fetch job)
writes each message as an `.eml` file into a drop folder, and
`FileDropSource` consumes it. The subject line is the contract,
`DSAR <kind> [<ticket>]`, and the body carries the rest as `key: value`
lines. `examples/servicenow/email_example.eml`:

```
From: privacy-team@acme.example
To: dsar@provem.example
Subject: DSAR erasure RITM0010001

tenant: acme
term: alice
```

The `From` address stands in as the requester when the body names none, so
a stock notification template needs no editing. Unknown body lines
(signatures, disclaimers, vendor boilerplate) are ignored rather than
carried along.

Set the notification's content type to **text/plain**. ServiceNow
notifications default to text/html; an HTML-only mail carries no
text/plain part, and the parser reads that as an empty body. This was
confirmed against a real instance (see "Validated against a real
instance" above).

**Intake by topic.** A ServiceNow workflow that already publishes record
changes to a Kafka topic needs no new integration either: point a consumer
at the topic and wrap it in `KafkaSource`. The consumer object is injected,
so the broker client and its configuration stay in the deployment and the
listener stays pure stdlib. The record keeps ServiceNow's own field names;
`default_transform` maps `sys_id` onto `request_id` and `number` onto
`ticket`. `examples/servicenow/kafka_message.json`:

```json
{
  "sys_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
  "number": "RITM0010001",
  "kind": "erasure",
  "tenant": "acme",
  "requester": "privacy-team@acme.example",
  "term": "alice"
}
```

A deployment whose field names differ passes its own `transform` to
`DSARListener` instead of forking the listener.

**Wiring it up.**

```python
from cognitive_memory.dsar_listener import DSARListener, FileDropSource
from cognitive_memory.mcp_server import GovernedMemoryService

service = GovernedMemoryService(config.server)._dsar_service()
DSARListener(service, [FileDropSource("/var/spool/dsar")]).run()
```

Each message drives the full loop (plan, execute, verify) and yields one
compact result carrying `status`, `replayed` and `verify_passed`.

**The return path.** Three ways back into ServiceNow, all of them native:

* An **Inbound-Email-Trigger**: send the result as a mail whose subject
  carries the ticket number, and let the inbound action update the ticket
  and attach the certificate.
* A **Kafka-Message-Trigger** on a reply topic: publish the verify report
  and the certificate, and let the trigger update the record.
* A **Scheduled-Trigger** that polls `GET /dsar/requests/<request_id>?tenant=<tenant>`
  for the requests it has open. The status view answers `planned`,
  `executed`, `held`, `approved` or `rejected` plus `last_verify_passed`,
  which is enough for a workflow to decide whether to close the ticket or
  escalate it.

```bash
curl -sS 'http://127.0.0.1:8321/dsar/requests/a1b2c3d4e5f60718293a4b5c6d7e8f90?tenant=acme'
```

## Request lifecycle

One request moves through a small, closed set of states, and the ticket
system only ever has to understand these:

| Status | Meaning | Next step |
|---|---|---|
| `planned` | A dry run happened. Nothing was executed. | Execute, or drop the request. |
| `executed` | The action was carried out and audited. | Verify, attach the certificate, close. |
| `held` | A strict revocation profile queued the request for a second operator. Nothing was erased or revoked. | Approve or reject. |
| `approved` | A reviewer signed off and the held action ran. | Verify, attach the certificate, close. |
| `rejected` | A reviewer closed the request without executing it. | Close the ticket with the reason. |

A hold is not an error. Under a strict revocation profile, a request whose
requester cannot be authorized (or whose text carries instruction risk) is
held on purpose, and the response carries `pending_id` and `reason`. The
flow branches on `status` and routes the ticket to a reviewer.

Held requests are settled over REST, addressed by the `request_id` the
ticket already knows rather than by the internal pending id:

```bash
curl -sS -X POST http://127.0.0.1:8321/dsar/approve \
  -H 'Content-Type: application/json' \
  -d '{"request_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
       "tenant": "acme", "reviewer": "dpo@acme.example"}'

curl -sS -X POST http://127.0.0.1:8321/dsar/reject \
  -H 'Content-Type: application/json' \
  -d '{"request_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
       "tenant": "acme", "reviewer": "dpo@acme.example",
       "reason": "requester could not be authenticated"}'
```

An approved erasure returns the certificate the approval produced, so the
closing comment carries the same evidence a direct execution would. Only a
held request can be approved or rejected: an unknown `request_id` answers
404, an already settled one answers 400, and neither is silently absorbed
as success.

`verify` can be called at any time and any number of times. It is
read-only against memory, it appends one audit entry per probe (the probe
itself is evidence), and it never rewrites the request's recorded outcome.
An unknown `request_id` is answered structurally with
`"unknown_request_id": true` rather than as an error, because a polling
integration asks about ids this tenant may never have seen.

## Metrics and health

```bash
curl -sS http://127.0.0.1:8321/health
curl -sS http://127.0.0.1:8321/metrics
```

`/health` returns `{"status": "ok"}` and is deliberately exempt from the
shared secret, so a supervisor or a load balancer can probe liveness
without holding the token.

`/metrics` merges the gateway's own counters with the service's:

```json
{
  "gateway": {"requests_total": 12, "unauthorized_total": 0, "errors_total": 0},
  "service": {
    "requests": {"erasure": {"executed": 1}},
    "verify": {"passed": 2, "failed": 0},
    "residuals_found": 0,
    "uptime_seconds": 41.2
  }
}
```

`gateway.errors_total` counts server-side failures only: a 400 or a 404 is
the caller's business, not a fault of the service. For a Prometheus scrape,
`DSARService.render_metrics()` renders the same snapshot as text lines
(`dsar_requests_total`, `dsar_verify_total`, `dsar_residuals_found_total`,
`dsar_uptime_seconds`), with the zero series always emitted so a missing
series and a zero series stay distinguishable on a dashboard.

## Trust model

**The bind address is the boundary.** The gateway binds `127.0.0.1` by
default, and that loopback bind is the actual security control. Everything
below is defense in depth on top of it.

**`X-DSAR-Token` is not authentication.** The optional shared secret is a
constant-time comparison against one static string. It keeps another local
process on a shared host from calling the erasure API by accident. It does
not identify a caller, it does not expire, and it cannot be scoped. Real
authentication, authorization, TLS termination, rate limiting and caller
audit belong in a reverse proxy or API gateway in front of this service.
That is where an internet-facing deployment terminates the inbound
connection from the ticket system.

**No access log by default.** The HTTP shell drops the per-request log
line on purpose: request lines carry the subject term of a DSAR in the
query string, and the default behaviour must not put personal data into an
operator's stderr. Access logging belongs to the proxy in front, where
retention and scrubbing are deliberate decisions.

**`requester` is self-declared.** The gateway records who the caller says
is acting; it does not authenticate them. That is exactly what the strict
revocation profiles exist for: under such a profile, an unauthorized or
missing requester turns the request into a hold that a named reviewer has
to approve, so authorization happens where it can actually be enforced.
See [`trust_model.md`](trust_model.md) and [`threat_model.md`](threat_model.md).

**Error mapping.** A rejected payload comes back as 400 with the
validation message, an unknown request as 404, a known route called with
the wrong verb as 405, an oversize body as 413, a missing or wrong token as
401, and any unexpected failure as an opaque 500 whose detail is counted
rather than returned.

## Honest limits

* **No certified ServiceNow application.** There is no store entry and
  nothing to install on the platform side. The integration is Workflow
  Studio plus an Outbound REST step, or the native email and Kafka paths.
  Everything in this document is built from platform features that already
  exist in the target instance.
* **No OAuth or mTLS in the gateway.** Shared secret and loopback bind
  only, as described above. Put a proxy in front for anything else.
* **The listener is at-least-once, with no dead-letter queue and no
  retry.** A crash between reading a message and finishing it delivers
  that message again on the next poll; a failed message is reported in the
  result list and dropped, not requeued. Redelivery is defused by
  idempotency rather than by machinery: a request carrying a stable
  `request_id` (the ServiceNow `sys_id`) replays its recorded outcome
  instead of erasing twice. A deployment that needs stronger guarantees
  should put a real broker in front of the listener rather than grow one
  inside it.
* **Producers must write atomically into the drop folder.** The folder
  poll reads whatever `.eml` or `.json` file it finds and then moves it,
  so a file that is still being written can be consumed half-finished.
  Write to a temporary name in the same directory and rename it into
  place; the rename is atomic on the same filesystem.
* **Replay does not validate the request body.** Idempotency is keyed on
  `request_id` alone. A second call with the same id but a different term
  replays the first outcome and does not report the discrepancy. Send the
  `sys_id` of the record the request belongs to, and never reuse an id for
  a different request.
* **An access replay re-derives the package from live state.** The first
  execution stores a hash, not a copy of the personal data, so a replayed
  access request returns the current package with `hash_drift` and both
  hashes. Drift is expected, because the subject's data keeps changing,
  and is not treated
  as a verification failure; what verify asserts for an export is the
  audit trail behind it.
* **The audit trail carries request identifiers.** `dsar_plan`,
  `dsar_execute` and `access_export` entries store the subject term or
  subject identifier, the requester and the ticket, because replay,
  verify and the ticket artifact are rebuilt from the trail alone. They
  never store record content or exported values, but these identifiers
  are personal data in their own right: erasing the audit trail itself
  is a separate, deliberate operation outside this integration's scope.
* **Metrics are process-local.** The counters describe what this service
  instance handled since it started, not the tenant's full history. The
  audit trail is the authority for history, and rebuilding a history figure
  on every scrape would mean re-reading every tenant's trail.
* **`approve` and `reject` are deliberately not MCP tools.** The MCP
  surface exposes `dsar_plan`, `dsar_execute` and `dsar_verify` only.
  Releasing a held request is the human control that a hold exists to
  create, and handing it to the same agent layer that raised the request
  would defeat it. Approvals go over REST, from the ticket system, under a
  named reviewer.
* **An approved consent withdrawal lands as a full restriction.** A held
  consent withdrawal that a reviewer approves goes through the shared
  revocation path, so the read is refused as `do_not_use` rather than as
  `consent_revoked`, and a purpose-scoped withdrawal ends up broader than
  requested. The verify probe is generic about which refusal it sees (it
  asserts that the read is refused, not which reason was recorded), so the
  evidence stays correct either way. If the exact reason code matters to
  your reporting, read it from the audit trail.

## Signing

Certificates are signed with HMAC-SHA256 over the canonical JSON form of
the certificate, and the signature is **detached**: it sits next to the
certificate, never inside it. The certificate bytes therefore stay
byte-identical to the audit entry they came from, and an auditor can
re-serialize the certificate and re-check the signature without stripping
fields first.

Configure it once, in the server half of the gateway config
(`examples/servicenow/gateway_config.json`):

```json
{
  "host": "127.0.0.1",
  "port": 8321,
  "token": "",
  "max_body_bytes": 1000000,
  "server": {
    "dsar_signing_secret": "change-me-shared-secret",
    "dsar_signing_key_id": "sn-demo"
  }
}
```

Every response that carries a certificate then carries a
`signed_certificate` next to it, with `algorithm`, `key_id` and
`signature`. The `key_id` is what makes rotation possible: an old
certificate stays verifiable against the key it was issued under. With an
empty secret the service gets no signer at all and every response stays
byte-identical to an unsigned deployment, so signing can be switched on
without changing any consumer.

Verification from the receiving side:

```python
from cognitive_memory.signing import canonical_json, verify_signature

payload = canonical_json(bundle["certificate"]).encode("utf-8")
assert verify_signature(payload, bundle["signature"], secret)
```

HMAC is a deliberate floor, not a PKI: it proves the certificate was issued
by a holder of the shared key, not by a named individual, and the verifier
has to hold that same key. Asymmetric signatures (a certificate anybody can
verify without being able to issue one) are enterprise scope and not part
of this layer.
