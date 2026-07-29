"""Regression tests for the 11 verified bug-hunt findings (one per reproduced case)."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


def _call(server, name, args):
    resp = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": args}})
    return resp["result"]["structuredContent"]


class C1CrossTenantCleanupTests(unittest.TestCase):
    def test_cleanup_of_one_tenant_does_not_delete_another(self):
        from cognitive_memory.mcp_server import GovernedMemoryService, MCPServer, ServerConfig

        with tempfile.TemporaryDirectory() as tmp:
            cfg = ServerConfig.from_dict({
                "backend": "sqlite",
                "sqlite_path": str(Path(tmp) / "m.db"),
                "tenant_profiles": {"t1": {"name": "aggressive", "retention_days": {"high": 0}}, "t2": "default"},
            })
            server = MCPServer(GovernedMemoryService(cfg))
            _call(server, "remember", {"text": "bob fact here", "subject": "bob", "relation": "note",
                                       "object": "keepme", "tenant": "t2", "entity": "bob"})
            # t1 runs cleanup with a 0-day retention; it must NOT touch t2's record
            out = _call(server, "cleanup", {"tenant": "t1"})
            self.assertEqual(out["removed"], 0)
            rec = _call(server, "recall", {"query": "bob fact", "tenant": "t2", "entity": "bob"})
            self.assertFalse(rec["abstained"], "t2's record was destroyed by t1's cleanup")
            self.assertEqual(rec["answer"], "keepme")


if __name__ == "__main__":
    unittest.main()
