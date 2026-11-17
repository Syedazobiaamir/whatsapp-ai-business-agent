"""Webhook signature check: proves a POST really came from Meta.

Meta computes HMAC-SHA256(app_secret, raw_request_body) and sends it as
    X-Hub-Signature-256: sha256=<hex digest>
We compute the same value and compare. An attacker without the app secret cannot
produce a valid signature, so fake "customer messages" are rejected.
"""
import hashlib
import hmac

PREFIX = "sha256="


def compute_signature(raw_body: bytes, app_secret: str) -> str:
    digest = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return PREFIX + digest


def is_valid_signature(raw_body: bytes, header_value: str | None, app_secret: str) -> bool:
    if not app_secret or not header_value or not header_value.startswith(PREFIX):
        return False
    expected = compute_signature(raw_body, app_secret)
    # compare_digest takes the same time whether the first or last character differs,
    # so an attacker cannot guess the signature character by character (timing attack).
    return hmac.compare_digest(expected, header_value.strip())
