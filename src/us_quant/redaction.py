from __future__ import annotations

import re


_SENSITIVE_KEY = re.compile(
    r"(?:api[_ -]?key|secret|client[_ -]?secret|access[_ -]?token|"
    r"token|password|authorization)",
    re.IGNORECASE,
)
_NAMED_SECRET = re.compile(
    r"(?i)\b(api[_ -]?key|secret|client[_ -]?secret|"
    r"access[_ -]?token|token|password|authorization)"
    r"(\s*[\"']?\s*[:=]\s*[\"']?)([^\s,;\"'&}]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:api[_-]?key|token|access_token|client_secret)=)"
    r"([^&#\s]+)"
)


def redact_text(value: str) -> str:
    redacted = _BEARER.sub("Bearer [REDACTED]", value)
    redacted = _QUERY_SECRET.sub(
        lambda match: f"{match.group(1)}[REDACTED]",
        redacted,
    )
    return _NAMED_SECRET.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}[REDACTED]"
        ),
        redacted,
    )


def sanitize_value(value):  # type: ignore[no-untyped-def]
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if _SENSITIVE_KEY.search(str(key))
                else sanitize_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_value(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value
