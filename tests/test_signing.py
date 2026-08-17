"""Acceptance tests for the DSAR signing primitives (stdlib HMAC).

The signature is a detached wrapper around an erasure certificate: the
certificate dict stays identical to the unsigned form, the signature
travels next to it. Parity with the audit chain's canonical JSON form is
pinned by value (no private import), so any drift between the two
serializations fails loudly here.
"""

import copy
import hashlib
import hmac
import json
import unittest

from cognitive_memory.signing import (
    HmacSigner,
    canonical_json,
    sign_certificate,
    verify_signature,
)


class _StrOnly:
    def __str__(self):
        return "str-only-object"


class CanonicalJsonTests(unittest.TestCase):
    def test_matches_audit_canonical_form_by_value(self):
        details = {"b": 1, "a": [1, 2], "text": "für", "obj": _StrOnly()}
        expected = json.dumps(details, sort_keys=True, ensure_ascii=False, default=str)
        self.assertEqual(canonical_json(details), expected)

    def test_is_independent_of_key_insertion_order(self):
        one = {"alpha": 1, "beta": {"y": 2, "x": 3}}
        two = {"beta": {"x": 3, "y": 2}, "alpha": 1}
        self.assertEqual(canonical_json(one), canonical_json(two))


class HmacSignerTests(unittest.TestCase):
    def test_sign_produces_pinned_hmac_sha256_shape(self):
        signer = HmacSigner("secret-key", key_id="k1")
        sig = signer.sign(b"payload")
        expected = hmac.new(b"secret-key", b"payload", hashlib.sha256).hexdigest()
        self.assertEqual(
            sig, {"algorithm": "HMAC-SHA256", "key_id": "k1", "signature": expected}
        )

    def test_sign_is_deterministic_and_payload_sensitive(self):
        signer = HmacSigner("secret-key", key_id="k1")
        self.assertEqual(signer.sign(b"same"), signer.sign(b"same"))
        self.assertNotEqual(
            signer.sign(b"one")["signature"], signer.sign(b"two")["signature"]
        )

    def test_verify_roundtrip_and_tamper_detection(self):
        signer = HmacSigner("secret-key")
        sig = signer.sign(b"certificate-bytes")
        self.assertTrue(verify_signature(b"certificate-bytes", sig, "secret-key"))
        self.assertFalse(verify_signature(b"tampered-bytes", sig, "secret-key"))

    def test_verify_with_wrong_secret_fails(self):
        signer = HmacSigner("secret-key")
        sig = signer.sign(b"payload")
        self.assertFalse(verify_signature(b"payload", sig, "other-secret"))

    def test_verify_unknown_algorithm_is_false_not_error(self):
        signer = HmacSigner("secret-key")
        sig = dict(signer.sign(b"payload"))
        sig["algorithm"] = "RSA-PSS"
        self.assertFalse(verify_signature(b"payload", sig, "secret-key"))


class SignCertificateTests(unittest.TestCase):
    def test_wraps_certificate_without_mutation_and_accepts_duck_signer(self):
        calls = []

        class RecordingSigner:
            def sign(self, payload):
                calls.append(payload)
                return {"algorithm": "TEST", "key_id": "duck", "signature": "s"}

        certificate = {
            "seq": 3,
            "action": "erasure",
            "details": {"term": "alice", "removed_ids": ["r1"]},
        }
        before = copy.deepcopy(certificate)
        wrapped = sign_certificate(certificate, RecordingSigner())
        self.assertEqual(certificate, before)
        self.assertEqual(wrapped["certificate"], before)
        self.assertEqual(wrapped["signature"]["key_id"], "duck")
        self.assertEqual(calls, [canonical_json(certificate).encode("utf-8")])
        hmac_wrapped = sign_certificate(certificate, HmacSigner("secret-key"))
        self.assertTrue(
            verify_signature(
                canonical_json(certificate).encode("utf-8"),
                hmac_wrapped["signature"],
                "secret-key",
            )
        )


if __name__ == "__main__":
    unittest.main()
