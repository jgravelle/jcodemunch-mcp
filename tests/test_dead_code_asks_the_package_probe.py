"""`find_dead_code` does not call a file dead when an unresolved import names its package (LEDGER L-59).

#415: a language that imports a PACKAGE (Go's `import "mod/pkg"`) addresses a
directory, so the file-level graph lands on no member file and every file in
that package reads as having no importer. `blast_verdict` has asked the
package probe since #718 before reading an empty walk as a zero.
`find_dead_code` never did: a file whose package directory is named by an
import that resolves to nothing was published as `zero_importers` at
confidence 1.0, documented as provably unreachable.

The probe is now one object (`_package_edges.PackageEdgeProbe`, which
`get_blast_radius` re-imports) that both read, so the file is capped like any
other unproven absence and names the cap. Only an EXACT package-path match
caps: a stdlib name that shares a directory's last segment does not. A file no
unresolved import names keeps its confidence.
"""

from __future__ import annotations

import subprocess

import pytest

from jcodemunch_mcp.tools._corpus_adequacy import UNPROVEN_CEILING
from jcodemunch_mcp.tools.find_dead_code import find_dead_code
from jcodemunch_mcp.tools.index_folder import index_folder

# The module this go.mod declares is not `fixture`, so `import "fixture/repository"`
# resolves to no file: the edge the graph cannot land.
GO_MOD = "module example.com/elsewhere\n\ngo 1.22\n"
ORDER_REPO_GO = (
    "package repository\n\ntype OrderRepo struct{ db string }\n\n"
    "func (r *OrderRepo) DeleteItem(id string) error { return nil }\n"
)
PICK_SERVICE_GO = (
    'package pkg\n\nimport "fixture/repository"\n\n'
    "type PickService struct{ orderRepo *repository.OrderRepo }\n\n"
    "func (s *PickService) StartSession(id string) error {\n"
    "\treturn s.orderRepo.DeleteItem(id)\n}\n"
)
ORPHAN_GO = "package orphan\n\nfunc Unused() int { return 1 }\n"


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "fx"
    for d in ("repository", "pkg", "orphan"):
        (root / d).mkdir(parents=True)
    (root / "go.mod").write_text(GO_MOD)
    (root / "repository" / "order_repo.go").write_text(ORDER_REPO_GO)
    (root / "pkg" / "pick_service.go").write_text(PICK_SERVICE_GO)
    (root / "orphan" / "orphan.go").write_text(ORPHAN_GO)
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@t.test"],
        ["config", "user.name", "T"],
        ["add", "-A"],
        ["commit", "-q", "-m", "initial"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    store = str(tmp_path / "store")
    res = index_folder(path=str(root), identity_mode="local", use_ai_summaries=False, storage_path=store)
    assert res["success"], res
    return res["repo"], store


def _dead(repo, store, **kw):
    out = find_dead_code(repo=repo, storage_path=store, **kw)
    assert "error" not in out, out
    return {e["file"]: e for e in out["dead_files"]}


def test_a_file_an_unresolved_package_import_names_is_not_proven_dead(repo):
    dead = _dead(*repo, min_confidence=0)
    entry = dead["repository/order_repo.go"]
    assert entry["confidence"] <= UNPROVEN_CEILING
    assert entry["uncapped_confidence"] == 1.0
    assert "package_granular_imports" in entry["confidence_capped_by"]


def test_the_default_call_does_not_publish_it(repo):
    assert "repository/order_repo.go" not in _dead(*repo)


def test_a_file_no_unresolved_import_names_keeps_its_confidence(repo):
    entry = _dead(*repo, min_confidence=0)["orphan/orphan.go"]
    assert entry["confidence"] == 1.0
    assert "confidence_capped_by" not in entry


@pytest.fixture
def basename_repo(tmp_path):
    """A stdlib or builtin import whose name is only a directory's LAST segment."""
    root = tmp_path / "bn"
    for d in ("app/logging", "web/events"):
        (root / d).mkdir(parents=True)
    (root / "app" / "main.py").write_text("import logging\n\nlogging.info('x')\n")
    (root / "app" / "logging" / "handlers.py").write_text("def unused():\n    return 1\n")
    (root / "web" / "index.js").write_text(
        'import { EventEmitter } from "events";\n\nexport const e = new EventEmitter();\n'
    )
    (root / "web" / "events" / "dead.js").write_text("export function unused() { return 1; }\n")
    store = str(tmp_path / "store")
    res = index_folder(path=str(root), identity_mode="local", use_ai_summaries=False, storage_path=store)
    assert res["success"], res
    return res["repo"], store


def test_a_basename_match_does_not_withhold_a_dead_file(basename_repo):
    """Review of L-59: `import logging` and `"events"` resolve to no file and
    share only a last segment with `app/logging/` and `web/events/`. Across a
    whole corpus that match would withhold real findings in every language."""
    out = find_dead_code(repo=basename_repo[0], storage_path=basename_repo[1], min_confidence=0)
    dead = {e["file"]: e for e in out["dead_files"]}
    for f in ("app/logging/handlers.py", "web/events/dead.js"):
        assert dead[f]["confidence"] == 1.0, dead[f]
        assert "confidence_capped_by" not in dead[f]
    assert "package_granular_imports_withheld" not in find_dead_code(
        repo=basename_repo[0], storage_path=basename_repo[1]
    )


def test_blast_verdict_reads_the_same_probe():
    """One answer to "can the graph reach this file?": the authority's own
    probe is the object the dead-code tool reads, not a second copy."""
    import inspect

    from jcodemunch_mcp.tools import find_dead_code as fdc
    from jcodemunch_mcp.tools import get_blast_radius as gbr

    assert "PackageEdgeProbe" in inspect.getsource(gbr.blast_verdict) or "PackageEdgeProbe" in inspect.getsource(
        gbr._unresolved_package_edges
    )
    assert fdc.PackageEdgeProbe is gbr.PackageEdgeProbe
