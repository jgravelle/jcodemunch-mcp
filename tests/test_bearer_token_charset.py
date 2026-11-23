"""#1558 (@goranai): a Bearer token holding ``~ + / =`` or ``!`` is redacted whole.

RFC 6750 section 2.1 allows ``~ + /`` and trailing ``=`` in a bearer token
(``b64token``). A Salesforce access token is ``<15-char org id>!<rest>``, and
Salesforce's cURL docs write the ``!`` as ``\\!`` inside double quotes. The old
pattern stopped at the first character outside ``[A-Za-z0-9_\\-.]``, so it
either redacted only the head or, when the head was under 20 characters,
nothing at all.
"""
import pytest

from jcodemunch_mcp.redact import redact_text

# A Salesforce-shaped token, assembled at import so no whole token sits in the
# source: GitHub push protection reads any committed Salesforce token as live.
# The shape is the docs example's: a 15-character org id, a bang, the rest.
SF_ORG = "00D5" + "0000000IehZ"
SF_REST = "AQcAQH0dMHZf" + "z972Szmpkb58urFRkgeBGsxL" + "_QJWwYMfAbUeeG7c1E6LYUfiDUkWe6H34r1AAwOR8B8fLEz6n04NPGRrq0FM"
SF_ESCAPED = (
    "curl https://MyDomainName.my.salesforce.com/services/data/v65.0/ "
    '-H "Authorization: Bearer ' + SF_ORG + "\\!" + SF_REST + '"'
)
SF_PLAIN = "-H 'Authorization: Bearer " + SF_ORG + "!" + SF_REST + "'"
SF_JSON_ESCAPED = "Authorization: Bearer " + SF_ORG + "\\\\!" + SF_REST

CASES = {
    "salesforce_escaped_bang": (SF_ESCAPED, "AQcAQH0dMHZf"),
    "salesforce_plain_bang": (SF_PLAIN, "AQcAQH0dMHZf"),
    # The shell line stored JSON-escaped: the backslash before `!` is doubled.
    "salesforce_json_escaped_bang": (SF_JSON_ESCAPED, "AQcAQH0dMHZf"),
    "plus_in_head": ("Authorization: Bearer abc+defghij1234567890klmnopqrstuvwxyz", "defghij"),
    "plus_slash_padding": ("Authorization: Bearer abcdefghij1234567890+klmnopqrstuvwxyz/AAAAAAAAAA==", "klmnop"),
    "slash_early": ("Authorization: Bearer abc/defghij1234567890klmnopqrstuvwxyz0123456789", "defghij"),
    "tilde": ("Authorization: Bearer 8f3k~d92jD0aLk29Dk3jd02kdJ39dk2lD0aLk29Dk3", "d92jD0aL"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_whole_token_is_redacted(name):
    text, fragment = CASES[name]
    out = redact_text(text)
    assert "[REDACTED:bearer_token]" in out, out
    assert fragment not in out, out
    # Nothing of the token survives after the marker: only the closing quote, if any.
    tail = out.split("[REDACTED:bearer_token]", 1)[1]
    assert tail in ("", '"', "'"), repr(tail)


def test_the_closing_quote_and_following_text_are_kept():
    out = redact_text('-H "Authorization: Bearer abcdefghij1234567890+klmnop==" -d x')
    assert out.endswith('[REDACTED:bearer_token]" -d x'), out


def test_a_json_escaped_closing_quote_is_kept():
    # In a JSON string the header value closes with an escaped quote; `\` is not a token character.
    out = redact_text('{"cmd": "curl -H \\"Authorization: Bearer abcdefghij1234567890+klmnop==\\" -d x"}')
    assert '[REDACTED:bearer_token]\\" -d x"}' in out, out


def test_a_placeholder_is_not_redacted():
    # Shell and template placeholders hold none of the token characters at their start.
    for text in ("Authorization: Bearer ${TOKEN}", "Authorization: Bearer <your-token-here-please>",
                 "Authorization: Bearer $ACCESS_TOKEN_FROM_THE_ENVIRONMENT"):
        assert redact_text(text) == text, text


def test_the_runtime_trace_redactor_reads_the_same_pattern():
    # Trace ingest redacts through the same secrets registry, so the fix reaches it too.
    from jcodemunch_mcp.runtime.redact import _redact_string

    out, fired = _redact_string(SF_PLAIN)
    assert "bearer_token" in fired, fired
    assert "AQcAQH0dMHZf" not in out, out


def test_a_short_value_is_not_redacted():
    text = "Authorization: Bearer abc/def+ghi"
    assert redact_text(text) == text
