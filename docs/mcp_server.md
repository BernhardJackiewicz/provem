# Provem Governed Memory: MCP Server

A configurable, dependency-free MCP server that puts the governance layer in
front of agent memory. One deployment serves many domains: each tenant is
mapped to a compliance profile (recruitment / pharma / finance / custom), and
each tenant is fully isolated (its own memory instance and erasure state).

It speaks JSON-RPC 2.0 over stdio (the MCP transport). The dispatch core is a
pure `handle(request) -> response` function, so it is unit-testable without pipes
and embeds anywhere.

## Run it

```bash
# default profile for all tenants
PYTHONPATH=src python3 -m cognitive_memory mcp-serve --default-profile default

# per-tenant profiles + BM25 ranking, from a config file
PYTHONPATH=src python3 -m cognitive_memory mcp-serve --config examples/mcp/server_config.json
```

Server config (`examples/mcp/server_config.json`):

```json
{
  "default_profile": "default",
  "backend": "bm25",
  "tenant_profiles": {
    "acme_recruiting": "recruitment",
    "northwind_pharma": "pharma",
    "atlas_bank": "finance"
  }
}
```

`backend`: `naive` (token overlap), `bm25` (ranked), or `sqlite` (durable,
survives restart: set `sqlite_path`). `audit_path` persists the hash-chained
audit trail (per-tenant JSONL, survives restart). `max_text_chars` /
`max_line_bytes` cap input size. Profiles and deny-list regex are validated at
load (fail fast). `tenant_profiles` values can be a built-in name or an inline
profile object.

**Trust model:** the server is stdio-only (a trusted subprocess of the host),
so auth/TLS/RBAC/rate-limiting and horizontal scaling belong to a gateway in
front of it: see [`docs/trust_model.md`](trust_model.md). Retention is enforced
via the `cleanup` tool (or `cleanup_expired`) when a profile sets `retention_days`.

## Tools

| Tool | Purpose |
|---|---|
| `remember` | Store a memory under governance (injection quarantine, sensitivity, provenance, scope). Accepts optional `allowed_purposes` / `consented_purposes` arrays for purpose limitation. Returns whether it was quarantined and why. |
| `recall` | Governed recall: returns a value or a safe abstention **with a reason** (never a low-confidence guess). Accepts an optional declared, untrusted `purpose`; the response includes `record_ids` and `excluded_reasons`. |
| `forget` | Enforce erasure (GDPR Art. 17). Accepts an optional `requester`, recorded in the certificate; under a strict-revocation profile an unverified request returns a held pending revocation instead of executing. Returns a tamper-evident **erasure certificate**. |
| `verify` | Execution-time re-check for a previously served record id: is it still authorized (erasure, restriction, scope, retention)? The hook a tool layer calls before acting on a recalled value. |
| `list_profiles` | List available compliance profiles and the tenant→profile map. |
| `audit_export` | Export the hash-chained governance audit trail for a tenant (`verified: true/false`). Serves are audited as `recall_served`, not only blocks. |

Example `tools/call`:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{
  "name":"remember",
  "arguments":{"text":"candidate salary is 120k","subject":"cand_7",
               "relation":"salary","object":"120k","tenant":"acme_recruiting","entity":"cand_7"}}}
```

Every tool returns both `content` (MCP text) and `structuredContent` (typed).

## How it decides what is non-compliant

Four mechanisms, all driven by the tenant's `CompliancePolicy`:

1. **Detection**: injection and sensitive-content regex (baseline + per-profile
   patterns). Quarantined memories are stored but never served.
2. **Erasure**: `forget(term)` tombstones the term; matching memories are
   filtered on every future recall (`strict` inspects full text, `lenient` only
   subject/object). Enforcement holds **even if the backend does not delete**.
3. **Scope / tenancy**: a query about subject X is never answered from subject
   Y's look-alike; a tenant never sees another tenant's memory.
4. **Provenance / trust**: per-source trust; cross-source conflicts need a clear
   trust margin or the layer abstains rather than guess. Low-trust writes below
   `min_store_trust` are quarantined.

## Configuring per domain (recruitment vs pharma vs finance)

Different domains have different rules: that is exactly what profiles are for.
Load a built-in profile by name, or ship your own JSON/YAML:

```python
from cognitive_memory import CompliancePolicy, GovernedMemory

policy = CompliancePolicy.load("examples/profiles/pharma.json")
mem = GovernedMemory(policy=policy)            # or GovernedMemory(policy="pharma")
```

Built-in profiles and what they change:

| Profile | Sensitive patterns added | Notable settings |
|---|---|---|
| `default` | - | behaviour-identical to the original governance |
| `recruitment` | salary, compensation, notice period, visa status | scope isolation, distrust scrapers/tools, retention 365d |
| `pharma` | MRN, patient id, ICD-10, adverse event, diagnosis, dosage | consent required, `min_store_trust=0.3`, strict erasure, 10y retention |
| `finance` | IBAN, account/routing number, credit card, CVV, PAN | consent required, `min_store_trust=0.2`, strict tenant isolation |

Full field reference: see `CompliancePolicy` in `src/cognitive_memory/compliance.py`.

## Enterprise properties

- **Tamper-evident audit**: every decision is hash-chained; `audit_export`
  returns `verified` and any edit/reorder/truncation is detectable.
- **Erasure certificates**: `forget` emits an auditable record (term, tenant,
  targeted vs backend-confirmed counts).
- **Backend-agnostic**: runs over the built-in store, a BM25 store, or a real
  Mem0 backend (`adapters/mem0_reliability.py`, live-verified).
- **Pluggable answering**: keyless extractive answering by default; an optional
  key-gated LLM answerer (`LLMAnswerer`, caller injects the model) converts
  retrieval into end-to-end answers.
- **No required dependencies**: pure stdlib; optional extras (Mem0, PyYAML,
  an LLM) are cleanly gated.

## Honest limits

- Detection is rule-based: it is strong in the closed loop (provenance/trust
  contains attacks even when patterns miss) but the standalone detector misses
  paraphrased/novel payloads (measured on real datasets in
  `docs/reliability_results.md`). Tune per-profile patterns for your domain.
- Keyless answer synthesis cannot match an LLM answerer on free-form
  conversational QA; wire `LLMAnswerer` for that (see `docs/locomo_results.md`).
- Retention windows are recorded but enforcement (auto-expiry) is not yet wired.
- Two-phase retrieval (`recall_candidates` then `release`) and the record
  `release` operation exist in the library but are deliberately **not** exposed
  as MCP tools yet: candidate ids from the in-memory backends are only stable
  within a single process, so an external client could hold ids that silently
  become invalid after a restart. Use a durable backend and the library API for
  the two-phase flow until durable, restart-safe handles are added.
