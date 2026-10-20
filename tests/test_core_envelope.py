"""Offline contracts for the shared result boundary."""
import json
import unittest

import requests

from sabor.core.envelope import ErrorCode, fail, ok, preview
from sabor.core.errors import from_exception, from_http, partial
from sabor.core.redaction import redact


class EnvelopeTests(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(ok({"count": 2}), {"ok": True, "data": {"count": 2}})

    def test_preview(self):
        data = {"count": 2}
        self.assertEqual(preview(data), {"ok": True, "data": {"count": 2, "preview": True}})
        self.assertNotIn("preview", data)

    def test_fail(self):
        self.assertEqual(fail("not_found", "Not found."), {
            "ok": False, "error": {"code": "not_found", "message": "Not found.", "provider": "sabor"}})

    def test_optional_data(self):
        result = fail("conflict", "Conflict.", "bring", {"ids": ["one"], "token": "xyz"})
        self.assertEqual(result["data"], {"ids": ["one"]})
        self.assertEqual(result["error"]["provider"], "bring")

    def test_unknown_code(self):
        with self.assertRaises(ValueError):
            fail("unknown", "Unsafe")

    def test_closed_codes(self):
        self.assertEqual({c.value for c in ErrorCode}, set(
            "invalid_argument provider_unconfigured unsupported_operation not_found ambiguous_target "
            "authentication_failed rate_limited timeout upstream_error invalid_response conflict partial_failure".split()))

    def test_secret_body_never_exposed(self):
        secret = 'Bearer abc123, "api_key":"xyz", person@example.com'
        response = requests.Response()
        response.status_code = 500
        response._content = secret.encode()
        exc = requests.HTTPError(secret, response=response)
        for result in (from_http(response.status_code, "mealie"), from_exception(exc, "mealie")):
            serialized = json.dumps(result)
            for value in (secret, "abc123", "xyz", "person@example.com"):
                self.assertNotIn(value, serialized)

    def test_partial(self):
        result = partial(["one"], ["two"], "bring")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "partial_failure")
        self.assertEqual(result["data"], {"completed": ["one"], "failed": ["two"]})

    def test_nested_sanitization(self):
        result = fail("conflict", "Bearer abc123", data={
            "items": [{"password": "xyz", "name": "person@example.com"}],
            "body": "untrusted", "url": "https://example.com/path?secret=xyz"})
        serialized = json.dumps(result)
        for secret in ("abc123", "xyz", "person@example.com", "untrusted", "?secret"):
            self.assertNotIn(secret, serialized)


# Separate generated tests make each mapping/redaction rule independently visible.
def http_case(status, expected):
    def test(self):
        result = from_http(status, "mealie")
        self.assertEqual(result["error"]["code"], expected)
        self.assertEqual(result["error"]["provider"], "mealie")
    return test


for status, code in {400: "invalid_argument", 401: "authentication_failed",
                     403: "authentication_failed", 404: "not_found", 409: "conflict",
                     422: "invalid_argument", 429: "rate_limited", 500: "upstream_error",
                     503: "upstream_error", 302: "upstream_error"}.items():
    setattr(EnvelopeTests, f"test_http_{status}", http_case(status, code))


def exception_case(exc, expected):
    def test(self):
        self.assertEqual(from_exception(exc, "mealie")["error"]["code"], expected)
    return test


for name, exc, code in (
    ("timeout", requests.Timeout("secret"), "timeout"),
    ("connection", requests.ConnectionError("secret"), "upstream_error"),
    ("value", ValueError("secret"), "invalid_response"),
    ("json", json.JSONDecodeError("secret", "", 0), "invalid_response"),
    ("other", RuntimeError("secret"), "upstream_error"),
):
    setattr(EnvelopeTests, f"test_exception_{name}", exception_case(exc, code))


def redaction_case(source, secret):
    def test(self):
        self.assertNotIn(secret, redact(source))
    return test


for name, source, secret in (
    ("bearer", "Bearer abc123", "abc123"),
    ("authorization", "Authorization: Basic abc123", "abc123"),
    ("json_authorization", '{"Authorization":"Basic abc123"}', "abc123"),
    ("query", "https://example.com/?api_key=xyz&next=1", "xyz"),
    ("token", '{"token": "xyz"}', "xyz"),
    ("password", "?password=xyz", "xyz"),
    ("api_key", '{"api_key":"xyz"}', "xyz"),
    ("email", "Contact person@example.com", "person@example.com"),
):
    setattr(EnvelopeTests, f"test_redact_{name}", redaction_case(source, secret))
