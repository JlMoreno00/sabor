"""Central mappings deliberately never inspect upstream bodies or exception text."""
import requests

from .envelope import fail

_MESSAGES = {
    "authentication_failed": "Provider authentication failed.",
    "not_found": "Requested resource was not found.",
    "conflict": "The operation conflicts with the current state.",
    "invalid_argument": "Invalid request arguments.",
    "rate_limited": "Provider rate limit reached.",
    "timeout": "Provider request timed out.",
    "upstream_error": "Provider request failed.",
    "invalid_response": "Provider returned an invalid response.",
    "partial_failure": "Some operations failed.",
}


def from_http(status_code, provider):
    """Map an unsuccessful HTTP status; successful statuses are caller errors."""
    if 200 <= status_code < 300:
        raise ValueError("Expected an unsuccessful HTTP status.")
    code = {401: "authentication_failed", 403: "authentication_failed",
            404: "not_found", 409: "conflict", 400: "invalid_argument",
            422: "invalid_argument", 429: "rate_limited"}.get(
                status_code, "upstream_error")
    return fail(code, _MESSAGES[code], provider)


def from_exception(exc, provider):
    if isinstance(exc, requests.exceptions.Timeout):
        code = "timeout"
    elif isinstance(exc, requests.exceptions.ConnectionError):
        code = "upstream_error"
    elif isinstance(exc, ValueError):
        code = "invalid_response"
    else:
        code = "upstream_error"
    return fail(code, _MESSAGES[code], provider)


def partial(completed, failed, provider):
    return fail("partial_failure", _MESSAGES["partial_failure"], provider,
                {"completed": list(completed), "failed": list(failed)})
