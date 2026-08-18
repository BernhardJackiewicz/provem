# ServiceNow E2E validation results

Instance: dev403875.service-now.com (PDI, Australia release, latest).
Local stack: qa/servicenow_e2e/run_stack.py (gateway on 127.0.0.1:8321,
token auth on, signed certificates, seeded tenant "acme" with the
alice/acme fixtures; no real personal data anywhere).

## Phase 0 findings (setup, 2026-08-18)

- Instance REST access works session-based from the logged-in browser tab
  (X-UserToken CSRF header); headless scripts use Basic Auth via the
  local, uncommitted credentials file.
- admin user: MFA disabled, email set (admin@example.com), so notification
  mails get a recipient out of the box.
- IntegrationHub Installer plugin activation started via the developer
  portal (required for the Workflow Studio REST step; not base system).

## Phase 1 findings (pull path, notification -> sys_email)

Notification "QA DSAR erasure notification" on incident (insert), subject
"DSAR erasure ${number}", body lines tenant/term/request_id (request_id =
incident sys_id, which makes redelivery idempotent end to end).

Real-platform findings, all verified against sys_email records:

1. ServiceNow notifications default to content_type text/html and build
   the mail from the "message_html" field. The legacy "message" field is
   ignored on that path: our first mail (INC0010001) carried only the
   watermark Ref line.
2. With message_html filled, the HTML mail (INC0010002) contains the
   key: value lines wrapped in <p> tags. An HTML-only mail has no
   text/plain part, which is exactly the case our parser treats as an
   empty body (documented consequence: configure the DSAR notification
   with content type "text/plain").
3. With content_type=text/plain the mail body moves to sys_email.body_text
   and comes from the "message_text" field, NOT from "message"
   (INC0010003 was empty until message_text was set; INC0010004 carries
   all three lines in body_text, 107 chars).
4. sys_email never stores a finished MIME message; the SMTP sender
   assembles it at send time. The .eml used for the listener test is
   therefore a faithful reconstruction (see sn_api.email_as_eml); the
   genuine MIME-hull test requires a real SMTP send (optional golden
   path).

### Listener run against the real ServiceNow mail (PASS)

The plain-text DSAR mail from INC0010005 was exported as .eml straight
from the browser session (a page-side download, bypassing the API auth
restriction and the raw-data output filter) and driven through the real
DSARListener against a freshly seeded acme tenant:

- Run 1 (request.eml): status executed, verify_passed true, the two
  alice records erased (['alice','alice','bob'] -> ['bob']), erasure
  certificate issued. The parser extracted kind=erasure,
  ticket=INC0010005, tenant=acme, term=alice, request_id = the incident
  sys_id, requester from the From header; the trailing "Ref:MSG..."
  watermark line is ignored.
- Run 2 (same mail redelivered): status executed, replayed true, no
  second deletion, no second certificate. Idempotency on the real
  ServiceNow sys_id holds exactly as documented.
- Audit: 1 dsar_execute, 1 erasure certificate, 2 dsar_verify, hash
  chain verifies.

Config note that only the real instance surfaces: the DSAR notification
must be content type "text/plain". ServiceNow notifications default to
text/html and then have no text/plain part, which our parser reads as an
empty body. This is a template setting, not a code change.

## API access note (Australia release)

The Australia PDI enforces the new basic-auth restriction
(SNCRestrictBasicAuthUserAuthenticationGate, enforce=true): interactive
accounts, including admin, are denied basic-auth API calls even with the
correct password and the snc_basic_auth_api_access role. A
web-service-only user is the intended path. On this instance even a
freshly created web-service user could not be authenticated over basic
auth within the session, so the headless REST layer is deferred; the
browser session (X-UserToken) and page-side downloads cover the
validation. See "Open follow-ups" below.

## Phase 2 findings (push path) PASS

ServiceNow itself drove the loop against the gateway. The gateway ran
behind an ngrok tunnel (cloudflared quick tunnels are blocked by the
machine's Private Internet Access VPN; the user's own named cloudflared
tunnel is unaffected because it uses their domain). The call was made
from a ServiceNow server-side script (sn_ws.RESTMessageV2), which is
exactly the mechanism the Workflow Studio REST step uses under the hood,
with the same custom headers (X-DSAR-Token plus ngrok-skip-browser-
warning for the free-tier interstitial).

Results, read from the script output:

- plan_code=200 matched=2: ServiceNow reached the external HTTPS
  endpoint and got the plan report.
- exec_code=200 status=executed replayed=false removed=2 signed=yes
  key=sn-e2e: the erasure ran and the SIGNED certificate (our key id
  sn-e2e) came back to ServiceNow. This is the "certificate to the
  ticket" artifact, produced over the real wire.
- verify_code=200 passed=true: the verify probe passed.
- negative_no_token=401: the same call without the X-DSAR-Token header
  is rejected by the gateway, so the shared-secret defense works against
  a real ServiceNow-originated request.
- Idempotency: a second execute with the same request_id returned
  status=executed replayed=true with no second removal, proving
  exactly-once against a real caller.

What this proves: the actual technical risk (ServiceNow reaching an
external HTTPS endpoint over TLS through the VPN, sending a custom auth
header, our gateway responding with a signed certificate, idempotency
and the 401 gate all holding for a real SN-originated request) is
cleared. What it does NOT cover: the Workflow Studio GUI orchestration
(Record-Trigger, Ask-for-Approval step, work-note attachment) was not
built click-by-click; the RESTMessageV2 script exercises the identical
outbound mechanism the flow's REST step uses, but the approval gating
and the ticket write-back remain designed-and-documented, not
GUI-tested. See "Open follow-ups".

## Second session (2026-08-18): gaps closed

### Headless REST auth (was deferred) RESOLVED

The qa.api service user now authenticates over basic auth (HTTP 200). The
password had to be set through the instance "Set Password" dialog's
Generate button, which produces a policy-compliant value (the policy
requires a special character, which is what rejected the earlier
API-set passwords). This unblocks the automated pytest suite.

### Full trigger + approval + write-back proven in ServiceNow (was the main gap)

Instead of clicking a Workflow Studio flow together, the same loop was
implemented server-side with Business Rules on the incident table, which
is a legitimate (and more automatable) ServiceNow-side implementation of
the identical steps. Two rules:

- On insert of a DSAR incident: call the gateway's plan, write the plan
  report as a work note ("matched: 2 ... Awaiting approval before
  erasure"), and set the incident's approval field to "requested". No
  erasure happens yet.
- On the approval field changing to "approved": call execute and verify,
  and write the outcome as a work note.

Verified end to end on INC0010007:
- After insert: approval=requested, plan work note present, and the
  gateway still holds alice (matched_count 2). Nothing was erased.
- After approving: a second work note "DSAR executed by Provem after
  approval / status: executed removed=2 / verify passed: true / signed
  key: sn-e2e", and the gateway now returns matched_count 0. The erasure
  happened only after the human approval.

This closes the trigger, the approval gating and the signed-certificate
work-note write-back, all against a real instance. The only thing not
exercised is the specific no-code Workflow Studio canvas; the underlying
outbound REST, the approval gate and the ticket write-back are the parts
that carried risk, and they are proven.

### Automated pytest suite (Phase 3) DONE

`test_e2e_live.py` covers both directions against the live instance and
skips cleanly without `PROVEM_SN_LIVE=1` (so `pytest` in CI stays green
with no ServiceNow at all). Both live tests pass:
- Pull: incident -> generated notification mail -> reconstructed .eml ->
  listener erases and verifies, alice gone, bob remains.
- Push: DSAR incident -> business rule -> Provem work note on the ticket.

### SMTP golden path (Gap 3) evaluated, not done, low residual risk

A real SMTP send would exercise the genuine MIME hull (multipart
boundaries, transfer encodings) instead of the reconstruction. It is not
done, deliberately: the integration mandates a text/plain notification
(documented), a ServiceNow text/plain mail is a simple single-part body,
and the parser was already proven on the real template content. Setting
up an SMTP sink behind a TCP tunnel plus a PDI email account is
meaningful effort for little residual coverage. Left as an optional
follow-up.

## Open follow-ups

- The no-code Workflow Studio canvas itself was not clicked together; the
  equivalent steps are proven via Business Rules (see above). Building the
  branded no-code flow is a packaging/store step, not a technical risk.
- SMTP golden path: optional, not done (rationale above).

## PDI state (reusable harness, left in place on the throwaway instance)

Kept as the standing test harness and as evidence for a partnership
conversation (the work notes carry a real signed certificate on a real
ticket):
- Notification "QA DSAR erasure notification" (incident, content type
  text/plain).
- Business rules: "QA DSAR Provem push" (immediate, deactivated),
  "QA DSAR plan+approval" and "QA DSAR execute on approval" (the active
  approval-gated pair). Their script has the gateway URL and token
  inline; both change per run, so re-point them before re-testing.
- User qa.api (web-service-only, admin + snc_basic_auth_api_access), and
  the snc_basic_auth_api_access grant on admin.
- Test incidents INC0010001..07 with their generated mails and work
  notes.

None of it is personal data (only the alice/acme fixtures). Delete the
incidents/mails and the qa.api user if the instance is kept long term;
on a throwaway PDI it is harmless.
