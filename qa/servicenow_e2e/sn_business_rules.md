# ServiceNow Business Rules for the push-path validation

These reproduce the approval-gated push loop server-side, which is easier
to automate and version than the no-code Workflow Studio canvas while
exercising the identical steps: a DSAR incident triggers a plan and an
approval request, and only an approval runs the erasure and writes the
signed certificate back as a work note.

Set `BASE` to the gateway's public URL (a tunnel in dev) and `TOKEN` to
the gateway's `X-DSAR-Token`. Both change per run. The
`ngrok-skip-browser-warning` header is only for the ngrok free tier.

Shared helper (used in both rules):

```javascript
function call(path, payload) {
  var r = new sn_ws.RESTMessageV2();
  r.setHttpMethod('post');
  r.setEndpoint(BASE + path);
  r.setRequestHeader('Content-Type', 'application/json');
  r.setRequestHeader('X-DSAR-Token', TOKEN);
  r.setRequestHeader('ngrok-skip-browser-warning', '1');
  r.setRequestBody(payload);
  var resp = r.execute();
  return { code: resp.getStatusCode(), body: resp.getBody() };
}
var rid = current.sys_id.toString();
var body = JSON.stringify({
  request_id: rid, kind: 'erasure', tenant: 'acme',
  requester: (current.opened_by.getDisplayValue() || 'servicenow'),
  term: (current.description.toString() || 'alice'),
  ticket: current.number.toString()
});
```

## Rule 1: plan + request approval

- Table: incident, When: after insert.
- Condition: short_description starts with "DSAR".

```javascript
var pl = call('/dsar/plan', body);
var plO = {}; try { plO = JSON.parse(pl.body); } catch (e) {}
current.work_notes = 'DSAR plan by Provem\n' +
  'matched: ' + plO.matched_count + ' sources: ' + JSON.stringify(plO.sources) +
  '\nAwaiting approval before erasure.';
current.approval = 'requested';
current.update();
```

## Rule 2: execute on approval

- Table: incident, When: after update.
- Condition: short_description starts with "DSAR" AND approval changes to
  "approved".

```javascript
var ex = call('/dsar/execute', body);
var exO = {}; try { exO = JSON.parse(ex.body); } catch (e) {}
var ver = call('/dsar/verify', JSON.stringify({ request_id: rid, tenant: 'acme' }));
var verO = {}; try { verO = JSON.parse(ver.body); } catch (e) {}
var cert = exO.signed_certificate || exO.certificate || {};
current.work_notes = 'DSAR executed by Provem after approval\n' +
  'status: ' + exO.status + ' removed=' + exO.removed + '\n' +
  'verify passed: ' + verO.passed + '\n' +
  'signed key: ' + ((cert.signature && cert.signature.key_id) || '');
current.update();
```

An immediate (no-approval) variant is the same as Rule 2 but on insert;
useful for a quick smoke test, but the approval-gated pair is the real
compliance shape (a human sees the plan before anything is erased).
