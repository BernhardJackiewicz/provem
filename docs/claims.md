# Claims we make, and claims we do not make

One page you can hand to a prospect, a partner or a lawyer. Provem is a
tool that helps an operator meet data-subject obligations for AI agent
memory. It is not a legal service and it does not certify compliance. The
detailed, evidence-rated version of every claim lives in
[`claim_register.md`](claim_register.md); this page is the short, plain
statement.

## What we claim, and can back

- **Read-side erasure enforcement.** Once a term is forgotten, governed
  recall will not serve it. Enforcement is tenant-scoped, and the
  tombstones survive a restart and a backup restore (with an explicit,
  audited reconcile step). This is verified by the reproducible test
  suite and the closed-loop benchmark.
- **Backend delete attempts with honest residual counts.** Where the
  backend supports it, Provem asks it to delete the matched records and
  records how many deletes the backend confirmed. If copies remain, the
  certificate says so; it does not paper over a lagging backend.
- **A signed erasure certificate.** A tamper-evident record of what was
  targeted, what was removed, the backend-confirmed count, the requester
  and the derivative stores touched. HMAC-SHA256 signed when a signer is
  configured. It is evidence of the action Provem took.
- **A tamper-evident audit trail.** A SHA-256 hash-chained log that
  records serves as well as blocks and detects any later edit,
  reordering or truncation (with an external anchor for truncation).
- **The rest of the governance surface.** Tenant and entity isolation,
  prompt-injection quarantine, consent revocation (blanket or per
  purpose), purpose limitation, requester authority under strict
  profiles, and calibrated abstention instead of confident wrong answers.
- **A reproducible benchmark.** A self-authored, closed-loop benchmark
  comparing governed against ungoverned memory. Every number is
  replayable from frozen artifacts at zero cost.
- **A working ServiceNow integration, validated end to end against a real
  instance** (Australia release), in both directions: pull via a native
  notification email, and push via an incident trigger that runs plan,
  approval, execute and verify and writes the signed certificate back to
  the ticket.

## What we do not claim

- **Not "GDPR-compliant" or "GDPR-certified."** Provem helps an operator
  meet Article 17 and related obligations. Compliance is a property of
  the deployer's whole process, not of one library, and we do not certify
  it.
- **Not guaranteed physical deletion everywhere.** Provem enforces
  erasure at read time and attempts backend deletes; it does not promise
  that every downstream, cached or derived copy in a system it does not
  control is physically gone. It reports what it could and could not
  confirm.
- **Not a legal proof of erasure.** The certificate is evidence of the
  action taken, not a legal attestation that a subject's data no longer
  exists anywhere.
- **Not externally security-audited, and not production-certified.** No
  third party has audited the code. Self-hosted only.
- **Not a certified ServiceNow Store application.** The integration is
  validated against a real instance and built from standard platform
  features; the certified, published Store app is a future step.
- **Tamper-evident, not tamper-proof.** The audit trail makes tampering
  detectable. It does not make it impossible.
- **Not an authentication, authorization or TLS layer.** Those belong in
  a reverse proxy or API gateway in front of the service; Provem
  documents that boundary rather than pretending to cover it.
- **The recall benchmark is self-authored, not independent.** It is
  LoCoMo-only for recall, judged by LLMs, and tuned on a dev split. The
  claim register lists every qualifier.
- **The reliability agent is deterministic, not an LLM.** That is
  deliberate, to isolate the memory layer's own contribution; it is a
  stated limitation, not a hidden one.

## The honest one-liner

Provem enforces erasure at recall, attempts backend deletes with honest
residual counts, and issues a signed, tamper-evident certificate of what
it did. It does not guarantee legal compliance or physical deletion
everywhere. Say the first sentence; never let it drift into the second.
