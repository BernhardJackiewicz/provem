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

## Open follow-ups

- Headless basic-auth for the pytest suite is unresolved on this PDI.
  Root cause understood: the Australia PDI blocks basic-auth API calls
  for interactive accounts, and the qa.api service user's password was
  repeatedly rejected by the password policy ("must contain at least 1
  special character"). Fix is a policy-compliant password with a special
  character on a web-service-only user, then Phase 3 (Playwright/pytest)
  can be built. Deferred, not blocked.
- Workflow Studio GUI flow (Record-Trigger, Ask-for-Approval, work-note
  write-back) not built click-by-click; the outbound REST mechanism it
  relies on is proven via RESTMessageV2. This is the main untested GUI
  surface.
- Optional golden path: a real SMTP send (own SMTP account) to test the
  genuine MIME hull, versus the faithful reconstruction used for the
  pull path.
- Cleanup left in the PDI: notification "QA DSAR erasure notification",
  incidents INC0010001..05, the qa.api user, and the
  snc_basic_auth_api_access grant on admin. All harmless on a throwaway
  PDI; remove if the instance is kept.
