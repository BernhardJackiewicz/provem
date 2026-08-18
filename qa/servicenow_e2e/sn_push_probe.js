// ServiceNow server-side push probe (paste into Scripts - Background,
// sys.scripts.do, on the target instance). Drives the full DSAR loop from
// ServiceNow against the gateway, the same outbound mechanism the Workflow
// Studio REST step uses. Set BASE to the gateway's public URL (a tunnel in
// dev) and TOKEN to the gateway's X-DSAR-Token. The ngrok-skip-browser-
// warning header is only needed for the ngrok free tier; drop it otherwise.
//
// Output (gs.info, visible in the script output and in syslog) is compact
// on purpose: status codes and booleans, never the raw certificate.

var BASE = 'https://REPLACE-WITH-GATEWAY-URL';
var TOKEN = 'REPLACE-WITH-GATEWAY-TOKEN';
var RID = 'RITM-PUSH-0001'; // a stable ServiceNow sys_id/ticket in real use

function call(path, payload, withToken) {
  var r = new sn_ws.RESTMessageV2();
  r.setHttpMethod('post');
  r.setEndpoint(BASE + path);
  r.setRequestHeader('Content-Type', 'application/json');
  if (withToken) r.setRequestHeader('X-DSAR-Token', TOKEN);
  r.setRequestHeader('ngrok-skip-browser-warning', '1');
  r.setRequestBody(payload);
  var resp = r.execute();
  return { code: resp.getStatusCode(), body: resp.getBody() };
}

var reqBody = JSON.stringify({
  request_id: RID, kind: 'erasure', tenant: 'acme',
  requester: 'privacy-team@acme.example', term: 'alice', ticket: 'RITM0010001'
});

var plan = call('/dsar/plan', reqBody, true);
var planO = {}; try { planO = JSON.parse(plan.body); } catch (e) {}
var ex = call('/dsar/execute', reqBody, true);
var exO = {}; try { exO = JSON.parse(ex.body); } catch (e) {}
var ver = call('/dsar/verify', JSON.stringify({ request_id: RID, tenant: 'acme' }), true);
var verO = {}; try { verO = JSON.parse(ver.body); } catch (e) {}
var neg = call('/dsar/plan', '{}', false).code;

gs.info('DSAR_PUSH plan_code=' + plan.code + ' matched=' + planO.matched_count);
gs.info('DSAR_PUSH exec_code=' + ex.code + ' status=' + exO.status +
        ' replayed=' + exO.replayed + ' removed=' + exO.removed +
        ' signed=' + (exO.signed_certificate ? 'yes' : 'no') +
        ' key=' + (exO.signed_certificate && exO.signed_certificate.signature ?
                   exO.signed_certificate.signature.key_id : ''));
gs.info('DSAR_PUSH verify_code=' + ver.code + ' passed=' + verO.passed);
gs.info('DSAR_PUSH negative_no_token=' + neg);
