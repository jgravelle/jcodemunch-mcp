"""#415: get_blast_radius must not report a citable 0 for a symbol its import
graph cannot reach.

Go addresses imports at package granularity (``import "mod/pkg"`` names a
directory of files, and files inside that package need no import at all), so the
file-level importer graph lands on no member file and every symbol in the package
comes back with zero importers. The defect was that this was indistinguishable
from a symbol nothing depends on: ``overall_risk_score: 0.0`` with no verdict.

The controls matter as much as the assertions. A Python symbol reached through a
resolvable ``from ... import ...`` must keep a real risk score and an ``ok``
verdict, or this would be a fix that simply refuses every answer.
"""

import tempfile
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.get_blast_radius import (
    _unresolved_package_edges,
    get_blast_radius,
)
from jcodemunch_mcp.tools.index_folder import index_folder

GO_MOD = "module fixture\n\ngo 1.22\n"
INTERFACES_GO = """package repository

type OrderRepository interface {
\tDeleteItem(id string) error
}
"""
ORDER_REPO_GO = """package repository

type OrderRepo struct{ db string }

func (r *OrderRepo) DeleteItem(id string) error { return nil }

type AuditRepo struct{ log string }

func (a *AuditRepo) Record(msg string) error { return nil }
"""
SAME_PKG_GO = """package repository

func PurgeAll(r *OrderRepo, id string) error {
\treturn r.DeleteItem(id)
}
"""
PICK_SERVICE_GO = """package pkg

import "fixture/repository"

type PickService struct {
\torderRepo repository.OrderRepository
\tauditRepo *repository.AuditRepo
}

func (s *PickService) StartSession(id string) error {
\tif err := s.orderRepo.DeleteItem(id); err != nil {
\t\treturn err
\t}
\treturn s.auditRepo.Record("started")
}
"""
BILLING_PY = "def charge_card(amount):\n    return amount * 100\n"
CONTROL_PY = (
    "from services.billing import charge_card\n\n\n"
    "def run_charge(amount):\n    return charge_card(amount)\n"
)


@pytest.fixture(scope="module")
def fixture_repo():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "repository").mkdir()
        (root / "pkg").mkdir()
        (root / "services").mkdir()
        (root / "go.mod").write_text(GO_MOD)
        (root / "repository" / "interfaces.go").write_text(INTERFACES_GO)
        (root / "repository" / "order_repo.go").write_text(ORDER_REPO_GO)
        (root / "repository" / "same_pkg_caller.go").write_text(SAME_PKG_GO)
        (root / "pkg" / "pick_service.go").write_text(PICK_SERVICE_GO)
        (root / "__init__.py").write_text("")
        (root / "services" / "__init__.py").write_text("")
        (root / "pkg" / "__init__.py").write_text("")
        (root / "services" / "billing.py").write_text(BILLING_PY)
        (root / "pkg" / "control.py").write_text(CONTROL_PY)
        with tempfile.TemporaryDirectory() as store:
            res = index_folder(
                path=str(root),
                identity_mode="local",
                use_ai_summaries=False,
                storage_path=store,
            )
            yield res["repo"], store


@pytest.fixture(scope="module")
def served_repo():
    """A fixture indexed where the SERVER looks.

    ``IndexStore`` resolves to ``~/.code-index`` unless handed an explicit
    ``base_path``, and ``server.call_tool`` hands it none — so an entry-point
    test against a temp-store repo measures the not-found error path, not the
    tool. The repo id is derived from a fresh temp directory, so it cannot
    collide with a real one, and the index is deleted on teardown.
    """
    from jcodemunch_mcp.storage import IndexStore

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "repository").mkdir()
        (root / "pkg").mkdir()
        (root / "go.mod").write_text(GO_MOD)
        (root / "repository" / "order_repo.go").write_text(ORDER_REPO_GO)
        (root / "repository" / "same_pkg_caller.go").write_text(SAME_PKG_GO)
        (root / "pkg" / "pick_service.go").write_text(PICK_SERVICE_GO)
        res = index_folder(path=str(root), identity_mode="local", use_ai_summaries=False)
        repo = res["repo"]
        try:
            yield repo
        finally:
            owner, _, nm = repo.partition("/")
            try:
                IndexStore().delete_index(owner, nm, force=True)
            except Exception:
                pass


class TestPackageGranularRefusal:
    def test_go_symbol_with_callers_refuses_the_zero(self, fixture_repo):
        """#415: real callers were reported as a confident 0. Since L-262 the
        same-package caller is found; the cross-package one still is not, and
        the answer must keep saying so. (Name kept: renaming reads as a
        deleted test.)"""
        repo, store = fixture_repo
        r = get_blast_radius(repo=repo, symbol="DeleteItem", call_depth=2,
                             storage_path=store)
        # Precondition: the IMPORT graph still finds nothing. The only file
        # found is in the symbol's own package, reached with no import (L-262).
        assert [c["file"] for c in r["confirmed"]] == ["repository/same_pkg_caller.go"]
        verdict = r["_meta"]["verdict"]
        # L-262: the same-package caller is found now, so the answer is no
        # longer a zero. The cross-package caller (pkg/pick_service.go) is
        # still unreached, and the verdict must keep saying so.
        assert [c["file"] for c in r["callers"]] == ["repository/same_pkg_caller.go"]
        assert verdict["incomplete"]["reason"] == "package_granular_imports"
        assert r["overall_risk_score"] is None

    def test_risk_score_is_withheld_not_zeroed(self, fixture_repo):
        """0.0 is a measurement. The bug was emitting one where none was made."""
        repo, store = fixture_repo
        r = get_blast_radius(repo=repo, symbol="DeleteItem", storage_path=store)
        assert r["overall_risk_score"] is None

    def test_concrete_type_is_refused_too(self, fixture_repo):
        """Not interface-specific: the concrete receiver is equally unreachable."""
        repo, store = fixture_repo
        r = get_blast_radius(repo=repo, symbol="Record", storage_path=store)
        assert r["overall_risk_score"] is None
        assert r["_meta"]["verdict"]["state"] == "degraded"

    def test_evidence_names_the_package_and_the_edge(self, fixture_repo):
        repo, store = fixture_repo
        r = get_blast_radius(repo=repo, symbol="DeleteItem", storage_path=store)
        inc = r["_meta"]["verdict"]["incomplete"]
        assert inc["package_dir"] == "repository"
        assert inc["match"] == "exact"
        assert inc["unresolved_edges"] >= 1
        assert any(
            e["specifier"] == "fixture/repository" for e in inc["unresolved_sample"]
        )
        # Same-package callers need no import at all; that is a second, independent
        # reason the zero cannot be read as absence, so it must be disclosed.
        assert inc["same_package_files"] == 2
        assert "check_references" in inc["note"]


class TestControlsStillAnswer:
    """Non-vacuity: a fix that refuses every answer would pass the class above."""

    def test_resolvable_python_import_keeps_a_real_score(self, fixture_repo):
        repo, store = fixture_repo
        r = get_blast_radius(repo=repo, symbol="charge_card", call_depth=2,
                             storage_path=store)
        assert r["confirmed_count"] == 1
        assert r["overall_risk_score"] is not None
        assert r["overall_risk_score"] > 0
        assert r["_meta"]["verdict"]["state"] == "ok"
        assert "absence_refused" not in r["_meta"]["verdict"]

    def test_genuinely_unreferenced_symbol_is_not_refused(self, fixture_repo):
        """A real zero must survive. `charge_card`'s module has a resolvable
        importer, so a symbol in it with no dependents is honest absence, not an
        unreachable one."""
        repo, store = fixture_repo
        r = get_blast_radius(repo=repo, symbol="run_charge", storage_path=store)
        assert r["confirmed_count"] == 0
        assert r["overall_risk_score"] == 0.0
        verdict = r["_meta"]["verdict"]
        # Nothing to disclose means the key is ABSENT, not present-and-null: an
        # empty disclosure block would read as "we checked and found a gap".
        assert "incomplete" not in verdict
        # It is still an honest zero, so the absence claim stands.
        assert verdict["state"] == "absent"
        assert "absence_refused" not in verdict


class TestReachesTheCaller:
    """Verified at the MCP entry point, not at the layer that was edited.

    The refusal is worth nothing if it does not survive to the wire, and the
    shipped default is ``meta_fields: []`` — which DELETES ``_meta.verdict``
    before the caller sees it. On that install only the re-attached carrier
    survives, and it fires only when ``note_absence`` recorded the scan. That
    needed a non-empty subject string, which this tool spells ``symbol``, not
    ``query``.
    """

    def _call(self, repo, monkeypatch, meta_fields, symbol="Record"):
        import asyncio
        import json

        from jcodemunch_mcp import config as _config
        from jcodemunch_mcp import server

        real_get = _config.get
        monkeypatch.setattr(
            _config, "get",
            lambda k, *a, **kw: meta_fields if k == "meta_fields" else real_get(k, *a, **kw),
        )
        out = asyncio.run(
            # `Record` (the default) has no same-package caller, so its only
            # caller sits behind the unresolved package edge (L-262).
            server.call_tool("get_blast_radius", {"repo": repo, "symbol": symbol})
        )
        # `call_tool` returns a plain list on success and a CallToolResult on
        # error. Asserting the type is not ceremony: without it an unresolvable
        # repo comes back as an error payload that still parses as JSON, and
        # every assertion below would be measuring the error path instead.
        assert isinstance(out, list), f"tool errored instead of answering: {out}"
        text = out[0].text
        try:
            return json.loads(text)
        except ValueError:
            from jcodemunch_mcp.encoding.decoder import decode

            return decode(text)

    def test_verdict_survives_when_meta_is_kept(self, served_repo, monkeypatch):
        repo = served_repo
        d = self._call(repo, monkeypatch, None)
        v = (d.get("_meta") or {}).get("verdict") or {}
        assert d["overall_risk_score"] is None
        assert v["state"] == "degraded"
        # Added by the dispatcher, not by the tool: the refusal is not citable.
        assert v["absence_citable"] is False
        assert v["absence_blocked_by"]

    def test_a_partial_answer_says_so_on_the_shipped_default(self, served_repo, monkeypatch):
        """L-262 review round 4: DeleteItem's same-package caller is found and
        its cross-package caller is not. No absence carrier fires for a
        non-empty answer, so the reason must ride in the body, or a default
        install reads the partial list as complete."""
        d = self._call(served_repo, monkeypatch, [], symbol="DeleteItem")
        assert "verdict" not in (d.get("_meta") or {}), "precondition: stripped"
        assert [c["file"] for c in d["confirmed"]] == ["repository/same_pkg_caller.go"]
        assert d["incomplete"]["reason"] == "package_granular_imports"
        assert d["overall_risk_score"] is None

    def test_carrier_survives_on_the_shipped_default(self, served_repo, monkeypatch):
        """meta_fields: [] strips the verdict. Without the carrier the caller gets
        a bare zero and no reason — the exact failure this fix exists to end."""
        repo = served_repo
        d = self._call(repo, monkeypatch, [])
        meta = d.get("_meta") or {}
        assert "verdict" not in meta, "precondition: the default really does strip it"
        carrier = meta.get("absence_evidence")
        assert carrier is not None, "the refusal reached the caller as a bare empty result"
        assert carrier["citable"] is False
        assert carrier["blocked_by"]
        # The withheld score is on the body, not in _meta, so it survives regardless.
        assert d["overall_risk_score"] is None


class TestEvidenceHelper:
    """Unit-level: the helper must be evidence-driven, not language-driven."""

    def test_returns_none_when_every_edge_resolved(self):
        imports = {"a.py": [{"specifier": "pkg.b", "names": ["x"]}]}
        assert _unresolved_package_edges(
            imports, frozenset({"a.py", "pkg/b.py"}), "pkg/b.py"
        ) is None

    def test_returns_none_for_a_root_level_file(self):
        """No directory to match against, so any answer would be coincidence."""
        imports = {"a.go": [{"specifier": "fixture/nowhere", "names": []}]}
        assert _unresolved_package_edges(
            imports, frozenset({"a.go", "main.go"}), "main.go"
        ) is None

    def test_unrelated_unresolved_edge_is_not_evidence(self):
        """An unresolved specifier for some OTHER package proves nothing here."""
        imports = {"a.go": [{"specifier": "fixture/elsewhere", "names": []}]}
        out = _unresolved_package_edges(
            imports, frozenset({"a.go", "repository/x.go"}), "repository/x.go"
        )
        assert out is None

    def test_exact_match_preferred_over_basename(self):
        imports = {
            "a.go": [{"specifier": "fixture/repository", "names": []}],
            "b.go": [{"specifier": "other/repository", "names": []}],
        }
        out = _unresolved_package_edges(
            imports,
            frozenset({"a.go", "b.go", "repository/x.go"}),
            "repository/x.go",
        )
        assert out["match"] == "exact"
        # Both specifiers end in the target dir, so both are exact matches here.
        assert out["unresolved_edges"] == 2


def test_the_compact_encoding_keeps_the_body_disclosures(fixture_repo):
    """L-262: br2 dropped undeclared top-level dicts, so `incomplete` and
    `dynamic_imports_unfollowed` vanished from a compact answer."""
    from jcodemunch_mcp.encoding.schemas import get_blast_radius as br_schema

    repo, store = fixture_repo
    r = get_blast_radius(repo=repo, symbol="DeleteItem", storage_path=store)
    assert r["incomplete"]["reason"] == "package_granular_imports"
    payload, _enc = br_schema.encode("get_blast_radius", r)
    decoded = br_schema.decode(payload)
    assert decoded["incomplete"] == r["incomplete"]
    assert br_schema.ENCODING_ID == "br4"
    assert "br2" in br_schema.LEGACY_ENCODING_IDS
    assert "br3" in br_schema.LEGACY_ENCODING_IDS
