"""get_file_tree ships a compact form that decodes to the same answer.

The tree was served as JSON: every node repeated `"path"`, `"type"`,
`"language"` and `"symbol_count"`, and every file repeated its directory,
about 139 characters a file on a real repository. No encoder could carry a
nested tree, so the dispatcher always shipped JSON. The compact form sends
one row per file, with each repeated directory written once, and decodes
back to the same tree. It is a change of encoding only: what a caller reads
after decoding is the response the tool built, key for key.
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp.encoding import encode_response
from jcodemunch_mcp.encoding.decoder import decode

FILES = {
    "README.md": "# demo\n",
    "Makefile": "all:\n\techo hi\n",
    "setup.py": "def setup():\n    return 1\n",
    "src/app/main.py": "def main():\n    return 0\n",
    "src/app/util.py": "def helper():\n    return 1\n\nclass Thing:\n    pass\n",
    "src/app/models/user.py": "class User:\n    pass\n",
    "src/app/models/team.py": "class Team:\n    pass\n",
    "src/lib/strings.ts": "export function pad(s: string) { return s; }\n",
    "src/lib/numbers.ts": "export function add(a: number, b: number) { return a + b; }\n",
    "src/lib/@scoped/pkg.ts": "export const x = 1;\n",
    "src/lib/odd, name.py": "def odd():\n    return 2\n",
    "src/lib/it's here.py": "def quoted():\n    return 3\n",
    "tests/test_main.py": "def test_main():\n    assert True\n",
    "tests/test_util.py": "def test_helper():\n    assert True\n",
    "docs/guide/intro.md": "# intro\n",
    "docs/guide/setup.md": "# setup\n",
}


@pytest.fixture
def tree_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / ".idx"))
    monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", str(tmp_path))
    monkeypatch.setenv("JCODEMUNCH_USE_AI_SUMMARIES", "false")
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    assert index_folder(str(root), use_ai_summaries=False)["success"]
    return resolve_repo(str(root))["repo"]


def _tree(repo, **kw) -> dict:
    from jcodemunch_mcp.tools.get_file_tree import get_file_tree

    return get_file_tree(repo, **kw)


def _shipped(response: dict, fmt: str = "auto"):
    payload, meta = encode_response("get_file_tree", response, fmt)
    return payload, meta


def _assert_round_trips(response: dict, fmt: str = "compact") -> str:
    # "compact" skips the size gate, so a small tree is checked too; the
    # default call's test uses "auto", the shipped default.
    payload, meta = _shipped(response, fmt)
    assert isinstance(payload, str), f"JSON shipped: {meta}"
    assert payload.startswith("#MUNCH/")
    back = decode(payload)
    body = {k: v for k, v in response.items() if not k.startswith("_")}
    assert {k: v for k, v in back.items() if not k.startswith("_")} == body
    assert back.get("_meta") == response.get("_meta")
    return payload


def test_the_default_tree_ships_compact_and_decodes_to_the_same_answer(tree_repo):
    response = _tree(tree_repo)
    payload = _assert_round_trips(response, fmt="auto")
    assert len(payload) < len(json.dumps(response, separators=(",", ":")))


@pytest.mark.parametrize("prefix", ["src/", "src", "src/app/models/", "tests"])
def test_a_prefixed_tree_decodes_to_the_same_answer(tree_repo, prefix):
    response = _tree(tree_repo, path_prefix=prefix)
    assert response["tree"], prefix
    _assert_round_trips(response)


def test_summaries_round_trip_and_stay_absent_when_not_asked(tree_repo):
    with_summaries = _tree(tree_repo, include_summaries=True)
    back = decode(_assert_round_trips(with_summaries))
    assert '"summary"' in json.dumps(back["tree"])
    without = decode(_assert_round_trips(_tree(tree_repo)))
    assert '"summary"' not in json.dumps(without["tree"])


def test_a_capped_tree_keeps_its_truncation_fields(tree_repo):
    response = _tree(tree_repo, max_files=5)
    assert response["truncated"] is True
    back = decode(_assert_round_trips(response))
    assert back["truncated"] is True
    assert back["total_file_count"] == _tree(tree_repo)["_meta"]["file_count"]
    assert back["hint"] == response["hint"]


def test_awkward_file_names_survive(tree_repo):
    back = decode(_assert_round_trips(_tree(tree_repo)))
    flat = json.dumps(back["tree"])
    for name in ("src/lib/@scoped/pkg.ts", "src/lib/odd, name.py", "src/lib/it's here.py", "setup.py"):
        assert name in flat, name


def test_a_warning_survives(tree_repo):
    response = _tree(tree_repo)
    response["budget_warning"] = "Turn budget 93% used."
    back = decode(_assert_round_trips(response))
    assert back["budget_warning"] == "Turn budget 93% used."


def test_a_field_the_compact_form_does_not_carry_ships_json(tree_repo):
    # Fail closed: a key the encoder does not know about would be dropped by
    # the compact form, so the dispatcher must ship JSON instead.
    response = _tree(tree_repo)
    response["new_field"] = "added by a later change"
    payload, meta = _shipped(response)
    assert meta["encoding"] == "json"
    assert payload["new_field"] == "added by a later change"


def test_an_empty_tree_round_trips(tree_repo):
    response = _tree(tree_repo, path_prefix="no/such/dir/")
    assert response["tree"] == []
    payload, _ = encode_response("get_file_tree", response, "compact")
    back = decode(payload)
    assert back == response
