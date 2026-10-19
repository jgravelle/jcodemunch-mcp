"""The savings meter's client says what its receiver does; this holds the two together.

`storage/token_tracker.py` and SECURITY.md say the client sends exactly
`{delta, total, anon_id}`, and that the server keeps GREATEST(stored, total),
with `delta` only for older builds. Until 2026-10-19 the receiver lived only on
the web host, and the one local copy (outside any repository) was an older,
delta-adding build that contradicted the client's description. The deployed
code is now `server/savings/post.php`. These tests read it as text: PHP is not
run here. The file is not shipped in the sdist, so the module skips outside a
source checkout; inside one, a missing receiver fails rather than skips, so
deleting the file cannot turn this guard into a skip.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RECEIVER = REPO / "server" / "savings" / "post.php"
CLIENT = REPO / "src" / "jcodemunch_mcp" / "storage" / "token_tracker.py"

if not (REPO / ".git").exists():
    pytest.skip(
        "not a source checkout: server/ is not shipped in the sdist",
        allow_module_level=True,
    )


def test_the_receiver_is_in_the_checkout():
    assert RECEIVER.is_file(), (
        "server/savings/post.php is gone; the client's description is unchecked"
    )


def _receiver() -> str:
    return RECEIVER.read_text(encoding="utf-8")


def _client_payload_keys() -> set[str]:
    """The keys of the JSON body `_telemetry_worker` posts, read from its AST."""
    tree = ast.parse(CLIENT.read_text(encoding="utf-8"))
    worker = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_telemetry_worker"
    )
    for call in ast.walk(worker):
        if isinstance(call, ast.Call) and getattr(call.func, "attr", None) == "post":
            for kw in call.keywords:
                if kw.arg == "json" and isinstance(kw.value, ast.Dict):
                    return {
                        k.value for k in kw.value.keys if isinstance(k, ast.Constant)
                    }
    raise AssertionError("_telemetry_worker posts no json body")


def test_the_receiver_reads_exactly_the_fields_the_client_sends():
    text = _receiver()
    read = set(re.findall(r"""\$data\[["'](\w+)["']\]""", text))
    assert read == _client_payload_keys() == {"delta", "total", "anon_id"}, read
    # The only other touches of $data are the decode and the is_array guard: no
    # loop over it and no dynamic key.
    others = re.findall(r"""\$data(?!\[["'])""", text)
    assert len(others) == 2, others


def test_a_lifetime_total_is_kept_as_the_larger_of_the_two():
    """The client's claim: the server sets the stored value to GREATEST(stored, total)."""
    text = _receiver()
    total_branch = text.index("if ($total !== null)")
    delta_branch = text.index("elseif ($delta !== null)")
    assert total_branch < delta_branch, "a client sending both must take the total path"
    total_sql = text[total_branch:delta_branch]
    assert "total_tokens = GREATEST(total_tokens, VALUES(total_tokens))" in total_sql
    assert "$value = $total;" in total_sql
    assert "total_tokens + VALUES" not in total_sql


def test_a_delta_is_added_only_when_no_total_is_sent():
    text = _receiver()
    delta_sql = text[
        text.index("elseif ($delta !== null)") : text.index("$config = require")
    ]
    assert "total_tokens = total_tokens + VALUES(total_tokens)" in delta_sql
    assert "$value = $delta;" in delta_sql


def test_the_clients_description_names_what_the_receiver_does():
    """If either side changes, this fails until the description is checked again."""
    doc = ast.get_docstring(ast.parse(CLIENT.read_text(encoding="utf-8"))) or ""
    assert "GREATEST(stored, total)" in doc
    assert "backward" in doc and "delta" in doc
    security = (REPO / "SECURITY.md").read_text(encoding="utf-8")
    assert "server/savings/post.php" in security
    assert "`{delta, total, anon_id}`" in security


def test_the_client_posts_to_this_receiver():
    tree = ast.parse(CLIENT.read_text(encoding="utf-8"))
    urls = [
        n.value.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", None) == "_TELEMETRY_URL" for t in n.targets)
        and isinstance(n.value, ast.Constant)
    ]
    assert len(urls) == 1 and urls[0].endswith("/savings/post.php"), urls


def test_the_checked_in_copy_holds_no_credential_or_host():
    text = _receiver()
    assert re.findall(r"\$config\s*=\s*require\s+\"([^\"]+)\"", text) == [
        "/path/to/private/config.php"
    ]
    assert not re.search(r"""['"]db_(host|name|user|pass)['"]\s*=>""", text), (
        "a config value is inline"
    )
    assert not re.search(r"https?://", text), "a URL names a host"
    assert "gravelle" not in text.lower()
    # The connection takes every value from $config: no literal DSN host and no
    # positional credential.
    assert (
        "$dsn = \"mysql:host={$config['db_host']};dbname={$config['db_name']};" in text
    )
    assert 'new PDO($dsn, $config["db_user"], $config["db_pass"]' in text
    assert len(re.findall(r"new PDO\(", text)) == 1
