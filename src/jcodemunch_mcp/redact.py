"""Response-level secret redaction for tool output.

Scans string values inside tool-response dicts for credential patterns and
replaces them with ``[REDACTED:<type>]`` placeholders.  Designed to prevent
accidental secret leakage into LLM context windows.

Patterns are deliberately conservative (high-entropy + structural anchors)
to avoid false-positives on code identifiers and documentation.

Controlled by the ``redact_response_secrets`` config key (default: ``true``).
Disable with ``"redact_response_secrets": false`` in config.jsonc or
``JCODEMUNCH_REDACT_RESPONSE_SECRETS=0`` environment variable.
"""

from __future__ import annotations

import logging
import math
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pattern registry: (label, compiled regex)
# ---------------------------------------------------------------------------
# Each pattern must include a named group ``secret`` spanning the value to
# redact.  If no named group, the entire match is redacted.
#
# We anchor on structural prefixes (AKIA, ya29., eyJ, etc.) so we don't
# false-positive on ordinary code tokens.
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, re.Pattern]] = [
    # AWS access key IDs: always start with AKIA and are 20 chars
    ("aws_access_key", re.compile(
        r"(?<![A-Za-z0-9/+])(?P<secret>AKIA[0-9A-Z]{16})(?![A-Za-z0-9/+])"
    )),
    # AWS secret keys: 40-char base64 after a key-like context
    ("aws_secret_key", re.compile(
        r"(?i)(?:aws_secret|secret_access_key|aws_secret_access_key)"
        r"[\s:=\"']+(?P<secret>[A-Za-z0-9/+=]{40})"
    )),
    # GCP service account emails
    ("gcp_service_account", re.compile(
        r"(?P<secret>[a-z][a-z0-9\-]{4,28}[a-z0-9]@[a-z0-9\-]+\.iam\.gserviceaccount\.com)"
    )),
    # Azure storage account keys (base64, 88 chars with == padding)
    ("azure_storage_key", re.compile(
        r"(?i)(?:account_?key|storage_?key|azure_?key)"
        r"[\s:=\"']+(?P<secret>[A-Za-z0-9/+=]{86,90}==)"
    )),
    # Azure client secret / tenant patterns
    ("azure_client_secret", re.compile(
        r"(?i)(?:client_?secret|azure_?secret)"
        r"[\s:=\"']+(?P<secret>[A-Za-z0-9~._\-]{34,})"
    )),
    # JWT tokens (three dot-separated base64url segments)
    ("jwt", re.compile(
        r"(?P<secret>eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_\-]{10,})"
    )),
    # Bearer tokens in Authorization-header form.
    # Require the `Authorization` header prefix — matching a bare `Bearer`
    # produced false positives on prose/docstrings containing the word
    # (audit finding F9).
    ("bearer_token", re.compile(
        r"(?i:authorization)\s*[:=]\s*Bearer\s+(?P<secret>[A-Za-z0-9_\-\.]{20,})"
    )),
    # GitHub personal access tokens (ghp_, gho_, ghu_, ghs_, ghr_)
    ("github_token", re.compile(
        r"(?P<secret>gh[pousr]_[A-Za-z0-9_]{36,})"
    )),
    # GitHub fine-grained PATs (github_pat_...). The classic gh[pousr]_ pattern
    # above does NOT match these — different prefix shape.
    ("github_fine_grained_pat", re.compile(
        r"(?P<secret>github_pat_[A-Za-z0-9_]{20,})"
    )),
    # Anthropic API keys (sk-ant-...). Listed BEFORE the OpenAI pattern so the
    # ant- form is labelled + redacted first; the OpenAI bare branch then can't
    # re-scan it.
    ("anthropic_api_key", re.compile(
        r"(?P<secret>sk-ant-[A-Za-z0-9_-]{20,})"
    )),
    # OpenAI API keys: project keys (sk-proj-...) and legacy bare keys (sk-...).
    # The bare branch forbids a hyphen so it can't swallow an sk-ant- key.
    ("openai_api_key", re.compile(
        r"(?P<secret>sk-(?:proj-[A-Za-z0-9_-]{20,}|[A-Za-z0-9]{20,}))"
    )),
    # Slack tokens
    ("slack_token", re.compile(
        r"(?P<secret>xox[bpasor]-[A-Za-z0-9\-]{10,})"
    )),
    # Private key blocks (PEM)
    ("private_key", re.compile(
        r"(?P<secret>-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----)"
    )),
    # Generic high-entropy API key patterns (key=... or api_key=...)
    # Requires 32+ chars of high-entropy content to reduce false positives
    ("generic_api_key", re.compile(
        r"(?i)(?:api_?key|apikey|secret_?key|auth_?token|access_?token)"
        r"[\s:=\"']+(?P<secret>[A-Za-z0-9_\-]{32,})"
    )),
    # Private IPv4 ranges (10.x, 172.16-31.x, 192.168.x) with port
    ("private_ipv4", re.compile(
        r"(?<![0-9.])(?P<secret>"
        r"(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3})"
        r"|(?:172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})"
        r"|(?:192\.168\.\d{1,3}\.\d{1,3})"
        r")(?::\d{1,5})?(?![0-9.])"
    )),
]


# Minimum Shannon entropy (bits per char) for the generic_api_key secret
# candidate. Real high-entropy tokens sit well above 3.5; typical code
# identifiers (snake_case_value_names) fall below.
_MIN_ENTROPY_BITS = 3.5


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def _redact_string(text: str) -> tuple[str, int]:
    """Scan a single string for secrets and return (redacted_text, count).

    Returns the original string unchanged if no patterns match.
    """
    count = 0
    for label, pattern in _PATTERNS:
        def _replacer(m: re.Match, _label: str = label) -> str:
            nonlocal count
            # For generic_api_key, avoid scrubbing identifier-shaped values
            # like `api_key: DEFAULT_CONFIG_IDENTIFIER_CONSTANT`. Real secrets
            # almost always mix cases and digits; SCREAMING_CASE and
            # snake_case identifiers do not. Require all three character
            # classes AND high entropy.
            if _label == "generic_api_key":
                try:
                    candidate = m.group("secret")
                    has_lower = any(c.islower() for c in candidate)
                    has_upper = any(c.isupper() for c in candidate)
                    has_digit = any(c.isdigit() for c in candidate)
                    if not (has_lower and has_upper and has_digit):
                        return m.group()
                    if _shannon_entropy(candidate) < _MIN_ENTROPY_BITS:
                        return m.group()
                except IndexError:
                    pass
            count += 1
            try:
                secret = m.group("secret")
                start = m.start("secret") - m.start()
                prefix = m.group()[:start]
                suffix = m.group()[start + len(secret):]
                return f"{prefix}[REDACTED:{_label}]{suffix}"
            except IndexError:
                return f"[REDACTED:{_label}]"

        text = pattern.sub(_replacer, text)
    return text, count


def redact_text(text: str) -> str:
    """Scrub secrets from ONE string and return it; the public single-string form.

    ``redact_dict`` is the response chokepoint. A producer that builds a
    string from an exception message before the response exists (a provider
    echoes the request, key included, into its error text) needs the same
    patterns at the point the string is made, and the private helper had no
    second consumer until then.
    """
    return _redact_string(text)[0]


def redact_dict(data: Any, _depth: int = 0) -> tuple[Any, int]:
    """Recursively redact secrets in a dict/list structure.

    Only scans string values; keys are never modified.
    Returns (redacted_data, total_redaction_count).

    Caps recursion at depth 20 to prevent pathological nesting.
    """
    if _depth > 20:
        # Don't return raw data past the cap — that would leak secrets we were
        # asked to scrub. Collapse every scalar/container to a sentinel; the
        # prior 16-char length gate let short high-signal prefixes (e.g. an
        # AWS access-key prefix) slip through (audit finding F10).
        if isinstance(data, (str, dict, list)):
            return "[REDACTED:depth_exceeded]", 1
        return data, 0

    total = 0

    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            # Inside _meta, bypass only scalar numeric/bool fields (timings,
            # counters). Strings and nested containers still get scanned —
            # secrets can land in _meta.hint, _meta.error, etc.
            # `source` and `content` carry raw code payloads from
            # get_symbol_source / get_file_content. The bearer_token pattern
            # is now header-anchored and generic_api_key is entropy-gated, so
            # normal code traverses cleanly; we still scan them for the
            # anchored high-confidence patterns (AWS keys, PEM blocks, etc.).
            if key == "_meta" and isinstance(value, dict):
                meta_result: dict = {}
                for mk, mv in value.items():
                    if isinstance(mv, (int, float, bool)) or mv is None:
                        meta_result[mk] = mv
                    else:
                        redacted, count = redact_dict(mv, _depth + 1)
                        meta_result[mk] = redacted
                        total += count
                result[key] = meta_result
                continue
            redacted, count = redact_dict(value, _depth + 1)
            result[key] = redacted
            total += count
        return result, total

    if isinstance(data, list):
        result_list = []
        for item in data:
            redacted, count = redact_dict(item, _depth + 1)
            result_list.append(redacted)
            total += count
        return result_list, total

    if isinstance(data, str) and len(data) >= 16:
        # Only scan strings long enough to plausibly contain a secret
        redacted_str, count = _redact_string(data)
        return redacted_str, count

    return data, 0


def is_redaction_enabled() -> bool:
    """Check whether response secret redaction is enabled.

    Controlled by config key ``redact_response_secrets`` (default: True)
    or env var ``JCODEMUNCH_REDACT_RESPONSE_SECRETS`` (set "0" to disable).
    """
    env = os.environ.get("JCODEMUNCH_REDACT_RESPONSE_SECRETS")
    if env is not None:
        return env not in ("0", "false", "no", "off")

    try:
        from . import config as _cfg
        val = _cfg.get("redact_response_secrets", True)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() not in ("0", "false", "no", "off")
        return bool(val)
    except Exception:
        return True  # safe default
