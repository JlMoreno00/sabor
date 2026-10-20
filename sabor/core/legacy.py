"""Registration-only envelopes; direct legacy callers retain their old contract."""
import re
from functools import wraps
from inspect import signature

from . import errors
from .envelope import ErrorCode, fail, ok
from .redaction import sanitize

_MESSAGES = {
    **errors._MESSAGES,
    "provider_unconfigured": "Provider is not configured.",
    "unsupported_operation": "The operation is not supported.",
    "ambiguous_target": "The requested target is ambiguous.",
}
# Error diagnostics are deliberately an allowlist, not arbitrary upstream data.
# Only boolean gates/retryability and numeric transport metadata are retained.
_BOOLEAN_FIELDS = {"retryable", "apply", "publish", "execute", "preview"}
_NUMBER_FIELDS = {"status", "status_code", "retryAfter"}
# Sabor-generated domain identifiers (for example nutrition failure codes and
# stages) explain why a gate refused; plain identifiers cannot carry upstream text.
_IDENTIFIER = re.compile(r"^[a-z0-9_]{1,64}$")


def _result(value, provider):
    if not isinstance(value, dict) or value.get("ok") is not False:
        return ok({k: v for k, v in value.items() if k != "ok"}
                  if isinstance(value, dict) else value)
    status = value.get("status", value.get("status_code"))
    code = {401: "authentication_failed", 403: "authentication_failed",
            404: "not_found", 429: "rate_limited"}.get(status if type(status) is int else 0)
    if code is None:
        candidate = value.get("code")
        code = candidate if candidate in {item.value for item in ErrorCode} else "upstream_error"
    data = {k: v for k, v in value.items()
            if (k in _BOOLEAN_FIELDS and isinstance(v, bool))
            or (k in _NUMBER_FIELDS and type(v) in (int, float))}
    for source, target in (("code", "legacy_code"), ("stage", "stage")):
        if isinstance(value.get(source), str) and _IDENTIFIER.match(value[source]):
            data[target] = value[source]
    return fail(code, _MESSAGES[code], provider, data=sanitize(data))


def legacy_envelope(fn, provider):
    """Wrap at registration without changing tool arguments or descriptions."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return _result(fn(*args, **kwargs), provider)
        except Exception as exc:
            return errors.from_exception(exc, provider)

    setattr(wrapped, "__signature__", signature(fn))
    return wrapped
