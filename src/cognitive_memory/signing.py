"""Detached HMAC signatures for erasure certificates (DSAR evidence).

A certificate is signed over its canonical JSON form and the signature is
carried next to it, never inside it: the certificate bytes stay byte-identical
to the unsigned artifact, so an auditor can re-serialize the certificate and
re-check the signature without stripping fields first.

:func:`canonical_json` reproduces the audit chain's serialization by value
(sorted keys, no ASCII escaping, ``str`` fallback). The parity is pinned by
test rather than by import, so a drift in either module fails loudly instead
of silently invalidating past signatures.

Pure stdlib. HMAC-SHA256 with a shared secret is a deliberate floor, not a
PKI: it proves the certificate was issued by a holder of the key, not by a
named individual.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from typing import Any, Dict, Union

ALGORITHM = "HMAC-SHA256"

Secret = Union[str, bytes]


def canonical_json(obj: Any) -> str:
    """Serialize to the canonical form used for hashing and signing."""

    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def _secret_bytes(secret: Secret) -> bytes:
    if isinstance(secret, bytes):
        return secret
    return str(secret).encode("utf-8")


class HmacSigner:
    """Sign payload bytes with a shared secret under a named key id."""

    def __init__(self, secret: Secret, key_id: str = "default") -> None:
        self._secret = _secret_bytes(secret)
        self.key_id = key_id

    def sign(self, payload: bytes) -> Dict[str, str]:
        digest = hmac.new(self._secret, payload, hashlib.sha256).hexdigest()
        return {
            "algorithm": ALGORITHM,
            "key_id": self.key_id,
            "signature": digest,
        }


def verify_signature(payload: bytes, signature: Dict[str, Any], secret: Secret) -> bool:
    """Return True only for an intact HMAC-SHA256 signature over payload.

    A malformed or foreign-algorithm signature is a verification failure, not
    an exception: verification runs on untrusted input and must stay total.
    """

    if not isinstance(signature, dict):
        return False
    if signature.get("algorithm") != ALGORITHM:
        return False
    claimed = signature.get("signature")
    if not isinstance(claimed, str):
        return False
    expected = hmac.new(_secret_bytes(secret), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(claimed, expected)


def sign_certificate(certificate: Dict[str, Any], signer: Any) -> Dict[str, Any]:
    """Wrap a certificate with a detached signature over its canonical form.

    The input is never mutated and the returned certificate is a detached
    copy, so a later edit of the caller's dict cannot desynchronize the
    wrapper from the signature that was issued over it. ``signer`` is
    duck-typed: any object exposing ``sign(bytes) -> dict`` is accepted.
    """

    payload = canonical_json(certificate).encode("utf-8")
    return {
        "certificate": copy.deepcopy(certificate),
        "signature": signer.sign(payload),
    }
