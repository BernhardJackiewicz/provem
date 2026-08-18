"""Live end-to-end tests against a real ServiceNow instance.

These are NOT part of the main unit suite: they require a running gateway,
a public tunnel, the qa.api credentials file and the QA notification plus
business rules configured in the PDI (see RESULTS.md). Every test skips
cleanly when its prerequisites are absent, so a plain `pytest` in CI stays
green without any ServiceNow at all.

Run them deliberately, with the harness up:

    python3 qa/servicenow_e2e/run_stack.py &          # gateway + seed
    ngrok http 8321                                   # public tunnel
    PROVEM_SN_LIVE=1 PYTHONPATH=src python3 -m pytest qa/servicenow_e2e/test_e2e_live.py -v

The push test needs the tunnel URL wired into the PDI business rules; it
reads what the rules already produced (the work note on a DSAR incident)
rather than reconfiguring them, so it validates the real, standing
integration.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
_SRC = os.path.join(_REPO, "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

LIVE = os.environ.get("PROVEM_SN_LIVE") == "1"
CREDS = os.path.expanduser("~/.provem-sn-pdi.json")


def _client_or_skip():
    if not LIVE:
        raise unittest.SkipTest("set PROVEM_SN_LIVE=1 to run the live ServiceNow tests")
    if not os.path.exists(CREDS):
        raise unittest.SkipTest("no ~/.provem-sn-pdi.json credentials file")
    from sn_api import SNClient  # noqa: E402 (path bootstrap above)

    sys.path.insert(0, _HERE)
    return SNClient()


class PullPathLiveTests(unittest.TestCase):
    def setUp(self):
        self.sn = _client_or_skip()

    def test_notification_mail_drives_the_listener(self):
        from cognitive_memory.dsar import DSARService
        from cognitive_memory.dsar_listener import DSARListener, FileDropSource
        from cognitive_memory.mcp_server import GovernedMemoryService

        incident = self.sn.create_dsar_incident("alice", label="pytest pull")
        # give ServiceNow a moment to generate the notification mail
        mail = None
        for _ in range(12):
            time.sleep(2)
            mail = self.sn.newest_dsar_email()
            if mail and incident["number"] in mail.get("subject", ""):
                break
        self.assertIsNotNone(mail, "no DSAR mail generated for the incident")
        self.assertIn(incident["number"], mail["subject"])

        eml = self.sn.email_as_eml(mail["sys_id"])
        drop = tempfile.mkdtemp(prefix="dsar-live-")
        self.addCleanup(shutil.rmtree, drop, True)
        with open(os.path.join(drop, "request.eml"), "wb") as handle:
            handle.write(eml)

        svc = GovernedMemoryService()
        mem = svc.memory_for("acme")
        for text, s, r, o, src in [("alice likes tea", "alice", "likes", "tea", "crm"),
                                    ("alice works in berlin", "alice", "works_in", "berlin", "hr"),
                                    ("bob likes coffee", "bob", "likes", "coffee", "crm")]:
            mem.remember(text, subject=s, relation=r, object=o, tenant="acme", source=src)

        results = DSARListener(DSARService(svc), [FileDropSource(drop)],
                               sleep_fn=lambda s: None).run(max_iterations=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "executed")
        self.assertTrue(results[0]["verify_passed"])
        self.assertEqual([rr.subject for rr in mem.backend.all_records()], ["bob"])


class PushPathLiveTests(unittest.TestCase):
    def setUp(self):
        self.sn = _client_or_skip()

    def test_dsar_incident_gets_a_signed_certificate_work_note(self):
        incident = self.sn.create_dsar_incident("alice", label="pytest push")
        sys_id = incident["sys_id"]

        note = ""
        for _ in range(12):
            time.sleep(2)
            rows = self.sn.get("sys_journal_field", {
                "sysparm_query": "element_id=%s^element=work_notes" % sys_id,
                "sysparm_fields": "value", "sysparm_limit": "10",
            })
            note = " || ".join(r.get("value", "") for r in rows)
            if "DSAR" in note and "Provem" in note:
                break

        self.assertIn("Provem", note, "no Provem work note written back to the ticket")
        # Either the immediate-execute rule or the approval flow's plan step
        # has run; both prove the trigger and the write-back.
        self.assertTrue("status:" in note or "matched:" in note)


if __name__ == "__main__":
    unittest.main()
