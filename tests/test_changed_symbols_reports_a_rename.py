"""`get_changed_symbols` reports a renamed function as `renamed` (LEDGER L-322).

Symbols are keyed by `qualified_name#kind`, so a renamed function is a
different key on each side. The `renamed` branch compared two symbols under
the SAME key, which share a qualified name by construction, and its hash test
compared hashes that include the name itself: it could not be reached. A
rename came back as one `added` row and one `removed` row, and
`change_type: renamed` and `previous_name` were never published.

Now an unmatched removed symbol and an unmatched added symbol in the same file
pair as a rename when they share a kind and a parent, and their bodies are
identical once each one's own name is masked. Only a unique match pairs; two
candidates with the same masked body stay `added` and `removed`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jcodemunch_mcp.tools import get_changed_symbols as mod
from jcodemunch_mcp.tools.get_changed_symbols import get_changed_symbols
from jcodemunch_mcp.tools.index_folder import index_folder

PADDING = "# padding line\n" * 30


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
        encoding="utf-8", stdin=subprocess.DEVNULL,
    ).stdout.strip()


def _repo(tmp_path: Path, text: str) -> tuple[Path, str, str, str]:
    root = tmp_path / "r"
    root.mkdir()
    (root / "app.py").write_text(text, encoding="utf-8")
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@t"], root)
    _git(["config", "user.name", "t"], root)
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "c0"], root)
    storage = str(tmp_path / "store")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage, identity_mode="local")
    return root, res["repo"], storage, _git(["rev-parse", "HEAD"], root)


def _commit(root: Path, text: str) -> None:
    (root / "app.py").write_text(text, encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "c1"], root)


def _diff(tmp_path: Path, before: str, after: str) -> dict:
    root, repo, storage, base = _repo(tmp_path, before)
    _commit(root, after)
    result = get_changed_symbols(repo, since_sha=base, storage_path=storage)
    assert "error" not in result, result
    return result


def test_a_renamed_function_is_one_renamed_row(tmp_path):
    before = "def compute_total(items):\n    return sum(items)\n\n\n" + PADDING
    after = "def sum_items(items):\n    return sum(items)\n\n\n" + PADDING
    result = _diff(tmp_path, before, after)

    assert result["added_symbols"] == [], "a rename was published as an added symbol"
    assert result["removed_symbols"] == [], "a rename was published as a removed symbol"
    rows = result["changed_symbols"]
    assert len(rows) == 1, rows
    assert rows[0]["change_type"] == "renamed"
    assert rows[0]["name"] == "sum_items"
    assert rows[0]["previous_name"] == "compute_total"


def test_a_recursive_function_renamed_everywhere_in_its_body_is_a_rename(tmp_path):
    before = "def walk(n):\n    return 0 if n == 0 else walk(n - 1)\n\n\n" + PADDING
    after = "def descend(n):\n    return 0 if n == 0 else descend(n - 1)\n\n\n" + PADDING
    rows = _diff(tmp_path, before, after)["changed_symbols"]
    assert [(r["change_type"], r["name"], r.get("previous_name")) for r in rows] == [
        ("renamed", "descend", "walk")
    ]


def test_a_renamed_method_keeps_its_class(tmp_path):
    before = "class Cart:\n    def total(self):\n        return 1\n\n\n" + PADDING
    after = "class Cart:\n    def grand_total(self):\n        return 1\n\n\n" + PADDING
    result = _diff(tmp_path, before, after)
    renamed = [r for r in result["changed_symbols"] if r["change_type"] == "renamed"]
    assert [(r["qualified_name"], r["previous_name"]) for r in renamed] == [("Cart.grand_total", "total")]
    assert result["added_symbols"] == [] and result["removed_symbols"] == []


def test_a_rename_with_an_edited_body_stays_added_and_removed(tmp_path):
    before = "def compute_total(items):\n    return sum(items)\n\n\n" + PADDING
    after = "def sum_items(items):\n    return sum(items) + 1\n\n\n" + PADDING
    result = _diff(tmp_path, before, after)
    assert [r["name"] for r in result["added_symbols"]] == ["sum_items"]
    assert [r["name"] for r in result["removed_symbols"]] == ["compute_total"]
    assert result["changed_symbols"] == []


def test_two_candidates_with_the_same_body_are_not_paired(tmp_path):
    body = "(x):\n    return x\n\n\n"
    before = "def a" + body + PADDING
    after = "def b" + body + "def c" + body + PADDING
    result = _diff(tmp_path, before, after)
    assert sorted(r["name"] for r in result["added_symbols"]) == ["b", "c"]
    assert [r["name"] for r in result["removed_symbols"]] == ["a"]
    assert result["changed_symbols"] == []


def test_a_different_body_is_not_a_rename(tmp_path):
    before = "def shape(x):\n    return x\n\n\n" + PADDING
    after = "class Shape:\n    pass\n\n\n" + PADDING
    result = _diff(tmp_path, before, after)
    assert result["changed_symbols"] == []
    assert [r["name"] for r in result["removed_symbols"]] == ["shape"]


def test_a_method_moved_to_another_class_and_renamed_is_not_a_rename(tmp_path):
    # Same masked body, different parent: only the parent rule rejects it.
    before = "class A:\n    def f(self):\n        return 1\n\n\nclass B:\n    pass\n\n\n" + PADDING
    after = "class A:\n    pass\n\n\nclass B:\n    def g(self):\n        return 1\n\n\n" + PADDING
    result = _diff(tmp_path, before, after)
    assert [r for r in result["changed_symbols"] if r["change_type"] == "renamed"] == []
    assert [r["qualified_name"] for r in result["added_symbols"]] == ["B.g"]
    assert [r["qualified_name"] for r in result["removed_symbols"]] == ["A.f"]


def test_a_rename_after_multibyte_text_reads_the_right_bytes(tmp_path):
    lead = "# caf\u00e9 \u2603 \u4e2d\u6587\n\n\n"
    before = lead + "def old_name(v):\n    return v * 2\n\n\n" + PADDING
    after = lead + "def new_name(v):\n    return v * 2\n\n\n" + PADDING
    rows = _diff(tmp_path, before, after)["changed_symbols"]
    assert [(r["change_type"], r["previous_name"]) for r in rows] == [("renamed", "old_name")]


def test_a_renamed_row_names_the_old_symbol_id(tmp_path):
    before = "def compute_total(items):\n    return sum(items)\n\n\n" + PADDING
    after = "def sum_items(items):\n    return sum(items)\n\n\n" + PADDING
    row = _diff(tmp_path, before, after)["changed_symbols"][0]
    assert row["previous_symbol_id"].endswith("compute_total#function"), row
    assert row["symbol_id"].endswith("sum_items#function"), row
    assert "_rename_span" not in row


def _sym(name, qn, kind, body):
    raw = body.encode("utf-8")
    return {"name": name, "qualified_name": qn, "kind": kind, "symbol_id": qn,
            "_rename_span": (0, len(raw), 1, 1)}, body


@pytest.mark.parametrize("old,new,pairs", [
    (("a", "a", "function"), ("b", "b", "function"), True),
    (("a", "a", "function"), ("b", "b", "method"), False),  # only the kind differs
    (("a", "X.a", "function"), ("b", "Y.b", "function"), False),  # only the parent differs
])
def test_each_pairing_condition_rejects_on_its_own(old, new, pairs):
    (b, bt) = _sym(*old, f"def {old[0]}(): return 1")
    (a, at) = _sym(*new, f"def {new[0]}(): return 1")
    got = mod._pair_renames({"o"}, {"n"}, {"o": b}, {"n": a}, bt, at)
    assert got == ([("o", "n")] if pairs else [])
