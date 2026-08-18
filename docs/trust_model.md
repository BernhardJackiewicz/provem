# Provem (formerly Engram): Trust Model & Deployment Boundaries

The enterprise audit flagged "no auth", "no TLS", "no RBAC", "tenant trusted from
the caller", "no horizontal scaling". These are **not bugs in the shipped
server**: they are consequences of the **stdio transport** and are the host's
responsibility. This document states the trust boundary explicitly so deployers
know exactly what Provem enforces and what they must provide.

## What Provem is

`mcp-serve` is a **local, stdio JSON-RPC MCP server**: the MCP client launches it
as a **subprocess** and talks to it over stdin/stdout. There is **no network
socket, no listener, no port** (verified: `serve_stdio` reads line-delimited JSON
from stdin). Requests are processed **strictly sequentially**, one line at a time.

Consequences that are **by design**, not defects:

- **No transport TLS.** stdin/stdout is an in-process pipe on the same host;
  there is nothing to encrypt in transit. TLS applies to a network gateway, not
  a pipe.
- **No connection-level authentication.** The client process that spawned the
  server is already trusted by the host OS. Authentication is the host's job.
- **Tenant is a request argument.** `remember/recall/forget` take `tenant=...`.
  The server trusts the caller to pass the right tenant, because the caller is
  the trusted host. Tenant is an **isolation boundary within a trusted client**,
  not an authorization boundary against hostile clients.
- **Single process, in-memory by default.** Two independently launched servers
  do not share state.

## What Provem does enforce (inside the trusted boundary)

- **Tenant isolation:** one tenant never reads or over-erases another
  (separate `GovernedMemory`, tenant-keyed erasure), tested.
- **Injection quarantine, sensitivity + consent, provenance/trust conflict
  resolution, calibrated abstention**, the governance headline.
- **Durable storage + tamper-evident, persistent audit** (SQLite backend +
  append-only hash-chained audit), survives restart.
- **Retention enforcement, input-size limits, fail-fast config validation,
  ReDoS-screened deny-list regex, thread-safe embedding.**

## Deploying beyond a single trusted host

If you expose Provem to **mutually untrusted clients or over a network** (SaaS,
multi-org), put a **gateway in front of the stdio server** that provides what the
transport intentionally does not:

| Need | Where it belongs |
|---|---|
| AuthN/AuthZ, API keys, OIDC | Gateway / host, before the MCP call |
| TLS / mTLS | Gateway terminating the network connection |
| Binding a connection to a tenant (so the caller cannot forge `tenant`) | Gateway injects/validates `tenant` from the authenticated identity |
| RBAC (e.g. read-only auditor vs writer) | Gateway maps identity → allowed tools |
| Rate limiting / quotas / DoS | Gateway (Provem adds only per-request input-size caps) |
| Horizontal scaling | Point every instance at one **shared durable backend** (SQLite on a shared volume, or a real DB adapter via the `MemoryBackend` protocol) and a shared `audit_path` |
| Encryption at rest, SIEM, backup | Storage/infra layer (SQLite file on an encrypted, backed-up volume; ship `audit_path` JSONL to your SIEM) |

The `MemoryBackend` protocol (`write/delete_ids/all_records/candidates`) is the
seam for a networked, shared, encrypted store: the governance logic is unchanged
above it (proven by the SQLite and live-Mem0 backends).

## Supply chain

Core install has **no required dependencies** (intentional; runs on any Python
3.9+). Optional extras (`mem0ai`, `letta-client`, `graphiti`) are unpinned in
`pyproject.toml`. For a reproducible deployment image, pin them with a
`constraints.txt` (e.g. `pip install -c constraints.txt .[mem0]`) rather than
relying on floating versions.
