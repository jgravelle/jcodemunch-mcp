"""A JavaScript method called through a string key is recorded (LEDGER L-173, item 7).

JavaScript and TypeScript let a member be reached by a string: `t["m"]()` calls
the same method as `t.m()`. The grammar gives the first a
`subscript_expression`, and the parser resolved every subscript to its
container, the rule for `fs[0]()` where an element has no name. So the call
recorded `t`, the method `m` recorded nothing, and a method called only that
way read `no_callers` in `get_dead_code_v2`: live code as dead.

A key that is a string holding a name IS a name. Any other key (a variable, a
number, a string that is not a name, a template with a substitution) still
resolves to the container.

Every case goes through `parse_file`, and the dead-code cases through
`index_folder` and the tool.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools.index_folder import index_folder

BACKTICK = chr(96)
BACKSLASH = chr(92)
LANGUAGES = [("javascript", "js"), ("typescript", "ts"), ("tsx", "tsx")]


def _records(statement: str, language: str, extension: str) -> list[str]:
    """What a function whose body is `statement` records."""
    source = "function host(t, k) {\n  " + statement + ";\n}\n"
    found = [s for s in parse_file(source, "a." + extension, language) if s.name == "host"]
    assert len(found) == 1, found
    return found[0].call_references


# the statement -> what the function records
BY_STRING_KEY = {
    "double quotes": ('t["m"]()', ["m"]),
    "single quotes": ("t['m']()", ["m"]),
    "a template with no substitution": ("t[" + BACKTICK + "m" + BACKTICK + "]()", ["m"]),
    "an optional chain": ('t?.["m"]()', ["m"]),
    "an optional call": ('t["m"]?.()', ["m"]),
    "on this": ('this["m"]()', ["m"]),
    "after a member": ('a.b["m"]()', ["m"]),
    "after another key": ('a["b"]["m"]()', ["m"]),
    "with arguments": ('t["m"](1, 2)', ["m"]),
    "in parentheses": ('(t["m"])()', ["m"]),
    "created with new": ('new t["Made"]()', ["Made"]),
    "on a call's result": ('f()["m"]()', ["f", "m"]),
    "its result called": ('t["m"]()()', ["m"]),
    "a name with a dollar": ('t["$m"]()', ["$m"]),
    "a name with an underscore and a digit": ('t["_m1"]()', ["_m1"]),
    "returned": ('return t["m"]()', ["m"]),
    "a name in another script": ('t["caf' + chr(0xE9) + '"]()', ["caf" + chr(0xE9)]),
    "a name that is only a dollar": ('t["$"]()', ["$"]),
    "an element of the member": ('t["m"][0]()', ["m"]),
    "on a container in parentheses": ('(t)["m"]()', ["m"]),
    # A reserved word is the oldest reason to write a string key.
    "a reserved word": ('t["finally"]()', ["finally"]),
    "a word Python reserves": ('t["class"]()', ["class"]),
    "as an argument": ('other(t["m"]())', ["m", "other"]),
}

# A key that is not a name: the container, as before.
BY_ANOTHER_KEY = {
    "a variable": ("t[k]()", ["t"]),
    "a number": ("t[0]()", ["t"]),
    "a negated variable": ("t[-k]()", ["t"]),
    "a string in parentheses": ('t[("m")]()', ["t"]),
    "a string that is not a name": ('t["a-b"]()', ["t"]),
    "a string with a space": ('t["a b"]()', ["t"]),
    "a string that starts with a digit": ('t["1m"]()', ["t"]),
    "an empty string": ('t[""]()', ["t"]),
    "a string with an escape": ('t["m' + BACKSLASH + 'n"]()', ["t"]),
    "two strings joined": ('t["m" + "n"]()', ["t"]),
    "a template with a substitution": ("t[" + BACKTICK + "m${k}" + BACKTICK + "]()", ["t"]),
    "a private-looking string": ('t["#m"]()', ["t"]),
    "a string with a dot": ('t["a.b"]()', ["t"]),
    "a string with a tilde": ('t["~validate"]()', ["t"]),
    "a string with a hyphen after a name": ('t["m-"]()', ["t"]),
    "a template that ends in a newline": ("t[" + BACKTICK + "m\n" + BACKTICK + "]()", ["t"]),
    "a template that is only a substitution": ("t[" + BACKTICK + "${k}" + BACKTICK + "]()", ["t"]),
    "a string that is only an escape": ('t["' + BACKSLASH + 'u006d"]()', ["t"]),
}

# Shapes that were right before and are unchanged.
UNCHANGED = {
    "a dot call": ("t.m()", ["m"]),
    "a member of a keyed element": ('t["m"].n()', ["n"]),
    "a keyed element not called": ('var x = t["m"]', []),
    "a string key assigned": ('t["m"] = 1', []),
    "a plain call": ("m()", ["m"]),
}


def _cases(table):
    return [pytest.param(label, lang, ext, id=f"{lang}: {label}") for lang, ext in LANGUAGES for label in table]


@pytest.mark.parametrize("label,language,extension", _cases(BY_STRING_KEY))
def test_a_call_through_a_string_key_records_the_member(label, language, extension):
    statement, expected = BY_STRING_KEY[label]
    assert sorted(_records(statement, language, extension)) == sorted(expected)


@pytest.mark.parametrize("label,language,extension", _cases(BY_ANOTHER_KEY))
def test_a_key_that_is_not_a_name_records_the_container(label, language, extension):
    statement, expected = BY_ANOTHER_KEY[label]
    assert _records(statement, language, extension) == expected


@pytest.mark.parametrize("label,language,extension", _cases(UNCHANGED))
def test_what_was_recorded_before_is_recorded_still(label, language, extension):
    statement, expected = UNCHANGED[label]
    assert _records(statement, language, extension) == expected


@pytest.mark.parametrize("extension,language", [("ts", "typescript"), ("tsx", "tsx")])
def test_a_typescript_non_null_string_key_records_the_member(extension, language):
    assert _records('t["m"]!()', language, extension) == ["m"]


def test_a_method_that_calls_itself_through_a_string_key_records_nothing_for_it():
    source = 'function m(t) {\n  return t["m"]();\n}\n'
    (found,) = [s for s in parse_file(source, "a.js", "javascript") if s.name == "m"]
    assert found.call_references == []


# The same spelling in a language where a string key is an element of a
# container, not a member: the container, as before.
ELSEWHERE = {
    "python": ("a.py", "def host(t):\n    t[\"m\"]()\n", ["t"]),
    "go": ("a.go", "package a\n\nfunc host(t map[string]func()) {\n\tt[\"m\"]()\n}\n", ["t"]),
    "csharp": (
        "A.cs",
        "class A {\n    void Host(System.Collections.Generic.Dictionary<string, System.Action> t) {\n"
        "        t[\"m\"]();\n    }\n}\n",
        ["t"],
    ),
}


@pytest.mark.parametrize("language", sorted(ELSEWHERE))
def test_a_string_key_in_another_language_records_the_container(language):
    filename, source, expected = ELSEWHERE[language]
    found = [s for s in parse_file(source, filename, language) if s.name.lower() == "host"]
    assert len(found) == 1, found
    assert found[0].call_references == expected


@pytest.mark.parametrize("key", ['"m"', "'m'"], ids=["double quotes", "single quotes"])
def test_a_php_string_key_does_not_record_the_key(key):
    # PHP's grammar has a `subscript_expression` too. A string key there
    # reaches an element of an array.
    source = "<?php\nfunction host($t) {\n    $t[" + key + "]();\n}\n"
    (found,) = [s for s in parse_file(source, "a.php", "php") if s.name == "host"]
    assert "m" not in found.call_references


# Two Python files joined by an import, so the index has an import graph and the
# tool runs its three-signal path.
PYTHON_PAIR = {
    "util.py": "def b():\n    return 1\n",
    "py.py": "from util import b\n\ndef a():\n    return b()\n",
}

JAVASCRIPT = {
    "api.js": (
        "class Api {\n  save() {\n    return 1;\n  }\n  lonely() {\n    return 2;\n  }\n"
        "  plain() {\n    return 3;\n  }\n}\nmodule.exports = { Api };\n"
    ),
    # `api.plain()` is a recorded call on both trees, so the tool reads
    # JavaScript by call references and not as text (L-155).
    "use.js": (
        "const { Api } = require('./api');\n\nfunction use() {\n  const api = new Api();\n"
        "  return api[\"save\"]() + api.plain();\n}\nmodule.exports = { use };\n"
    ),
}


def _signals(tmp_path, files):
    src = tmp_path / "src"
    src.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    for name, body in files.items():
        (src / name).write_text(body, encoding="utf-8")
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True
    out = v2.get_dead_code_v2(repo=r["repo"], min_confidence=0.0, max_results=0, storage_path=str(store))
    assert "error" not in out, out
    assert out.get("_meta", {}).get("mode") != "call_graph_only", out.get("_meta")
    return {(row["file"], row["name"]): row["signals"] for row in out["dead_symbols"]}


def test_a_method_called_through_a_string_key_is_not_reported_without_callers(tmp_path):
    rows = _signals(tmp_path, {**PYTHON_PAIR, **JAVASCRIPT})
    assert "no_callers" not in rows.get(("api.js", "save"), [])


def test_a_method_nothing_calls_is_still_reported(tmp_path):
    rows = _signals(tmp_path, {**PYTHON_PAIR, **JAVASCRIPT})
    assert "no_callers" in rows[("api.js", "lonely")], rows


def test_a_method_called_with_a_dot_was_never_reported(tmp_path):
    rows = _signals(tmp_path, {**PYTHON_PAIR, **JAVASCRIPT})
    assert "no_callers" not in rows.get(("api.js", "plain"), [])


def test_an_existing_index_is_reparsed():
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 18
