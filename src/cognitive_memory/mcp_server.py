"""Configurable MCP server exposing GovernedMemory as agent tools.

Speaks JSON-RPC 2.0 (the Model Context Protocol transport) over stdio, with no
third-party dependencies -- the dispatch core is ``handle(request_dict) ->
response_dict`` so it is fully unit-testable without real pipes.

Configuration is per tenant: a server config maps tenants to compliance
profiles (recruitment / pharma / finance / custom), so one deployment serves
many domains with different governance. Each tenant gets its own isolated
GovernedMemory instance (separate erasure state and backend), which guarantees
that one tenant's "forget" never over-blocks another tenant's memory.

Tools exposed: remember, recall, forget, list_profiles, audit_export.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .compliance import CompliancePolicy, available_profiles, resolve_policy
from .reliability import Bm25Backend, GovernedMemory, NaiveBackend, Scope

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "provem-governed-memory"
SERVER_VERSION = "0.1.0"

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


@dataclass
class ServerConfig:
    """Per-deployment configuration: which compliance profile each tenant uses."""

    default_profile: str = "default"
    tenant_profiles: Dict[str, Any] = field(default_factory=dict)
    backend: str = "naive"  # "naive" | "bm25" | "sqlite"
    sqlite_path: str = ""
    audit_path: str = ""
    max_text_chars: int = 100_000
    max_line_bytes: int = 1_000_000

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServerConfig":
        backend = str(data.get("backend", "naive"))
        if backend not in ("naive", "bm25", "sqlite"):
            raise ValueError("backend must be 'naive', 'bm25' or 'sqlite'")
        config = cls(
            default_profile=str(data.get("default_profile", "default")),
            tenant_profiles=dict(data.get("tenant_profiles", {})),
            backend=backend,
            sqlite_path=str(data.get("sqlite_path", "")),
            audit_path=str(data.get("audit_path", "")),
            max_text_chars=int(data.get("max_text_chars", 100_000)),
            max_line_bytes=int(data.get("max_line_bytes", 1_000_000)),
        )
        config.validate()
        return config

    def validate(self) -> None:
        """Fail fast at load: every profile must resolve and every deny-list
        regex must compile / pass the ReDoS screen (via resolve_policy)."""
        resolve_policy(self.default_profile)
        for tenant, spec in self.tenant_profiles.items():
            try:
                resolve_policy(spec)
            except Exception as exc:
                raise ValueError("tenant %r has an invalid profile: %s" % (tenant, exc))

    @classmethod
    def load(cls, path: str) -> "ServerConfig":
        from pathlib import Path

        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


class GovernedMemoryService:
    """Routes each tenant to its own GovernedMemory with the tenant's profile."""

    def __init__(
        self,
        config: Optional[ServerConfig] = None,
        backend_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.config = config or ServerConfig()
        self._explicit_backend_factory = backend_factory
        # A durable SQLite backend is shared across tenants (tenant is a column;
        # governance scopes reads and keeps erasure tenant-keyed), so data and
        # the audit trail survive a restart.
        self._shared_sqlite = None
        if backend_factory is None and self.config.backend == "sqlite":
            from .adapters.sqlite_backend import SqliteBackend

            self._shared_sqlite = SqliteBackend(self.config.sqlite_path or ":memory:")
        self._memories: Dict[str, GovernedMemory] = {}
        import threading

        self._lock = threading.Lock()

    def _make_backend(self):
        if self._explicit_backend_factory is not None:
            return self._explicit_backend_factory()
        if self.config.backend == "sqlite":
            return self._shared_sqlite
        if self.config.backend == "bm25":
            return Bm25Backend()
        return NaiveBackend()

    def _audit_path_for(self, tenant: str) -> Optional[str]:
        if not self.config.audit_path:
            return None
        return "%s.%s.jsonl" % (self.config.audit_path, tenant)

    def profile_for(self, tenant: str) -> CompliancePolicy:
        spec = self.config.tenant_profiles.get(tenant, self.config.default_profile)
        return resolve_policy(spec)

    def memory_for(self, tenant: str) -> GovernedMemory:
        # Lock the check-then-act so concurrent requests for a new tenant cannot
        # create two isolated instances (only matters if the service is embedded
        # in a multi-threaded host; the stdio server is single-threaded).
        with self._lock:
            if tenant not in self._memories:
                self._memories[tenant] = GovernedMemory(
                    backend=self._make_backend(),
                    policy=self.profile_for(tenant),
                    audit_path=self._audit_path_for(tenant),
                )
            return self._memories[tenant]

    # -- tool implementations --------------------------------------------

    @staticmethod
    def _require_tenant(args: Dict[str, Any]) -> str:
        tenant = args.get("tenant")
        if not isinstance(tenant, str) or not tenant.strip():
            raise ValueError("a non-empty string 'tenant' is required")
        return tenant

    @staticmethod
    def _coerce_trust(value: Any) -> float:
        if value is None:
            return 0.9
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError("'trust' must be a number")

    def remember(self, args: Dict[str, Any]) -> Dict[str, Any]:
        tenant = self._require_tenant(args)
        text = str(args.get("text") or "")
        if not text:
            raise ValueError("remember requires non-empty 'text'")
        if len(text) > self.config.max_text_chars:
            raise ValueError("'text' exceeds max_text_chars (%d)" % self.config.max_text_chars)
        mem = self.memory_for(tenant)
        before = len(mem.audit)
        mem.remember(
            text,
            subject=str(args.get("subject", "")),
            relation=str(args.get("relation", "")),
            object=str(args.get("object", "")),
            tenant=tenant,
            entity=str(args.get("entity", "")),
            source=str(args.get("source", "user")),
            trust=self._coerce_trust(args.get("trust")),
            consent=bool(args.get("consent", False)),
        )
        quarantined = [e.to_dict() for e in mem.audit.entries()[before:] if e.action == "quarantine"]
        return {
            "stored": True,
            "tenant": tenant,
            "profile": self.profile_for(tenant).name,
            "quarantined": bool(quarantined),
            "quarantine_reason": quarantined[0]["details"]["reason"] if quarantined else "",
        }

    def recall(self, args: Dict[str, Any]) -> Dict[str, Any]:
        tenant = self._require_tenant(args)
        query = str(args.get("query") or "")
        if not query:
            raise ValueError("recall requires non-empty 'query'")
        mem = self.memory_for(tenant)
        result = mem.recall_value(query, tenant=tenant, entity=str(args.get("entity", "")))
        return {
            "answer": result.answer,
            "abstained": result.abstained,
            "reason": result.reason,
            "provenance": list(result.provenance),
        }

    def forget(self, args: Dict[str, Any]) -> Dict[str, Any]:
        tenant = self._require_tenant(args)
        term = str(args.get("term") or "")
        if not term:
            raise ValueError("forget requires non-empty 'term'")
        mem = self.memory_for(tenant)
        removed = mem.forget(term, Scope(tenant=tenant, subject=str(args.get("subject", ""))))
        cert = mem.audit.filter("erasure")[-1].to_dict()
        return {"term": term, "tenant": tenant, "backend_confirmed_deletes": removed, "certificate": cert}

    def list_profiles(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "builtin": list(available_profiles()),
            "default_profile": self.config.default_profile,
            "tenant_profiles": {k: (v if isinstance(v, str) else "custom") for k, v in self.config.tenant_profiles.items()},
        }

    def audit_export(self, args: Dict[str, Any]) -> Dict[str, Any]:
        tenant = self._require_tenant(args)
        mem = self.memory_for(tenant)
        return {"tenant": tenant, "verified": mem.verify_audit(), "audit": mem.export_audit()}

    def cleanup(self, args: Dict[str, Any]) -> Dict[str, Any]:
        tenant = self._require_tenant(args)
        mem = self.memory_for(tenant)
        # Scope the sweep to this tenant -- the SQLite backend is shared, so an
        # unscoped cleanup would delete other tenants' records under this
        # tenant's retention policy.
        removed = mem.cleanup_expired(tenant=tenant)
        return {"tenant": tenant, "removed": removed}


# JSON Schemas for the tools (advertised via tools/list)
_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "remember",
        "description": "Store a memory under governance (injection quarantine, sensitivity, provenance, scope).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The memory content."},
                "tenant": {"type": "string", "description": "Tenant/organization id (selects the compliance profile)."},
                "entity": {"type": "string", "description": "Subject the memory is about (for scope isolation)."},
                "subject": {"type": "string"},
                "relation": {"type": "string"},
                "object": {"type": "string"},
                "source": {"type": "string", "description": "e.g. user, external_tool, scraper."},
                "trust": {"type": "number"},
            },
            "required": ["text", "tenant"],
        },
    },
    {
        "name": "recall",
        "description": "Governed recall: returns a value or a safe abstention with a reason.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "tenant": {"type": "string"},
                "entity": {"type": "string"},
            },
            "required": ["query", "tenant"],
        },
    },
    {
        "name": "forget",
        "description": "Enforce erasure (GDPR Art. 17). Returns a tamper-evident erasure certificate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {"type": "string"},
                "tenant": {"type": "string"},
                "subject": {"type": "string"},
            },
            "required": ["term", "tenant"],
        },
    },
    {
        "name": "list_profiles",
        "description": "List available compliance profiles and the tenant->profile mapping.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "audit_export",
        "description": "Export the tamper-evident governance audit trail for a tenant.",
        "inputSchema": {
            "type": "object",
            "properties": {"tenant": {"type": "string"}},
            "required": ["tenant"],
        },
    },
    {
        "name": "cleanup",
        "description": "Delete records past their retention window for a tenant (returns count).",
        "inputSchema": {
            "type": "object",
            "properties": {"tenant": {"type": "string"}},
            "required": ["tenant"],
        },
    },
]


class MCPServer:
    """JSON-RPC 2.0 / MCP dispatch over an injectable service."""

    def __init__(self, service: Optional[GovernedMemoryService] = None) -> None:
        self.service = service or GovernedMemoryService()
        self._initialized = False
        self._tool_impls = {
            "remember": self.service.remember,
            "recall": self.service.recall,
            "forget": self.service.forget,
            "list_profiles": self.service.list_profiles,
            "audit_export": self.service.audit_export,
            "cleanup": self.service.cleanup,
        }

    def handle(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle one JSON-RPC request. Returns a response dict, or None for
        notifications (requests without an ``id``)."""
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return self._error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 request")
        method = request.get("method")
        req_id = request.get("id")
        is_notification = "id" not in request
        params = request.get("params") or {}

        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method in ("notifications/initialized", "initialized"):
                self._initialized = True
                return None  # notification, no response
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": _TOOLS}
            elif method == "tools/call":
                result = self._call_tool(params)
            else:
                if is_notification:
                    return None
                return self._error(req_id, METHOD_NOT_FOUND, "unknown method: %s" % method)
        except (ValueError, TypeError) as exc:
            if is_notification:
                return None
            return self._error(req_id, INVALID_PARAMS, str(exc))
        except Exception as exc:  # defensive: never crash the loop
            if is_notification:
                return None
            return self._error(req_id, INTERNAL_ERROR, str(exc))

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        self._initialized = True
        requested = str(params.get("protocolVersion") or PROTOCOL_VERSION)
        return {
            "protocolVersion": requested,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _call_tool(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ValueError("'arguments' must be an object")
        impl = self._tool_impls.get(name)
        if impl is None:
            raise ValueError("unknown tool: %s" % name)
        payload = impl(arguments)
        # MCP tool result: structured content plus a text rendering
        return {
            "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
            "structuredContent": payload,
            "isError": False,
        }

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def serve_stdio(self, stdin=None, stdout=None) -> None:  # pragma: no cover - IO loop
        """Read line-delimited JSON-RPC from stdin, write responses to stdout."""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        max_line = self.service.config.max_line_bytes
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            if max_line and len(line) > max_line:
                self._write(stdout, self._error(None, INVALID_REQUEST, "request exceeds max_line_bytes"))
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                self._write(stdout, self._error(None, PARSE_ERROR, "invalid JSON"))
                continue
            response = self.handle(request)
            if response is not None:
                self._write(stdout, response)

    @staticmethod
    def _write(stdout, obj: Dict[str, Any]) -> None:  # pragma: no cover - IO
        stdout.write(json.dumps(obj) + "\n")
        stdout.flush()
