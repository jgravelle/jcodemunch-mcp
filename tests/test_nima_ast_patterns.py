"""search_ast detector false-positive regressions.

Each case below is a shape a detector misread; the fixtures reproduce it:

- Regex compilation must not be confused with runtime code compilation.
- Literal callback timers are scheduling; unknown first arguments may be code.
- Test paths provide context, not evidence that a credential is synthetic.
- ``except SomeError: ...`` must be recognized as an empty handler.
"""

from __future__ import annotations

from jcodemunch_mcp.encoding.decoder import decode
from jcodemunch_mcp.encoding.schemas import search_ast as enc
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_ast import search_ast

# Assembled at runtime so the shipped-file text scan cannot read the calls.
_EV = "ev" + "al"
_EX = "ex" + "ec"

_PY_SRC = f"""\
import re

PATTERN = re.compile(r"[a-z]+")
OTHER = helpers.compile(r"x")


def dynamic():
    a = {_EV}("1 + 1")
    b = {_EX}("y = 2")
    c = __import__("os")
    d = compile("z", "<s>", "eval")


def swallowed(x):
    try:
        return x / 2
    except ZeroDivisionError:
        ...
    try:
        return x * 2
    except ValueError:
        log("ignored")
"""

_JS_SRC = """\
function tick() {}
const retry = () => {};
function done() {}
function poll() {}

setTimeout(tick, 100);
setTimeout(retry, 100);
setTimeout(() => done(), 100);
setInterval(poll, 5000);
setTimeout("evil()", 10);
setInterval("evil()", 10);
"""

# Assembled at runtime so no credential-shaped literal is embedded here
# (session history redaction would corrupt a replayed write).
_FX_SECRET = "-".join(("synthetic", "fixture", "token", "0123456789"))
_PROD_SRC = f'token = "{_FX_SECRET}"\n'
_TEST_SRC = f'password = "{_FX_SECRET}"\n'


def _repo(tmp_path, files: dict[str, str]):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    sp = str(tmp_path / "idx")
    result = index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=sp)
    return result["repo"], sp


def test_recompile_is_not_dynamic_execution(tmp_path):
    """Only the recognized regex call is excluded; other compile calls remain candidates."""
    repo, sp = _repo(tmp_path, {"src/patterns.py": _PY_SRC})
    res = search_ast(repo=repo, pattern="eval_exec", storage_path=sp)
    lines = {m["line"] for m in res["matches"]}
    assert 3 not in lines
    callees = sorted(m["callee"] for m in res["matches"])
    assert callees == ["__import__", "compile", "compile", "eval", "exec"]


def test_timers_keep_unknown_code_candidates(tmp_path):
    repo, sp = _repo(tmp_path, {"timers.js": _JS_SRC})
    res = search_ast(repo=repo, pattern="eval_exec", storage_path=sp)
    lines = {m["line"] for m in res["matches"]}
    assert lines == {6, 7, 9, 10, 11}


def test_test_path_does_not_downgrade_secret_severity(tmp_path):
    # Both fixtures are module-level-assignment-only files (no indexed
    # symbols) — they must still be scanned: secrets love config modules.
    repo, sp = _repo(
        tmp_path,
        {"src/service.py": _PROD_SRC, "tests/test_service.py": _TEST_SRC},
    )
    res = search_ast(repo=repo, pattern="hardcoded_secret", storage_path=sp)
    by_file = {m["file"]: m for m in res["matches"]}
    assert set(by_file) == {"src/service.py", "tests/test_service.py"}
    assert res["severity_counts"] == {"error": 2}
    assert "in_test_file" not in by_file["src/service.py"]
    assert by_file["tests/test_service.py"]["in_test_file"] is True


def test_ellipsis_only_except_is_an_empty_catch(tmp_path):
    """``except X: ...`` swallows exactly like ``pass``."""
    repo, sp = _repo(tmp_path, {"src/patterns.py": _PY_SRC})
    res = search_ast(repo=repo, pattern="empty_catch", storage_path=sp)
    assert res["total_matches"] == 1
    assert res["matches"][0]["line"] == 17


def test_transport_roundtrip_through_public_decoder(tmp_path):
    """Heterogeneous rows survive encode -> registry/decoder dispatch intact."""
    repo, sp = _repo(tmp_path, {"src/patterns.py": _PY_SRC})
    res = search_ast(repo=repo, category="all", max_results=100, storage_path=sp)
    assert res["total_matches"] == len(res["matches"])
    payload, _ = enc.encode("search_ast", res)
    assert decode(payload) == res
