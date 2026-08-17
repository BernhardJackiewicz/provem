"""The DSAR REST gateway: a transport-free core plus a thin HTTP shell.

The gateway is split into a core and a shell. :class:`GatewayCore` is the
core: routing, authentication, body limits, payload parsing and error
mapping live there, and it takes an already-read request as plain Python
values and returns ``(status, payload)``. :class:`DSARHTTPServer` at the
bottom of this module is the shell; it only reads the socket, hands the
bytes over and writes the JSON back, so the whole REST surface is
testable, and reusable in-process, without ever binding a port.

Trust model: the default bind address is loopback (``127.0.0.1``), which
is the actual security boundary, and the optional ``X-DSAR-Token`` shared
secret is defense in depth for a host that other local processes share.
It is deliberately not an authentication system: real authentication,
authorization and TLS belong in a reverse proxy or API gateway in front
of this service, which is also where an internet-facing deployment gets
its rate limiting and audit of callers. ``/health`` stays exempt from the
token so a supervisor can probe liveness without holding the secret.

Errors are mapped so that a caller learns what it did wrong and nothing
about the inside of the service: a rejected payload comes back as 400
with the validation message, an unknown request as 404, and any
unexpected failure as an opaque 500 whose detail is counted, not
returned. Pure stdlib.
"""

from __future__ import annotations

import hmac
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from .dsar import DSARRequest
from .mcp_server import GovernedMemoryService, ServerConfig

# Shared-secret header, looked up case-insensitively (HTTP header names are).
TOKEN_HEADER = "x-dsar-token"

# The one route whose path carries a variable segment.
STATUS_PREFIX = "/dsar/requests/"

# POST-only routes and the method that serves them. Keeping the table here
# is what lets an unknown path answer 404 and a known path with the wrong
# verb answer 405.
_POST_ROUTES: Dict[str, str] = {
    "/dsar/plan": "_plan",
    "/dsar/execute": "_execute",
    "/dsar/verify": "_verify",
    "/dsar/approve": "_approve",
    "/dsar/reject": "_reject",
}

_GET_ROUTES = ("/health", "/metrics")

_METHOD_NOT_ALLOWED: Tuple[int, Dict[str, Any]] = (
    405, {"error": "method_not_allowed"})
_NOT_FOUND: Tuple[int, Dict[str, Any]] = (404, {"error": "not_found"})


def _str_field(data: Dict[str, Any], name: str, default: str) -> str:
    """Cast a known config key to ``str``; ``None`` reads as the default."""

    raw = data.get(name, default)
    return default if raw is None else str(raw)


def _int_field(data: Dict[str, Any], name: str, default: int) -> int:
    """Cast a known config key to ``int``; anything uncastable is a ValueError."""

    raw = data.get(name, default)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError("gateway config %r must be an integer, got %r" % (name, raw))


def _required(payload: Dict[str, Any], name: str, route: str) -> str:
    """Return a stripped, non-empty payload field or raise ValueError.

    The message names the missing field because it is handed straight to
    the caller as the 400 body.
    """

    raw = payload.get(name)
    value = "" if raw is None else str(raw).strip()
    if not value:
        raise ValueError("%s requires non-empty %r" % (route, name))
    return value


def _optional(payload: Dict[str, Any], name: str) -> str:
    raw = payload.get(name)
    return "" if raw is None else str(raw).strip()


def _query_value(query: Optional[Dict[str, Any]], name: str) -> str:
    """Read one query parameter, accepting the parse_qs list shape."""

    raw = (query or {}).get(name)
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
    return "" if raw is None else str(raw).strip()


@dataclass
class GatewayConfig:
    """Deployment settings for the DSAR gateway.

    ``token`` empty means the shared secret check is off, which is the
    right default for a loopback bind. ``server`` is the governed memory
    configuration the same deployment runs on, so one config file
    describes both halves.
    """

    host: str = "127.0.0.1"
    port: int = 8321
    token: str = ""
    max_body_bytes: int = 1_000_000
    server: ServerConfig = field(default_factory=ServerConfig)

    @classmethod
    def from_dict(cls, data: Any) -> "GatewayConfig":
        """Build a config from an untrusted mapping.

        Known keys are cast and validated, unknown keys are ignored (a
        deployment config may carry keys for other components), and a
        nested ``server`` mapping is delegated to :class:`ServerConfig`.
        """

        if not isinstance(data, dict):
            raise ValueError(
                "gateway config must be a dict, got %s" % type(data).__name__)

        raw_server = data.get("server")
        if raw_server is None:
            server = ServerConfig()
        elif isinstance(raw_server, ServerConfig):
            server = raw_server
        elif isinstance(raw_server, dict):
            server = ServerConfig.from_dict(raw_server)
        else:
            raise ValueError(
                "gateway config 'server' must be a dict, got %s"
                % type(raw_server).__name__
            )

        max_body_bytes = _int_field(data, "max_body_bytes", 1_000_000)
        if max_body_bytes <= 0:
            raise ValueError("gateway config 'max_body_bytes' must be positive")

        return cls(
            host=_str_field(data, "host", "127.0.0.1"),
            port=_int_field(data, "port", 8321),
            token=_str_field(data, "token", ""),
            max_body_bytes=max_body_bytes,
            server=server,
        )


class GatewayCore:
    """The DSAR REST surface, minus the socket.

    ``service`` is duck-typed on :class:`dsar.DSARService`
    (``plan``/``execute``/``verify``/``approve``/``reject``/``status``/
    ``metrics``), so an in-process caller can pass a wrapper or a fake.

    :meth:`handle_raw` is the entry point for the HTTP shell and owns
    everything that is about the wire: the counter, the query split,
    the shared secret, the body limit and JSON parsing.
    :meth:`dispatch` is the entry point for in-process callers and
    assumes an already authenticated, already parsed request, which is
    why the token check deliberately lives one level up.
    """

    def __init__(self, service: Any, config: Optional[GatewayConfig] = None) -> None:
        self.service = service
        self.config = config or GatewayConfig()
        # Process-local counters, reported by /metrics next to the
        # service's own. errors_total counts server-side failures (the
        # opaque 500s) only: a 400 or a 404 is the caller's business and
        # is not a fault of this gateway.
        self.requests_total = 0
        self.unauthorized_total = 0
        self.errors_total = 0

    # -- wire level ---------------------------------------------------------

    def handle_raw(self, method: str, path: str, headers: Dict[str, str],
                   body: bytes) -> Tuple[int, Dict[str, Any]]:
        """Handle one raw request and return ``(status, payload)``.

        ``path`` may carry a query string. ``body`` is the raw request
        body; it is only parsed for POST, where an empty body reads as an
        empty object so a no-argument route needs no payload at all.
        """

        self.requests_total += 1
        split = urlsplit(path or "")
        route = split.path
        query = parse_qs(split.query)

        if not self._authorized(route, headers):
            self.unauthorized_total += 1
            return 401, {"error": "unauthorized"}

        raw = body or b""
        if len(raw) > self.config.max_body_bytes:
            return 413, {"error": "body_too_large"}

        payload: Optional[Dict[str, Any]] = None
        if str(method).upper() == "POST":
            stripped = raw.strip()
            if not stripped:
                payload = {}
            else:
                try:
                    parsed = json.loads(stripped.decode("utf-8"))
                except ValueError:
                    return 400, {"error": "invalid_json"}
                if not isinstance(parsed, dict):
                    return 400, {"error": "payload_must_be_object"}
                payload = parsed

        return self.dispatch(method, route, payload, query)

    def _authorized(self, route: str, headers: Optional[Dict[str, str]]) -> bool:
        """Compare the shared secret in constant time; /health is exempt."""

        token = self.config.token
        if not token or route == "/health":
            return True
        provided = ""
        for name, value in (headers or {}).items():
            if str(name).lower() == TOKEN_HEADER:
                provided = "" if value is None else str(value)
                break
        return hmac.compare_digest(
            provided.encode("utf-8"), token.encode("utf-8"))

    # -- routing ------------------------------------------------------------

    def dispatch(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None,
                 query: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
        """Route an authenticated, parsed request to the service.

        Success returns the service payload unchanged, so the REST body
        and the MCP tool result stay the same artifact.
        """

        verb = str(method).upper()
        route = str(path)
        data = payload if isinstance(payload, dict) else {}

        if route == "/health":
            if verb != "GET":
                return _METHOD_NOT_ALLOWED
            return 200, {"status": "ok"}

        if route == "/metrics":
            if verb != "GET":
                return _METHOD_NOT_ALLOWED
            return self._guarded(self._metrics)

        handler = _POST_ROUTES.get(route)
        if handler is not None:
            if verb != "POST":
                return _METHOD_NOT_ALLOWED
            return self._guarded(lambda: getattr(self, handler)(data))

        if route.startswith(STATUS_PREFIX):
            request_id = route[len(STATUS_PREFIX):]
            if request_id and "/" not in request_id:
                if verb != "GET":
                    return _METHOD_NOT_ALLOWED
                return self._guarded(lambda: self._status(request_id, query))

        return _NOT_FOUND

    def _guarded(self, call: Callable[[], Dict[str, Any]]) -> Tuple[int, Dict[str, Any]]:
        """Run one service call and translate its failure into a status.

        Only the call is wrapped, never the routing above it, so a
        KeyError from the gateway's own tables can never masquerade as
        an unknown request.
        """

        try:
            return 200, call()
        except (ValueError, TypeError) as exc:
            return 400, {"error": str(exc)}
        except KeyError:
            return _NOT_FOUND
        except Exception:
            # Deliberately opaque: the detail belongs in the operator's
            # logs, not in the response of a request that may come from
            # an ITSM vendor's network.
            self.errors_total += 1
            return 500, {"error": "internal_error"}

    # -- route handlers -----------------------------------------------------

    def _plan(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.service.plan(DSARRequest.from_dict(payload))

    def _execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.service.execute(DSARRequest.from_dict(payload))

    def _verify(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request_id = _required(payload, "request_id", "verify")
        tenant = _required(payload, "tenant", "verify")
        return self.service.verify(request_id, tenant)

    def _approve(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request_id = _required(payload, "request_id", "approve")
        tenant = _required(payload, "tenant", "approve")
        reviewer = _required(payload, "reviewer", "approve")
        return self.service.approve(request_id, tenant, reviewer)

    def _reject(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request_id = _required(payload, "request_id", "reject")
        tenant = _required(payload, "tenant", "reject")
        reviewer = _required(payload, "reviewer", "reject")
        return self.service.reject(
            request_id, tenant, reviewer, _optional(payload, "reason"))

    def _status(self, request_id: str, query: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        tenant = _query_value(query, "tenant")
        if not tenant:
            raise ValueError("status requires a non-empty 'tenant' query parameter")
        return self.service.status(request_id, tenant)

    def _metrics(self) -> Dict[str, Any]:
        """Merge the gateway's own counters with the service's."""

        return {
            "gateway": {
                "requests_total": self.requests_total,
                "unauthorized_total": self.unauthorized_total,
                "errors_total": self.errors_total,
            },
            "service": self.service.metrics(),
        }


# -- HTTP shell -------------------------------------------------------------


class DSARHTTPServer(ThreadingHTTPServer):
    """The socket half of the gateway: bind, thread, delegate to the core.

    Requests are served on their own threads so a slow erasure cannot block
    a health probe from being accepted, but the DSAR service underneath is
    not documented as thread-safe (its replay registry and the audit trail
    are shared mutable state), so every call into the core is serialized on
    :attr:`lock`. Concurrency here buys accept-and-respond liveness, not
    parallel erasure.

    ``service`` is injectable for tests and for an embedder that already
    holds a service; the default builds the deployment's governed memory
    service from ``config.server``, which keeps the DSAR signer configured
    in exactly one place.
    """

    daemon_threads = True

    def __init__(self, config: GatewayConfig, service: Any = None) -> None:
        if service is None:
            service = GovernedMemoryService(config.server)._dsar_service()
        self.core = GatewayCore(service, config)
        self.lock = threading.Lock()
        super().__init__((config.host, config.port), _Handler)


class _Handler(BaseHTTPRequestHandler):
    """Adapter only: read bytes, call the core, write JSON back.

    No routing, no auth and no validation happens here; every such decision
    belongs to :class:`GatewayCore` so that the transport-free tests remain
    the behavioural truth of the REST surface.
    """

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
        self._handle("GET", b"")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler protocol
        length = self._content_length()
        limit = self.server.core.config.max_body_bytes
        if length > limit:
            # Answered from the declared length alone: reading a body we have
            # already decided to reject is exactly the memory exhaustion the
            # limit exists to prevent. The connection is closed rather than
            # kept alive because the unread body would otherwise be parsed as
            # the next request on it.
            self.close_connection = True
            self._respond(413, {"error": "body_too_large"})
            return
        self._handle("POST", self.rfile.read(length) if length else b"")

    def _content_length(self) -> int:
        """Declared body size; an absent or unparsable header reads as zero."""

        try:
            length = int(self.headers.get("Content-Length"))
        except (TypeError, ValueError):
            return 0
        return length if length > 0 else 0

    def _handle(self, method: str, body: bytes) -> None:
        with self.server.lock:
            status, payload = self.server.core.handle_raw(
                method, self.path, dict(self.headers), body)
        self._respond(status, payload)

    def _respond(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        """Drop the per-request access log.

        Request lines carry the subject term of a DSAR in the query string
        and would put personal data into an operator's stderr by default.
        Access logging belongs to the reverse proxy in front of this
        service, where it can be retained and scrubbed deliberately.
        """


def serve(config: GatewayConfig, service: Any = None) -> None:
    """Bind and serve until the process is interrupted (blocking)."""

    DSARHTTPServer(config, service).serve_forever()
