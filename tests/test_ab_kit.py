"""Unit tests for the self-run A/B kit's grading and statistics (no network, no model calls)."""
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location("ab_report", pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "ab-kit" / "report.py")
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
ENTRY = {"symbols": [{"name": "handle", "owner": "Router", "file": "lib/router/index.js"}]}


def test_grade_needs_symbol_and_file():
    assert R.grade("lib/router/index.js:136 Router.handle", ENTRY)
    assert R.grade(r"lib\router\index.js:136 handle", ENTRY)
    assert not R.grade("lib/application.js:10 handle", ENTRY)
    assert not R.grade("lib/router/index.js:10 route", ENTRY)


def test_grade_alt_files():
    e = {"symbols": [{"name": "Binding", "file": "binding/binding.go", "alt_files": ["binding/binding_nomsgpack.go"]}]}
    assert R.grade("binding/binding_nomsgpack.go:29 Binding", e)


def test_wilcoxon_exact_small():
    assert R.wilcoxon_exact([1, 2, 3, 4, 5]) == 2 / 32   # all positive, n=5: two-sided 1/16
    assert R.wilcoxon_exact([0, 0]) == 1.0
    assert R.wilcoxon_exact([1, -2, 3, -4, 5, -6]) > 0.5


def test_hodges_lehmann_and_mcnemar():
    assert R.hodges_lehmann([1.0, 2.0, 3.0]) == 2.0
    assert R.mcnemar_exact(0, 0) == 1.0
    assert abs(R.mcnemar_exact(0, 6) - 2 / 64) < 1e-12
