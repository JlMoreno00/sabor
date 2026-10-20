"""Uniform result envelopes."""
from enum import Enum

from .redaction import redact, sanitize


class ErrorCode(str, Enum):
    INVALID_ARGUMENT = "invalid_argument"
    PROVIDER_UNCONFIGURED = "provider_unconfigured"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    NOT_FOUND = "not_found"
    AMBIGUOUS_TARGET = "ambiguous_target"
    AUTHENTICATION_FAILED = "authentication_failed"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UPSTREAM_ERROR = "upstream_error"
    INVALID_RESPONSE = "invalid_response"
    CONFLICT = "conflict"
    PARTIAL_FAILURE = "partial_failure"


def ok(data):
    return {"ok": True, "data": data}


def preview(data):
    return ok({**data, "preview": True})


def fail(code, message, provider="sabor", data=None):
    result = {"ok": False, "error": {
        "code": ErrorCode(code).value, "message": redact(message),
        "provider": redact(provider)}}
    if data is not None:
        result["data"] = sanitize(data)
    return result
