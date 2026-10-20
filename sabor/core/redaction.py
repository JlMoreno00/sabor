"""Redaction for diagnostic text and optional error details."""
import re

_SECRET = r"(?:authorization|api_key|token|password)"
_PATTERNS = (
    (r'(?im)(\bauthorization\s*:\s*)[^\r\n]+', r'\1[REDACTED]'),
    (r'(?i)\bbearer\s+[^\s",;\x27}]+', "Bearer [REDACTED]"),
    (rf'(?i)(["\x27]?{_SECRET}["\x27]?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|\x27[^\x27]*\x27|[^\s&,;}}]+)', r'\1[REDACTED]'),
    (r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[REDACTED]"),
    (r"(https?://[^\s?]+)\?[^\s]*", r"\1?[REDACTED]"),
)


def redact(text):
    """Remove credentials, email addresses, and URL queries from text."""
    for pattern, replacement in _PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


def sanitize(value):
    """Copy JSON-like details, dropping raw transport/model material."""
    if isinstance(value, dict):
        return {redact(str(k)): sanitize(v) for k, v in value.items()
                if str(k).lower() not in {
                    "authorization", "api_key", "token", "password", "headers",
                    "body", "response", "exception", "model_output"}}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[REDACTED]"
