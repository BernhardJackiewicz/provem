"""The DSAR pull listener: intake without a single line of ITSM-side code.

The push direction (a ticket system calling the REST gateway) needs an
integration built inside that ticket system. This module is the pull
direction, and it exists because most ITSM platforms already know how to
emit a request without any custom development: ServiceNow sends a native
outbound notification email, or a workflow drops a record on a Kafka
topic. The listener picks those up, parses whatever the channel happens to
speak into the one canonical :class:`~cognitive_memory.dsar.DSARRequest`
shape, and drives the full plan, execute, verify loop per message. The
external system is left untouched.

Three layers, each replaceable on its own:

* Parsers turn a raw message into a plain payload dict.
  :func:`parse_email_request` reads the ITSM notification mail,
  :func:`parse_kafka_message` reads a JSON record, and
  :func:`default_transform` normalizes the ServiceNow field names
  (``sys_id``, ``number``) onto ours. A deployment with different field
  names passes its own ``transform`` instead of forking the listener.
* Sources are duck-typed: anything with ``poll() -> List[Tuple[str, bytes]]``
  works. :class:`FileDropSource` reads a maildrop or export folder,
  :class:`KafkaSource` wraps an already-configured consumer object. No
  Kafka client is imported here; the consumer is injected, which keeps this
  module pure stdlib and keeps the broker choice out of the core.
* :class:`DSARListener` is the loop that ties a service to its sources.

Reference quality, stated plainly rather than implied. Delivery is
at-least-once: a crash between reading a message and finishing it will see
that message again. Nothing here dedupes, because it does not have to:
:meth:`DSARService.execute` is idempotent per ``request_id``, so a
redelivery of a request that carries a stable id replays the recorded
outcome instead of erasing twice. Processing is serial and errors are
isolated per message and per source, so one unparsable file cannot stall
the queue behind it. There is no retry, no backoff and no dead-letter
queue: a failed message is reported in the result list and dropped. A
deployment that needs delivery guarantees beyond this should put a real
broker in front of the listener rather than grow one inside it.

Pure stdlib.
"""

from __future__ import annotations

import email
import json
import os
import time
from email.message import Message
from email.utils import parseaddr
from typing import Any, Callable, Dict, List, Optional, Tuple

from .dsar import KINDS, DSARRequest

# Re-exported so an integrator has one import for the whole intake path:
# the parsers, the sources, the loop, and the request model plus the kind
# vocabulary they all validate against.
__all__ = [
    "KINDS",
    "DSARRequest",
    "DSARListener",
    "FileDropSource",
    "KafkaSource",
    "default_transform",
    "parse_email_request",
    "parse_kafka_message",
]

# Body keys the mail parser accepts. Anything else in the notification body
# (signatures, disclaimers, vendor boilerplate) is ignored rather than
# carried along, so the payload stays the request and nothing else.
_BODY_KEYS = frozenset(
    {"tenant", "term", "subject", "purpose", "requester", "request_id"}
)

# ServiceNow's own field names, mapped onto ours. Only these two: sys_id is
# the stable record id that makes redelivery idempotent, number is the
# human-facing ticket reference that closes the loop back to the caller.
_SERVICENOW_ALIASES = (("sys_id", "request_id"), ("number", "ticket"))

# File extensions the drop folder consumes. Everything else is somebody
# else's file and is never read, never moved, never delivered.
_DROP_SUFFIXES = (".json", ".eml")

# Subdirectory a consumed file is moved into.
PROCESSED_DIR = "processed"

# Fallback source name for a consumer whose records carry no topic.
_DEFAULT_TOPIC = "kafka"


# -- parsers ----------------------------------------------------------------


def parse_email_request(raw: Any) -> Dict[str, str]:
    """Parse an ITSM notification mail into a DSAR payload dict.

    The subject line is the contract: ``DSAR <kind> [<ticket>]``, with the
    leading marker read case-insensitively because mail clients and ITSM
    templates disagree about capitalization. A subject that does not have
    that form is rejected, which is also how non-mail input is rejected:
    a binary blob parses into a message with no subject at all, and a
    message with no subject cannot be a request.

    The body carries the rest as ``key: value`` lines, read from the first
    ``text/plain`` part so an HTML notification with a plain-text
    alternative works without the markup leaking into the payload. Only
    the known keys are read. If the body names no requester, the ``From``
    address stands in: the person the ITSM system sent the mail as is the
    best attribution available, and :class:`DSARRequest` refuses a request
    that cannot be attributed to anyone.

    Returns a payload dict, not a request: validation belongs to
    :meth:`DSARRequest.from_dict`, once, for every channel.
    """

    if isinstance(raw, (bytes, bytearray)):
        message = email.message_from_bytes(bytes(raw))
    else:
        message = email.message_from_string(str(raw))

    payload = _subject_fields(message.get("Subject"))
    payload.update(_body_fields(_text_plain_body(message)))

    if not payload.get("requester"):
        sender = parseaddr(message.get("From", ""))[1].strip()
        if sender:
            payload["requester"] = sender
    return payload


def _subject_fields(subject: Any) -> Dict[str, str]:
    """Split ``DSAR <kind> [<ticket>]`` into its payload fields."""

    words = str(subject or "").split()
    if len(words) < 2 or words[0].lower() != "dsar":
        raise ValueError(
            "DSAR mail subject must read 'DSAR <kind> [<ticket>]', got %r" % (subject,)
        )
    fields = {"kind": words[1]}
    # Absent rather than empty when the subject carries no reference: an
    # empty ticket string would read as "ticket zero" in the audit trail.
    if len(words) > 2:
        fields["ticket"] = words[2]
    return fields


def _text_plain_body(message: Message) -> str:
    """Return the first ``text/plain`` part of a mail, decoded leniently.

    ``walk`` covers both shapes with one loop: a single-part mail yields
    itself, a multipart yields its container and then the alternatives.
    Decoding replaces undecodable bytes instead of raising, because a
    mangled character in a notification body should not cost the request.
    """

    for part in message.walk():
        if part.get_content_type() != "text/plain":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        return payload.decode("utf-8", errors="replace")
    return ""


def _body_fields(body: str) -> Dict[str, str]:
    """Read the known ``key: value`` lines out of a notification body."""

    fields: Dict[str, str] = {}
    for line in body.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        name = key.strip().lower()
        # Only the first colon separates: a value may contain more of them
        # (an address with a port, a purpose with a description).
        if name in _BODY_KEYS:
            fields[name] = value.strip()
    return fields


def parse_kafka_message(raw: Any) -> Dict[str, Any]:
    """Parse a JSON record from a topic into a DSAR payload dict.

    Accepts bytes (the usual wire shape) or an already-decoded string.
    Anything that is not a JSON object is a ``ValueError``: a bare array or
    scalar cannot describe a request, and failing here keeps the loop's
    per-message error isolation in charge of it.
    """

    if isinstance(raw, (bytes, bytearray)):
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("DSAR message is not valid utf-8: %s" % exc)
    else:
        text = str(raw)

    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise ValueError("DSAR message is not valid JSON: %s" % exc)

    if not isinstance(payload, dict):
        raise ValueError(
            "DSAR message must be a JSON object, got %s" % type(payload).__name__
        )
    return payload


def default_transform(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a ServiceNow-shaped payload onto the DSAR field names.

    Two aliases only, and both defer: an explicit ``request_id`` or
    ``ticket`` in the payload always wins, because a channel that names
    the field we expect means it. The input dict is copied, never mutated,
    so a caller can keep the raw message for its own audit.
    """

    mapped = dict(payload)
    for alias, target in _SERVICENOW_ALIASES:
        if str(mapped.get(target) or "").strip():
            continue
        value = mapped.get(alias)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            mapped[target] = text
    return mapped


# -- sources ----------------------------------------------------------------


class FileDropSource:
    """Consumes DSAR messages from a drop folder, once each.

    The folder is what an ITSM mail rule or an export job writes into:
    ``.eml`` files for notification mails, ``.json`` files for exported
    records. Any other extension is left strictly alone, so the folder can
    be shared with whatever else writes there.

    A file is consumed by moving it into ``processed/`` with
    :func:`os.replace`, which is atomic on the same filesystem: a file is
    either still pending or already moved, never half of both. That makes
    the source at-least-once rather than exactly-once. A crash between the
    move and the end of processing loses the message; a crash between the
    read and the move delivers it a second time on the next poll. The
    second is the failure mode worth having, and it is harmless in
    practice because :meth:`DSARService.execute` is idempotent per
    ``request_id``: a redelivered request replays its recorded outcome.

    A redelivered file overwrites its earlier copy in ``processed/``. The
    audit trail, not this folder, is the record of what happened.
    """

    def __init__(self, directory: str) -> None:
        if not os.path.isdir(directory):
            raise ValueError("DSAR drop directory %r does not exist" % (directory,))
        self.directory = directory
        self.processed_directory = os.path.join(directory, PROCESSED_DIR)

    def poll(self) -> List[Tuple[str, bytes]]:
        """Return and consume the pending messages, in filename order.

        Sorted so a run is reproducible and so a naming convention that
        encodes arrival order is honoured.
        """

        items: List[Tuple[str, bytes]] = []
        for name in sorted(os.listdir(self.directory)):
            if not name.lower().endswith(_DROP_SUFFIXES):
                continue
            path = os.path.join(self.directory, name)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as handle:
                raw = handle.read()
            # Created on demand: an empty folder that is never written to
            # should not grow a subdirectory just for being polled.
            os.makedirs(self.processed_directory, exist_ok=True)
            os.replace(path, os.path.join(self.processed_directory, name))
            items.append((name, raw))
        return items


class KafkaSource:
    """Adapts an already-configured Kafka consumer to the source protocol.

    The consumer is injected and duck-typed: this module never imports a
    broker client, so the dependency (and the choice of client) belongs to
    the deployment rather than to the library. Anything with a ``poll()``
    works, including a test double.

    Both common return shapes are accepted, because the clients disagree:
    ``kafka-python`` hands back a dict of partition to record list,
    ``confluent-kafka`` and most wrappers hand back a flat iterable. A
    record may expose ``.value`` and ``.topic`` or simply be the raw value
    itself. Nothing is committed here; offset management stays with the
    consumer that owns it.
    """

    def __init__(self, consumer: Any) -> None:
        self.consumer = consumer

    def poll(self) -> List[Tuple[str, bytes]]:
        """Return one batch of records as ``(topic, bytes)`` pairs."""

        batch = self.consumer.poll()
        if batch is None:
            return []
        if isinstance(batch, dict):
            records = [record for group in batch.values() for record in group]
        else:
            records = list(batch)
        return [self._item(record) for record in records]

    @staticmethod
    def _item(record: Any) -> Tuple[str, bytes]:
        value = getattr(record, "value", record)
        if isinstance(value, (bytes, bytearray)):
            raw = bytes(value)
        else:
            raw = str(value).encode("utf-8")
        # The topic doubles as the message name in the result list, which is
        # what an operator reads when a message fails.
        topic = getattr(record, "topic", "") or _DEFAULT_TOPIC
        return (str(topic), raw)


# -- loop -------------------------------------------------------------------


class DSARListener:
    """Drives plan, execute and verify for every message a source delivers.

    One message is one full loop: the request is planned (the read-only
    effect report, audited before anything is touched), executed, and then
    verified against current state, so the result the operator sees is
    evidence rather than an acknowledgement. Messages are handled serially,
    which keeps the audit trail in a defensible order and keeps two
    deliveries of the same request from racing each other into a double
    erasure.

    Every failure is contained at the smallest scope that still makes
    sense. A message that cannot be parsed, validated or executed becomes
    an ``error`` entry and the next message runs. A source whose ``poll``
    raises (a broker that went away, a folder that got unmounted) becomes
    an ``error`` entry for the source and the next source runs. Nothing
    propagates out of :meth:`run`, because a listener that dies on one bad
    message is a listener that stops honouring erasure requests.

    ``transform`` and ``sleep_fn`` are injected: the first is the field
    mapping for a deployment whose ITSM names things differently, the
    second is what makes the loop testable without real time passing.
    """

    def __init__(self, service: Any, sources: Any, transform: Optional[Callable] = None,
                 sleep_fn: Optional[Callable] = None) -> None:
        self.service = service
        self.sources = list(sources)
        self.transform = default_transform if transform is None else transform
        self._sleep = time.sleep if sleep_fn is None else sleep_fn

    def process_one(self, name: str, raw: Any) -> Dict[str, Any]:
        """Run one message through the full DSAR loop, never raising.

        The parser is chosen by extension: ``.eml`` is a notification mail,
        everything else is JSON. The result is the compact per-message
        record the caller collects, or ``{"name", "error"}`` when any step
        of the path failed. The error text is kept as-is because the
        failures worth reading here are validation messages naming the
        field that was wrong.
        """

        try:
            if str(name).lower().endswith(".eml"):
                payload = parse_email_request(raw)
            else:
                payload = parse_kafka_message(raw)
            request = DSARRequest.from_dict(self.transform(payload))
            self.service.plan(request)
            executed = self.service.execute(request)
            verify = self.service.verify(request.request_id, request.tenant)
            return {
                "name": name,
                "request_id": request.request_id,
                "kind": request.kind,
                "status": executed.get("status"),
                # A replay is a redelivery that was absorbed, not a second
                # erasure, and it is worth seeing that it happened.
                "replayed": bool(executed.get("replayed")),
                "verify_passed": bool(verify.get("passed")),
            }
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            return {"name": name, "error": str(exc)}

    def run(self, max_iterations: Optional[int] = None,
            interval: float = 1.0) -> List[Dict[str, Any]]:
        """Poll every source repeatedly and return every result collected.

        ``max_iterations`` bounds the loop; ``None`` runs forever, which is
        the deployed shape. The sleep happens between iterations and not
        after the last one, so a bounded run returns as soon as its work is
        done instead of idling once for nothing.
        """

        results: List[Dict[str, Any]] = []
        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            for source in self.sources:
                try:
                    items = source.poll()
                except Exception as exc:  # noqa: BLE001 - isolation is the point
                    results.append(
                        {"source": type(source).__name__, "error": str(exc)}
                    )
                    continue
                for name, raw in items:
                    results.append(self.process_one(name, raw))
            if max_iterations is None or iteration < max_iterations:
                self._sleep(interval)
        return results
