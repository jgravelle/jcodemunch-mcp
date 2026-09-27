"""A class in a Vue or Svelte `<script>` owns its members, as in a `.ts` file (#861).

Both channels walked their script by hand and stopped at a class: a
declaration published its name and no member, and a class expression
(`const C = class {...}`) published a `constant` with no class (#803's shape in
a channel #803 never reached). The class and its members now come from the
generic JS/TS walk, so these tests compare against a `.ts`/`.js` file of the
same script rather than restating a member list.
"""

import pytest

import jcodemunch_mcp.config as config_module
from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages(monkeypatch):
    monkeypatch.setattr(config_module, "is_language_enabled", lambda *a, **k: True)


CLASS_TS = "class Svc {\n  x = 1;\n  constructor(private a: number) {}\n  m() {}\n}\n"
CLASS_JS = "class Svc {\n  x = 1;\n  constructor(a) {}\n  m() {}\n}\n"
EXPRESSION = "const C = class { x = 1; m() {} };\nexport default { name: 'A' };\n"

CHANNELS = [("a.vue", "vue"), ("a.svelte", "svelte")]


def _ids(symbols):
    return {s.id.split("::")[1]: s.parent and s.parent.split("::")[1] for s in symbols}


def _script(body: str, attrs: str = "") -> str:
    return f"<template><div/></template>\n<script{attrs}>\n{body}</script>\n"


def _expected(body: str, lang: str) -> dict:
    """What a plain `.ts`/`.js` file of the same script publishes, re-rooted at
    the component (`a#class`), the only difference the channel adds."""
    ext = {"typescript": "ts", "tsx": "tsx"}.get(lang, "js")
    got = _ids(parse_file(body, f"a.{ext}", lang))
    return {k: (v if v is not None else "a#class") for k, v in got.items()}


@pytest.mark.parametrize("filename,language", CHANNELS)
@pytest.mark.parametrize(
    "body,attrs,lang",
    [
        (CLASS_TS, ' lang="ts"', "typescript"),
        (CLASS_JS, "", "javascript"),
        (EXPRESSION, "", "javascript"),
        (EXPRESSION, ' lang="ts"', "typescript"),
    ],
    ids=["declaration-ts", "declaration-js", "expression-js", "expression-ts"],
)
def test_the_class_publishes_what_a_script_file_publishes(filename, language, body, attrs, lang):
    got = _ids(parse_file(_script(body, attrs), filename, language))
    expected = _expected(body, lang)

    assert any(k.endswith("#class") and k != "a#class" for k in expected)
    assert any(k.endswith("#method") for k in expected)
    for sid, parent in expected.items():
        assert got.get(sid, "MISSING") == parent, (sid, got)


@pytest.mark.parametrize("filename,language", CHANNELS)
def test_a_class_expression_is_not_also_a_constant(filename, language):
    got = _ids(parse_file(_script(EXPRESSION), filename, language))

    assert "C#class" in got
    assert "C#constant" not in got


def test_vue_script_setup_class_owns_its_members():
    got = _ids(parse_file(_script(CLASS_TS, ' setup lang="ts"'), "a.vue", "vue"))

    assert got["Svc.m#method"] == "Svc#class"
    assert got["Svc.a#field"] == "Svc#class"


@pytest.mark.parametrize("filename,language", CHANNELS)
def test_exported_and_multi_declarator_classes_own_their_members(filename, language):
    body = (
        "export class F { n() {} }\n"
        "export const E = class { e() {} };\n"
        "const A = class { a() {} }, B = class { b() {} };\n"
    )
    got = _ids(parse_file(_script(body), filename, language))

    for cls, member in (("F", "n"), ("E", "e"), ("A", "a"), ("B", "b")):
        assert got.get(f"{cls}#class") == "a#class", got
        assert got.get(f"{cls}.{member}#method") == f"{cls}#class", got


@pytest.mark.parametrize("filename,language", CHANNELS)
def test_member_lines_and_bytes_address_the_component_file(filename, language):
    source = _script("// é\n" + CLASS_TS, ' lang="ts"')
    data = source.encode("utf-8")
    lines = source.splitlines()

    members = [s for s in parse_file(source, filename, language) if s.qualified_name.startswith("Svc")]

    assert {s.name for s in members} >= {"Svc", "x", "m"}
    for s in members:
        text = data[s.byte_offset:s.byte_offset + s.byte_length].decode("utf-8")
        assert text.split("\n")[0].strip() in lines[s.line - 1], (s.id, text, lines[s.line - 1])
        assert s.file == filename
        assert s.language == language


@pytest.mark.parametrize("filename,language", CHANNELS)
def test_a_class_inside_a_function_is_still_not_published(filename, language):
    body = "function make() { return class Inner { i() {} }; }\n"
    got = _ids(parse_file(_script(body), filename, language))

    assert not any(k.startswith("Inner") for k in got), got


# Every spelling the generic walk calls a class, and none the hand walks name.
# Review of the first draft: the hand walks fetched only `class_declaration`
# and `const C = class`, so each of these stayed dropped.
SPELLINGS = [
    ("abstract", "abstract class A { abstract f(): void; g() {} }\n", ' lang="ts"', "typescript"),
    ("export-abstract", "export abstract class A { g() {} }\n", ' lang="ts"', "typescript"),
    ("anonymous-default", "export default class { m() {} }\n", "", "javascript"),
    ("named-default", "export default class Foo extends Base { m() {} }\n", "", "javascript"),
    ("module-exports", "module.exports = class { m() {} };\n", "", "javascript"),
    ("member-assignment", "X.P = class { m() {} };\n", "", "javascript"),
]


@pytest.mark.parametrize("filename,language", CHANNELS)
@pytest.mark.parametrize("body,attrs,lang", [s[1:] for s in SPELLINGS], ids=[s[0] for s in SPELLINGS])
def test_every_class_spelling_publishes_what_a_script_file_publishes(filename, language, body, attrs, lang):
    got = _ids(parse_file(_script(body, attrs), filename, language))
    expected = _expected(body, lang)

    assert any(k.endswith("#method") for k in expected), expected
    for sid, parent in expected.items():
        assert got.get(sid, "MISSING") == parent, (sid, got)


@pytest.mark.parametrize("filename,language", CHANNELS)
def test_a_class_is_published_once(filename, language):
    body = (
        "export class F { n() {} }\n"
        "const C = class { m() { class Inner { i() {} } return Inner; } };\n"
    )
    ids = [s.id.split("::")[1] for s in parse_file(_script(body), filename, language)]

    assert len(ids) == len(set(ids)), ids
    assert not any(i.startswith("Inner") for i in ids), ids


def test_a_svelte_prop_holding_a_class_stays_a_prop():
    body = "export let C = class { m() {} };\n"
    got = _ids(parse_file(_script(body), "a.svelte", "svelte"))

    assert "C#property" in got
    assert not any(k.startswith("C#class") or k.startswith("C.") for k in got), got


@pytest.mark.parametrize("filename,language", CHANNELS)
def test_a_disabled_script_language_falls_back_to_the_bare_class(filename, language, monkeypatch):
    monkeypatch.setattr(
        config_module, "is_language_enabled",
        lambda lang, *a, **k: lang not in ("javascript", "typescript"),
    )
    got = _ids(parse_file(_script(CLASS_JS), filename, language))

    assert got.get("Svc#class") == "a#class", got
    assert "Svc.m#method" not in got


def test_a_class_beside_an_options_object_is_published():
    # LEDGER L-36: the options walk still drops every OTHER declaration here.
    body = "class K { k() {} }\nexport default { methods: { go() {} } };\n"
    got = _ids(parse_file(_script(body), "a.vue", "vue"))

    assert got.get("K#class") == "a#class", got
    assert got.get("K.k#method") == "K#class", got
    assert "go#method" in got, got


@pytest.mark.parametrize("body", [
    "$: C = class { m() {} };\n",
    "$: C = (class { m() {} });\n",
], ids=["bare", "parenthesised"])
def test_a_svelte_reactive_class_is_a_class_not_also_a_constant(body):
    got = _ids(parse_file(_script(body), "a.svelte", "svelte"))
    expected = _expected(body, "javascript")

    assert "C#constant" not in got, got
    for sid, parent in expected.items():
        assert got.get(sid, "MISSING") == parent, (sid, got)


@pytest.mark.parametrize("filename,language", CHANNELS)
@pytest.mark.parametrize("body,parses", [
    ("const el = document.body;\nel.classList.add('className');\n", 0),
    ("class K { k() {} }\n", 1),
], ids=["class-in-text-only", "a-class-node"])
def test_the_script_is_parsed_again_only_for_a_class_node(filename, language, body, parses, monkeypatch):
    """The second parse is gated on a class NODE in the tree the hand walk
    already holds, never on the substring `class` (review: +24-27% parse time
    on a script that only mentions `classList`)."""
    import jcodemunch_mcp.parser.extractor as extractor

    calls = []
    real = extractor.parse_file

    def spy(content, filename, *a, **k):
        if "#script." in filename:
            calls.append(filename)
        return real(content, filename, *a, **k)

    monkeypatch.setattr(extractor, "parse_file", spy)
    parse_file(_script(body), filename, language)

    assert len(calls) == parses, calls


# What may follow the keyword: a type parameter, a comment, a non-ASCII name.
# Review round 3: a prefilter that asked about the next character refused all
# three, so their classes stayed memberless with every other test green.
AFTER_THE_KEYWORD = [
    ("type-parameter", "const C = class<T> { m() {} };\n", ' lang="ts"', "typescript"),
    ("comment", "class /* x */ Foo { m() {} }\n", "", "javascript"),
    ("non-ascii-name", "class \u00dcber { m() {} }\n", "", "javascript"),
    # Review round 4: the gate read a `lang="tsx"` script with the TypeScript
    # grammar, where JSX in a class body is an ERROR, and said no alone.
    ("tsx-jsx-in-body", "class K { r() { return <div/>; } }\n", ' lang="tsx"', "tsx"),
]


@pytest.mark.parametrize("filename,language", CHANNELS)
@pytest.mark.parametrize("body,attrs,lang", [s[1:] for s in AFTER_THE_KEYWORD], ids=[s[0] for s in AFTER_THE_KEYWORD])
def test_what_follows_the_keyword_does_not_hide_the_class(filename, language, body, attrs, lang):
    got = _ids(parse_file(_script(body, attrs), filename, language))
    expected = _expected(body, lang)

    assert any(k.endswith("#method") for k in expected), expected
    for sid, parent in expected.items():
        assert got.get(sid, "MISSING") == parent, (sid, got)


def test_the_prefilter_never_refuses_a_class_spelling():
    """The keyword prefilter may say yes too often, never no: a no drops every
    class in the script silently. Every class body this file tests passes it."""
    from jcodemunch_mcp.parser.extractor import _CLASS_KEYWORD_RE

    bodies = [CLASS_TS, CLASS_JS, EXPRESSION, "$: C = (class { m() {} });\n", "const C = class\n{ m() {} };\n"]
    bodies += [s[1] for s in SPELLINGS] + [s[1] for s in AFTER_THE_KEYWORD]
    for body in bodies:
        assert _CLASS_KEYWORD_RE.search(body.encode()), body


# A class nested in a container. The gate's node walk prunes a container only
# where the generic walk gives its class an OWNER, which `_build` never emits.
NESTED = [
    ("function-declaration", "function f() { class K { k() {} } }\n", 0),
    ("generator", "function* g() { class K { k() {} } }\n", 0),
    ("object-method", "const o = { m() { class K { k() {} } } };\n", 0),
    ("arrow", "const f = () => { class K { k() {} } };\n", 1),
    ("function-expression", "const f = function () { class K { k() {} } };\n", 1),
    ("block", "if (x) { class K { k() {} } }\n", 1),
]


@pytest.mark.parametrize("filename,language", CHANNELS)
@pytest.mark.parametrize("body,parses", [n[1:] for n in NESTED], ids=[n[0] for n in NESTED])
def test_a_nested_class_is_published_where_a_script_file_publishes_it_as_a_root(
    filename, language, body, parses, monkeypatch
):
    import jcodemunch_mcp.parser.extractor as extractor

    calls = []
    real = extractor.parse_file

    def spy(content, fname, *a, **k):
        if "#script." in fname:
            calls.append(fname)
        return real(content, fname, *a, **k)

    monkeypatch.setattr(extractor, "parse_file", spy)
    got = _ids(parse_file(_script(body), filename, language))
    monkeypatch.setattr(extractor, "parse_file", real)
    roots = {k for k, v in _expected(body, "javascript").items() if v == "a#class" and k.endswith("#class")}

    assert len(calls) == parses, calls
    for sid in roots:
        assert got.get(sid) == "a#class", (sid, got)
        assert got.get(sid.replace("#class", ".k#method")) == sid, got


# A binding whose initializer only CONTAINS a class keeps its own symbol.
# Review round 5: the hand walk reads a `lang="tsx"` script with the TypeScript
# grammar, whose error recovery made `class K` the declarator's value, and an
# overlap-only `covers()` then silenced `e`, which `main` and `a.tsx` publish.
NESTED_IN_INITIALIZER = [
    ("tsx-jsx-arrow", "const e = <div onClick={() => { class K { k() {} } }} />;\n", ' lang="tsx"', "tsx"),
    ("tsx-jsx-function", "const e = <A render={function r() { class K { k() {} } }} />;\n", ' lang="tsx"', "tsx"),
    ("js-call-arrow", "const e = make(() => { class K { k() {} } });\n", "", "javascript"),
]


@pytest.mark.parametrize("filename,language", CHANNELS)
@pytest.mark.parametrize(
    "body,attrs,lang", [n[1:] for n in NESTED_IN_INITIALIZER], ids=[n[0] for n in NESTED_IN_INITIALIZER]
)
def test_a_binding_is_not_silenced_by_a_class_nested_in_its_initializer(filename, language, body, attrs, lang):
    got = _ids(parse_file(_script(body, attrs), filename, language))
    expected = _expected(body, lang)

    assert "e#constant" in expected, expected
    for sid, parent in expected.items():
        assert got.get(sid, "MISSING") == parent, (sid, got)


def test_a_svelte_prop_is_not_suppressed_by_a_class_nested_in_its_default():
    body = "export let e = <A render={() => { class K { k() {} } }} />;\n"
    got = _ids(parse_file(_script(body, ' lang="tsx"'), "a.svelte", "svelte"))
    expected = _expected(body, "tsx")

    assert got.get("e#property") == "a#class", got
    for sid, parent in expected.items():
        if sid.startswith("K"):
            assert got.get(sid, "MISSING") == parent, (sid, got)


# Review round 6: a name match stood in for "this class is the binding's
# value", so a nested class of the binding's OWN name still silenced it. The
# generic walk spans a bound class from its binder; a nested one starts after.
SAME_NAME_NESTED = [
    ("tsx-const", "const K = <A r={() => { class K { k() {} } }} />;\n", ' lang="tsx"', "tsx", "K#constant"),
    ("js-const", "const K = make(() => { class K { k() {} } });\n", "", "javascript", "K#constant"),
]


@pytest.mark.parametrize("filename,language", CHANNELS)
@pytest.mark.parametrize(
    "body,attrs,lang,binding", [n[1:] for n in SAME_NAME_NESTED], ids=[n[0] for n in SAME_NAME_NESTED]
)
def test_a_nested_class_of_the_bindings_own_name_does_not_silence_it(
    filename, language, body, attrs, lang, binding
):
    got = _ids(parse_file(_script(body, attrs), filename, language))
    expected = _expected(body, lang)

    assert binding in expected, expected
    for sid, parent in expected.items():
        assert got.get(sid, "MISSING") == parent, (sid, got)


def test_a_vue_export_let_holding_a_same_named_nested_class_keeps_its_binding():
    body = "export let K = <A r={() => { class K { k() {} } }} />;\n"
    got = _ids(parse_file(_script(body, ' lang="tsx"'), "a.vue", "vue"))

    assert got.get("K#variable") == "a#class", got
    assert got.get("K#class") == "a#class", got
    assert got.get("K.k#method") == "K#class", got
