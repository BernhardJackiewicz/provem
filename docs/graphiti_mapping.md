# Graphiti Mapping Contract

This document defines the MVP 2.1 Graphiti parity contract. It is not a live
Graphiti integration and it is not Neo4j schema documentation.

The goal is to make the local memory model explicit enough that a future
Graphiti backend can be tested for semantic parity before it is trusted.

## Status

- `GraphitiBackend` is still an optional stub.
- `LocalGraphitiParityBackend` is dependency-free and uses the existing local
  store plus policy gate.
- `graphiti-env-check` reports setup readiness only.
- No benchmark result currently uses real Graphiti.

## Mapping

| Local model | Graphiti-like target | Required semantics |
| --- | --- | --- |
| `Episode` | episode/source node | Append-only evidence record. Stores source, actor, timestamp, scope and evidence id. |
| `TemporalFact` | temporal fact edge | Interpreted claim with subject, relation, object, validity window and provenance. |
| `MemoryEvent` | event node | Higher-context event derived from accepted facts, such as a candidate statement or client requirement. |
| `EventParticipant` | entity node reference | Candidate, client, role, company, recruiter or unknown participant. |
| `EventRelation` | temporal edge/fact | Typed relationship such as `expressed_preference`, `stated_requirement`, `applies_to_client`, `pitch_blocked` or `objection_resolved`. |
| `EventContext` | scoped metadata | User, project, candidate, client, role, source trust and conversation context. |
| `provenance` / `evidence` | source/evidence reference | Every selected memory must remain traceable to source episodes. |
| `valid_at` / `invalid_at` | temporal validity | Current queries must exclude invalidated facts; historical queries may access facts valid at `as_of`. |
| `privacy_policy` | policy metadata | `deleted`, `do_not_use` and `sensitive` are retrieval policy states, not normal facts to answer with. |
| `source_type` / `source_trust` | metadata | Used for conflict handling and traceability. It is not a production trust system. |
| scope fields | entity/context metadata | `user_id`, `project_id`, `candidate_id`, `client_id`, `role_id`, `subject_id`, `actor_type` and `scope_confidence`. |

## Policy Boundary

Graphiti must not become a second write authority. A real adapter should either
store accepted controller outputs or propose candidates through the controller.
It must not bypass:

- deleted evidence checks
- do-not-use terms and memory ids
- project/user scope
- prompt-injection quarantine
- source conflict abstention
- provenance requirements
- current versus historical validity rules

Policy flags belong to the policy layer and retrieval gate. They may be stored
as metadata in a graph backend, but should not be modeled as ordinary positive
facts that can be retrieved as user-facing truth.

## Required Backend Operations

A future real Graphiti adapter should expose these operations:

- `write_episode`
- `write_temporal_fact`
- `write_memory_event`
- `query_current_facts`
- `query_historical_facts`
- `query_relationships`

It should also preserve the existing `TemporalGraphBackend` contract for local
controller compatibility:

- `add_episode`
- `add_candidate`
- `add_fact`
- `update_fact`
- `get_fact`
- `list_facts`
- `active_facts`
- `matching_active_facts`
- `add_event`
- `update_event`
- `get_event`
- `list_events`
- `add_reflection`
- `update_reflection`
- `list_reflections`
- `audit`
- `search`

## Capability Flags

Backends should report:

- `supports_temporal_facts`
- `supports_events`
- `supports_policy_metadata`
- `supports_provenance`

The local parity backend sets all four to `True` because it uses the existing
local model. A real Graphiti backend must prove these through integration tests
before any performance or product claims are made.

## Parity Expectations

The local parity tests currently require equivalent behavior for:

- current truth
- historical truth
- superseded facts
- deleted facts excluded
- do-not-use facts excluded
- wrong-scope facts excluded before ranking
- event relationship queries
- company-as-client versus company-as-employer separation
- source conflict abstention
- provenance preservation

These are semantic tests, not service tests. They do not validate Graphiti API
compatibility, Neo4j configuration, latency, scale or operational behavior.

## Open Work Before Real Graphiti

- Decide exact Graphiti package/client API and supported version.
- Add service-level tests with a disposable Neo4j/Graphiti setup.
- Prove delete/do-not-use semantics survive graph persistence and reload.
- Prove temporal queries return the same current/historical answers as local
  parity tests.
- Add latency and failure-mode reporting.
- Keep all tests runnable without Graphiti by default.
