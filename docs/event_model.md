# Local Event Model

This is MVP 1 local groundwork for relationship-heavy recruiting memory. It is
not Graphiti, not a database migration and not a production ontology.

## Purpose

Flat temporal facts can say `candidate_sam salary_expectation 120k` and
`candidate_sam target_client Nova`, but they cannot safely explain that the
salary applied to Nova while a later salary applied to Orion. The local event
model adds a small context layer so retrieval can use relationships when they
are explicit enough and abstain when they are not.

## Models

- `MemoryEvent`: a local event derived from an accepted temporal fact.
- `EventParticipant`: candidate, client, role, company or unknown participant.
- `EventRelation`: typed edge such as `expressed_preference`,
  `stated_requirement`, `applies_to_client`, `pitch_blocked`,
  `objection_raised` or `objection_resolved`.
- `EventContext`: user, project, candidate, client, role, source and timestamp
  context.

Events are additive. `TemporalFact` remains the main interpreted fact model and
the controller remains the only durable write authority.

## Retrieval Behavior

Event-aware retrieval is conservative:

- It only activates for known mixed candidate/client requests.
- It requires a matching candidate id, client id and relation.
- It filters wrong-scope events before ranking.
- It uses the latest event in the same candidate/client/relation context.
- It abstains on conflicts or missing context instead of guessing.
- It does not use event content that violates deletion, do-not-use, provenance
  or prompt-injection safety checks.

This allows structured cases like:

```text
FACT candidate_sam|salary_expectation|120k
FACT candidate_sam|target_client|Nova
FACT candidate_sam|salary_expectation|150k
FACT candidate_sam|target_client|Orion
```

to answer `candidate sam salary for client nova` with the Nova-linked event,
while preserving safe abstention for ambiguous or unparsed cases.

## Graphiti Mapping Notes

Future Graphiti mapping should be straightforward:

- `MemoryEvent` maps to episode/event nodes.
- `EventParticipant` maps to entity nodes.
- `EventRelation` maps to temporal edges.
- `EventContext` maps to scoped edge properties such as project, client, role,
  source trust and validity time.
- `evidence_episode_ids` maps to source episode provenance.

The detailed mapping/parity contract lives in `docs/graphiti_mapping.md`.
No Graphiti integration exists yet. These models define the local contract that
a future backend can implement and compare against. The current
`LocalGraphitiParityBackend` uses the local store and policy gate; it is not a
Graphiti client.

## Known Limitations

- Event creation is deterministic and derived from accepted facts.
- Natural-language extraction is still rule-based.
- Project switching inside one paragraph is not generally parsed.
- General coreference such as pronouns or "that company" is not solved by this
  model.
- Entity identity still depends on synthetic names such as `candidate_*` and
  `client_*`.
- Events improve some relationship-heavy synthetic cases, but they do not prove
  real recruiting readiness.
