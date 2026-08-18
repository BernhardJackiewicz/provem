"""Thin ServiceNow Table API client for the E2E validation scripts.

Credentials come from ``~/.provem-sn-pdi.json`` (outside the repo, never
committed, never printed):

    {"instance": "https://devXXXXXX.service-now.com",
     "user": "admin", "password": "..."}

Pure stdlib (urllib + base64 Basic Auth), matching the repo's ethos. The
helpers cover exactly what the QA flows need: create records, read the
generated notification mails, settle approvals.
"""

import base64
import json
import os
import urllib.parse
import urllib.request

CREDENTIALS_PATH = os.path.expanduser("~/.provem-sn-pdi.json")


class SNClient:
    def __init__(self, credentials_path: str = CREDENTIALS_PATH) -> None:
        with open(credentials_path, encoding="utf-8") as handle:
            creds = json.load(handle)
        self.base = creds["instance"].rstrip("/")
        raw = ("%s:%s" % (creds["user"], creds["password"])).encode("utf-8")
        self._auth = "Basic " + base64.b64encode(raw).decode("ascii")

    def _request(self, method, path, payload=None, params=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": self._auth,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def get(self, table, params):
        return self._request("GET", "/api/now/table/" + table, params=params)["result"]

    def get_record(self, table, sys_id, fields=""):
        params = {"sysparm_fields": fields} if fields else None
        return self._request("GET", "/api/now/table/%s/%s" % (table, sys_id),
                             params=params)["result"]

    def post(self, table, payload):
        return self._request("POST", "/api/now/table/" + table, payload)["result"]

    def patch(self, table, sys_id, payload):
        return self._request("PATCH", "/api/now/table/%s/%s" % (table, sys_id),
                             payload)["result"]

    # -- QA helpers ---------------------------------------------------------

    def create_dsar_incident(self, term, label="QA DSAR"):
        """Raise the incident that triggers the QA DSAR notification."""

        return self.post("incident", {
            "short_description": "DSAR erasure %s (%s)" % (term, label),
            "description": term,
            "urgency": "3", "impact": "3",
        })

    def newest_dsar_email(self):
        rows = self.get("sys_email", {
            "sysparm_query": "subjectLIKEDSAR^ORDERBYDESCsys_created_on",
            "sysparm_limit": "1",
            "sysparm_fields": "sys_id,subject,recipients,content_type,type",
        })
        return rows[0] if rows else None

    def email_as_eml(self, sys_id):
        """Reconstruct a ServiceNow outbound mail as .eml bytes.

        sys_email stores subject/body/body_text, not the final MIME message
        (that is assembled by the SMTP sender at send time), so this builds
        the message the same way: plain mails from body_text, HTML mails as
        a text/html single part. The golden-path MIME test still requires a
        real SMTP send; this covers the template and header contract.
        """

        row = self.get_record(
            "sys_email", sys_id,
            "subject,body,body_text,content_type,recipients,user_id")
        from email.message import EmailMessage

        message = EmailMessage()
        message["From"] = "dev-instance@service-now.example"
        message["To"] = row.get("recipients") or "dsar@provem.example"
        message["Subject"] = row.get("subject", "")
        ctype = (row.get("content_type") or "text/plain").lower()
        if ctype.startswith("text/plain"):
            message.set_content(row.get("body_text") or row.get("body") or "")
        else:
            message.set_content("")
            message.add_alternative(row.get("body") or "", subtype="html")
        return message.as_bytes()

    def open_approvals(self, source_table="incident"):
        return self.get("sysapproval_approver", {
            "sysparm_query": "state=requested^ORDERBYDESCsys_created_on",
            "sysparm_limit": "10",
            "sysparm_fields": "sys_id,state,approver,sysapproval",
        })

    def approve(self, approval_sys_id):
        return self.patch("sysapproval_approver", approval_sys_id,
                          {"state": "approved"})
