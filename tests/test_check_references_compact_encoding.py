"""check_references ships a compact form that decodes to the same answer.

It was always JSON: every import match repeated `"specifier"`, `"names"` and
`"match_type"`, every content line repeated `"line"` and `"text"`, and each
enclosing-symbol id repeated its file's whole path. The compact form sends one
row per match with each repeated path written once. It is a change of encoding
only: the decoded answer is the tool's response, and no list is shortened.
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp.encoding import encode_response
from jcodemunch_mcp.encoding.decoder import decode

N_IMPORTERS = 30


@pytest.fixture
def ref_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / ".idx"))
    monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", str(tmp_path))
    monkeypatch.setenv("JCODEMUNCH_USE_AI_SUMMARIES", "false")
    root = tmp_path / "repo"
    (root / "lib").mkdir(parents=True)
    (root / "lib" / "__init__.py").write_text("", encoding="utf-8")
    (root / "lib" / "service.py").write_text(
        "def Injectable(cls=None):\n    return cls\n\n\ndef Other():\n    return 1\n",
        encoding="utf-8",
    )
    for i in range(N_IMPORTERS):
        d = root / "app" / f"mod{i % 4}"
        d.mkdir(parents=True, exist_ok=True)
        names = "Injectable, Other" if i % 3 else "Injectable"
        (d / f"svc{i}.py").write_text(
            f"from lib.service import {names}\n\n\n"
            f"@Injectable\nclass Svc{i}:\n"
            f"    note = \"uses Injectable, with a comma and 'quotes' = {i}\"\n",
            encoding="utf-8",
        )
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    assert index_folder(str(root), use_ai_summaries=False)["success"]
    return resolve_repo(str(root))["repo"]


def _refs(repo, **kw) -> dict:
    from jcodemunch_mcp.tools.check_references import check_references

    if "identifiers" not in kw:
        kw.setdefault("identifier", "Injectable")
    return check_references(repo, **kw)


def _round_trip(response: dict, fmt: str = "compact") -> str:
    payload, meta = encode_response("check_references", response, fmt)
    assert isinstance(payload, str), f"JSON shipped: {meta}"
    assert payload.startswith("#MUNCH/")
    assert decode(payload) == response
    return payload


def test_the_default_call_ships_compact_and_decodes_to_the_same_answer(ref_repo):
    response = _refs(ref_repo)
    assert response["import_count"] == N_IMPORTERS
    assert response["content_references"]
    payload = _round_trip(response, fmt="auto")
    assert len(payload) < len(json.dumps(response, separators=(",", ":")))


def test_no_import_row_is_dropped(ref_repo):
    # Encoding only: the import list ships unbounded and must stay whole.
    response = _refs(ref_repo)
    back = decode(_round_trip(response))
    assert len(back["import_references"]) == N_IMPORTERS

    def rows(r):
        return sum(len(g["matches"]) for g in r["import_references"])

    # Each import yields more than one match (module and qualified name).
    assert rows(back) == rows(response) > N_IMPORTERS


def test_a_batch_decodes_to_the_same_answer(ref_repo):
    _round_trip(_refs(ref_repo, identifiers=["Injectable", "Other", "NoSuchName"]))


def test_an_imports_only_call_has_no_content_key(ref_repo):
    response = _refs(ref_repo, search_content=False)
    assert "content_references" not in response
    back = decode(_round_trip(response))
    assert "content_references" not in back and "content_truncated" not in back


def test_a_cut_content_page_keeps_its_flag(ref_repo):
    response = _refs(ref_repo, max_content_results=1)
    assert response["content_truncated"] is True
    assert decode(_round_trip(response))["content_truncated"] is True


def test_an_unreferenced_name_round_trips(ref_repo):
    response = _refs(ref_repo, identifier="NoSuchName")
    assert response["is_referenced"] is False
    _round_trip(response)


def test_line_text_survives(ref_repo):
    # Lines starting with "@" (the legend's own prefix), commas, quotes, "=".
    back = decode(_round_trip(_refs(ref_repo)))
    texts = {m["text"] for g in back["content_references"] for m in g["matches"]}
    assert "@Injectable" in texts
    assert any("with a comma and 'quotes' =" in t for t in texts)


def test_an_enclosing_symbol_outside_its_file_round_trips(ref_repo):
    # Ids normally start with their own file; one that doesn't must not be
    # rewritten on the way back.
    response = _refs(ref_repo)
    group = response["content_references"][0]
    group["matches"][0]["in"] = {"id": "elsewhere/x.py::Foo#class", "line": 1, "end_line": 2}
    _round_trip(response)


def test_meta_keys_the_server_adds_survive(ref_repo):
    response = _refs(ref_repo)
    response["_meta"]["budget"] = {"used": 18627, "limit": 20000}
    response["_meta"]["secrets_redacted"] = 2
    back = decode(_round_trip(response, fmt="auto"))
    assert back["_meta"] == response["_meta"]


def test_a_warning_survives(ref_repo):
    response = _refs(ref_repo)
    response["budget_warning"] = "Turn budget 93% used."
    assert decode(_round_trip(response))["budget_warning"] == "Turn budget 93% used."


def test_a_field_the_compact_form_does_not_carry_ships_json(ref_repo):
    response = _refs(ref_repo)
    response["new_field"] = "added by a later change"
    payload, meta = encode_response("check_references", response, "auto")
    assert meta["encoding"] == "json" and payload["new_field"] == "added by a later change"


def test_an_unexpected_match_key_ships_json(ref_repo):
    response = _refs(ref_repo)
    response["import_references"][0]["matches"][0]["alias"] = "Inj"
    payload, meta = encode_response("check_references", response, "auto")
    assert meta["encoding"] == "json"
