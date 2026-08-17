# Licensing and product boundary

Provem follows an **open engine, closed operations** model.

## What is MIT (this repository, forever)

The verifiable governance core. Everything the published benchmarks and
compliance claims rest on stays open, so anyone can reproduce and audit them:

- the governed memory engine and policy evaluation
- tombstones, erasure, revocation and requester-authority mechanisms
- the purpose stack (purpose limitation, consent revocation, policy drift)
- the audit event schema, erasure certificates and the verify re-check
- scope, consent and provenance data models
- lineage and conflict-resolution mechanisms
- the reference implementation of semantic erasure
- the reproducible benchmarks, test harness and limitations documentation
- the MCP server, generic connector specifications and simple reference
  connectors sufficient to evaluate the system
- the DSAR REST gateway, the generic listener protocol and the reference
  connectors for email, Kafka and drop-folder intake

MIT means exactly what it says: use it, fork it, ship it commercially,
no strings.

## What a future enterprise layer covers (separate, proprietary)

None of the following exists in this repository today. When it is built, it
lives in separate proprietary repositories and is sold as operations, not as
smarter compliance code:

- hosted multi-tenant control plane, admin UI, dashboards and reporting
- SSO, SCIM, RBAC and enterprise permissions
- long-term, tamper-evident certificate archiving as a managed service
- legal-hold, retention and approval workflows with managed policy rollouts
- production-grade Kafka, email, HRIS and ServiceNow integrations and
  certified connector builds
- monitoring, alerting, backup, restore and disaster recovery
- SLA, support, upgrades, migration services, DPA and procurement packages
- signing and verification of official "Provem Certified" artifacts

A generic listener can be open; the monitored, updated and contractually
supported service is the product. An open audit schema is good; the durable,
multi-tenant, provably operated audit archive is the product.

## Contributions

Contributions to this repository are accepted under the repository's MIT
license. If a contributor license agreement is introduced later, it will be
announced here before it applies to any incoming contribution.
