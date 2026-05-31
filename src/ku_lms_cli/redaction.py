"""Redaction helpers for secrets and LMS-private artifacts."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import is_dataclass, asdict
from typing import Any

REDACTION = "[REDACTED]"
SENSITIVE_KEY_PATTERNS = (
    "consumer_key",
    "pwd",
    "password",
    "pass",
    "secret",
    "token",
    "cookie",
    "session",
    "authorization",
    "auth",
    "credential",
    "secure_params",
    "oauth",
    "signature",
    "nonce",
    "saml",
    "relaystate",
    "email",
    "e_mail",
    "mail",
    "lis_person",
    "custom_user",
    "user_login",
    "sourcedid",
    "person_name",
)
_VALUE_PATTERNS = [
    re.compile(r"(?i)(KU_LMS_(?:ID|PWD)\s*=\s*)([^\s#]+)"),
    re.compile(r"(?i)()([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})"),
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([A-Za-z0-9._~+/=-]+)"),
    re.compile(r"(?i)((?:access|refresh|id)?_?token\s*[:=]\s*)([A-Za-z0-9._~+/=-]{8,})"),
    re.compile(r"(?i)(cookie\s*[:=]\s*)([^\n;]+(?:;[^\n]+)?)"),
    re.compile(r"(?i)((?:session|sess)[-_]?(?:id|key)?\s*[:=]\s*)([A-Za-z0-9._~+/=-]{8,})"),
    re.compile(r"(?i)([?&](?:access[_-]?token|refresh[_-]?token|id[_-]?token|token|password|pwd|session|sid|SAMLResponse|SAMLRequest|RelayState|oauth[_-]?(?:signature|nonce|token|consumer[_-]?key))=)([^&#\s]+)"),
    re.compile(r"(?<![A-Za-z])\d{8,}(?![A-Za-z])"),
]


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(pattern in normalized for pattern in SENSITIVE_KEY_PATTERNS)


def redact_text(text: object, extra_values: Sequence[str] | None = None) -> str:
    """Return a string with common credentials/tokens and explicit values removed."""
    result = str(text)
    for pattern in _VALUE_PATTERNS:
        if pattern.groups >= 2:
            result = pattern.sub(lambda m: f"{m.group(1)}{REDACTION}", result)
        else:
            result = pattern.sub(REDACTION, result)
    for value in extra_values or ():
        if value:
            result = result.replace(str(value), REDACTION)
    return result


def redact_data(value: Any, extra_values: Sequence[str] | None = None) -> Any:
    """Recursively redact mappings/sequences while preserving shape."""
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        redacted = {}
        for key, child in value.items():
            key_s = str(key)
            redacted[key] = REDACTION if is_sensitive_key(key_s) else redact_data(child, extra_values)
        return redacted
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_data(child, extra_values) for child in value]
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return redact_text(value, extra_values)
    return value
