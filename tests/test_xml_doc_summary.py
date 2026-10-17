"""Tier-1 summaries must not return the `<summary>` tag for XML doc comments.

`extract_summary_from_docstring` assumed "line 1 of a docstring is its first
sentence" -- true for Python, JSDoc, Go and Rust, false for XML documentation
comments, the standard in C# and AL (VB.NET is not indexed, and the F# parser
extracts no doc comment):

    /// <summary>          <- line 1 is markup
    /// Saves the order.   <- the actual sentence
    /// </summary>

⚠ Found while building the .NET fixtures, and NOT where it was first blamed. The
parser captures the docstring correctly; only the summary derived from it was
wrong, so the fix is in the summarizer and the blast radius is every
`///`-documented C# and AL symbol rather than generated `.designer.cs` files.

⚠ Summaries are scored by BM25 in `search_symbols`, so this was retrieval quality
on C# and AL codebases, not merely display text.
"""

import pytest

from jcodemunch_mcp.summarizer.batch_summarize import extract_summary_from_docstring


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "docstring,expected",
    [
        # the canonical multi-line form
        ("<summary>\nSaves the order.\n</summary>", "Saves the order."),
        # one-line form. ⚠ The WORSE half of the bug: this degraded to
        # "<summary>Saves the order." which reads as a formatting quirk.
        ("<summary>Saves the order.</summary>", "Saves the order."),
        # sibling tags must not leak in
        (
            "<summary>\nHeadContent control.\n</summary>\n<remarks>\nAuto-generated.\n</remarks>",
            "HeadContent control.",
        ),
        # attributes on the tag itself
        ('<summary xml:space="preserve">Saves it.</summary>', "Saves it."),
        # unterminated: taking everything after the tag still beats the tag
        ("<summary>\nSaves the order.", "Saves the order."),
        # casing varies in hand-written docs
        ("<SUMMARY>\nSaves the order.\n</SUMMARY>", "Saves the order."),
    ],
)
def test_xml_doc_summary_yields_the_sentence_not_the_tag(docstring, expected):
    assert extract_summary_from_docstring(docstring) == expected


def test_the_literal_tag_never_survives():
    """The single assertion that would have caught this in review."""
    for docstring in (
        "<summary>\nSaves the order.\n</summary>",
        "<summary>Saves the order.</summary>",
        "<summary>\nSaves the order.",
    ):
        assert "<summary>" not in extract_summary_from_docstring(docstring)


def test_multi_line_summary_is_joined_before_the_first_line_is_taken():
    """A summary wrapped for width is ONE sentence; taking line 1 would truncate
    it mid-thought, which is the same bug wearing different clothes."""
    docstring = "<summary>\nSaves the order and\nreturns a receipt.\n</summary>"
    assert (
        extract_summary_from_docstring(docstring)
        == "Saves the order and returns a receipt."
    )


@pytest.mark.parametrize(
    "docstring,expected",
    [
        # A reference keeps the identifier it names: that identifier is the
        # most searchable word in the sentence, and BM25 scores this field.
        ('<summary>Wraps <see cref="Foo"/> safely.</summary>', "Wraps Foo safely."),
        # documentation-id prefix and namespace dropped
        ('<summary>Gets the <see cref="T:System.String"/> name.</summary>', "Gets the String name."),
        # a method's parameter list dropped
        ('<summary>Calls <see cref="M:Ns.Svc.Run(System.Int32)"/> once.</summary>', "Calls Run once."),
        # generic arity and generic arguments dropped
        ('<summary>Fills <see cref="T:System.Collections.Generic.List`1"/>.</summary>', "Fills List."),
        ('<summary>Fills <see cref="List{T}"/>.</summary>', "Fills List."),
        ('<summary>Uses <paramref name="count"/> or <see langword="null"/>.</summary>', "Uses count or null."),
        ("<summary>Reads <typeparamref name='T'/> (<seealso cref='Bar'/>).</summary>", "Reads T (Bar)."),
        # a tag with CONTENT keeps the content and loses only the tag
        ('<summary>Use <c>Save()</c> then <see href="https://x">docs</see>.</summary>', "Use Save() then docs."),
        # a dropped tag must not fuse the words on either side of it
        ("<summary>Saves<para>the order</para>now.</summary>", "Saves the order now."),
    ],
)
def test_inline_tags_keep_the_text_they_carry(docstring, expected):
    assert extract_summary_from_docstring(docstring) == expected


@pytest.mark.parametrize(
    "docstring,expected",
    [
        # XML doc text is XML: an escaped angle bracket or ampersand is decoded
        ("<summary>Returns List&lt;int&gt; &amp; more.</summary>", "Returns List<int> & more."),
        ("<summary>Quotes &quot;x&quot;.</summary>", 'Quotes "x".'),
        # ...but hand-written docs leave comparisons unescaped, and a loose
        # `<[^>]+>` ate everything from one comparison to the next
        ("<summary>True when a < b and c > d.</summary>", "True when a < b and c > d."),
        ("<summary>True when a<b and c>d.</summary>", "True when a<b and c>d."),
    ],
)
def test_summary_text_is_decoded_and_comparisons_survive(docstring, expected):
    assert extract_summary_from_docstring(docstring) == expected


def test_empty_summary_element_yields_empty_not_the_tag():
    assert extract_summary_from_docstring("<summary></summary>") == ""
    assert extract_summary_from_docstring("<summary>\n</summary>") == ""


# ---------------------------------------------------------------------------
# Controls — every other convention must be byte-for-byte unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "docstring,expected",
    [
        ("Saves the order.\n\nLonger detail here.", "Saves the order."),
        ("Saves the order.", "Saves the order."),
        ("Saves the order", "Saves the order"),
        ("", ""),
        ("   ", ""),
        # Google-style Python
        ("Saves the order.\n\nArgs:\n    x: thing\n", "Saves the order."),
        # javadoc/KDoc: prose first, tags later
        ("Saves the order.\n@param x thing\n@return receipt", "Saves the order."),
    ],
)
def test_non_xml_conventions_are_unaffected(docstring, expected):
    assert extract_summary_from_docstring(docstring) == expected


def test_generics_in_prose_do_not_trigger_the_xml_path():
    """`List<int>` in ordinary prose must pass through."""
    assert (
        extract_summary_from_docstring("Returns a List<int> of ids.")
        == "Returns a List<int> of ids."
    )
    assert (
        extract_summary_from_docstring("Compares a < b and returns bool.")
        == "Compares a < b and returns bool."
    )


def _first_line_rule(docstring: str) -> str:
    """The pre-fix extractor, verbatim: what every non-XML docstring must still get."""
    if not docstring:
        return ""
    first_line = docstring.strip().split("\n")[0].strip()
    if "." in first_line:
        first_line = first_line[: first_line.index(".") + 1]
    return first_line[:120]


@pytest.mark.parametrize(
    "docstring",
    [
        # ⚠⚠ THE control that matters. The first draft guarded on `<summary`
        # appearing ANYWHERE, so a docstring that names the tag in prose was
        # unwrapped into a fragment -- this one became '.'.
        "Strips <summary>...</summary> wrappers from XML docs.",
        "Return the text inside a `<summary>` element, or the input unchanged.",
        # the summarizer's own docstring mentions the tag on its third line
        extract_summary_from_docstring.__doc__,
        "Parses the doc.\n\nHandles `<summary>Text</summary>` and <see cref='X'/>.",
        "Escapes &lt; and &amp; in output.",
        # an element that is not the first token does not make it XML doc
        "See <summary> below.\n<summary>Not this.</summary>",
        # ⚠⚠ prose that merely STARTS with a tag is not a doc comment with no
        # summary: Javadoc, JSDoc, PHPDoc and C keep these words. An "opens with
        # any element and has no <summary>" rule emptied every one of them.
        "<p>Html first.</p>\nMore.",
        "<b>Deprecated</b> use Foo instead.",
        "<code>null</code> if absent.",
        "<style> block is recorded as a constant symbol.",
        '<reference types="node" />',
        # ⚠⚠ ...and a `<summary>` MENTIONED after that prose is not one: the
        # unterminated-tag fallback turned this into 'element for the disclosure.'
        "<p>Renders a <summary> element for the disclosure.</p>",
        "<p>Uses <summary>x</summary> tags.</p>",
    ],
)
def test_a_docstring_that_is_not_xml_doc_is_unchanged_byte_for_byte(docstring):
    assert extract_summary_from_docstring(docstring) == _first_line_rule(docstring)


@pytest.mark.parametrize(
    "docstring",
    [
        # common on .NET interface implementations; summarised as the tag before
        "<inheritdoc/>",
        '<inheritdoc cref="IOrders.Save"/>',
        "<remarks>Only remarks.</remarks>",
        '<param name="x">The x.</param>\n<returns>A receipt.</returns>',
        '<inheritdoc/>\n<remarks>Also\nthis.</remarks>\n<exception cref="E">When.</exception>',
        # self-closing sections, and the rarer section names
        "<remarks/>",
        "<returns/>\n<value/>\n<example/>",
        "<exclude/>",
        "<filterpriority>2</filterpriority>",
        "<para>Some text.</para>",
        '<list type="bullet"><item>A</item></list>',
        # an EMPTY self-closing summary: once read as unterminated, which made
        # the section after it the summary
        '<summary/>\n<param name="x">The x value.</param>',
        "<summary />\n<returns>The number of rows</returns>",
    ],
)
def test_xml_doc_with_no_summary_element_falls_to_the_signature(docstring):
    """Markup is never a summary: Tier 1 answers "" and Tier 3 names the symbol."""
    from jcodemunch_mcp.parser.symbols import Symbol
    from jcodemunch_mcp.summarizer.batch_summarize import summarize_symbols_simple

    assert extract_summary_from_docstring(docstring) == ""
    sym = Symbol(
        id="a.cs::Save#method", file="a.cs", name="Save", qualified_name="Orders.Save",
        kind="method", language="csharp", signature="public void Save()", docstring=docstring,
    )
    (sym,) = summarize_symbols_simple([sym])
    assert sym.summary and "<" not in sym.summary


@pytest.mark.parametrize(
    "docstring,expected",
    [
        # spacing the author wrote is the author's; only a removed tag's gap moves
        ("<summary>Returns x ? y : z.</summary>", "Returns x ? y : z."),
        ("<summary>Takes a ( b ) value.</summary>", "Takes a ( b ) value."),
        ("<summary>Reads <c>x</c> , then y.</summary>", "Reads x , then y."),
    ],
)
def test_spacing_the_author_wrote_survives(docstring, expected):
    assert extract_summary_from_docstring(docstring) == expected


@pytest.mark.parametrize(
    "docstring,expected",
    [
        # a <summary> may follow documentation sections, and only those
        ("<remarks>R.</remarks>\n<summary>Saves.</summary>", "Saves."),
        ("<inheritdoc/>\n<summary>Saves.</summary>", "Saves."),
        ('<param name="x">X.</param><summary>Saves.</summary>', "Saves."),
    ],
)
def test_a_summary_after_documentation_sections_is_still_the_summary(docstring, expected):
    assert extract_summary_from_docstring(docstring) == expected


@pytest.mark.parametrize(
    "docstring,expected",
    [
        # an unterminated summary ends at the next section, not at the end
        ('<summary>Saves\n<param name="x">The x value.</param>', "Saves"),
        ("<summary>Saves\n<returns>A receipt.</returns>", "Saves"),
        # `<para>` and `<list>` nest INSIDE a summary, so they do not end it
        ("<summary>Saves <para>the order.</para>", "Saves the order."),
        # ⚠ A pinned weakness: prose that OPENS with the tag reads as an
        # unterminated summary and loses it; nothing tells the two apart
        ("<summary> element toggles the disclosure widget.", "element toggles the disclosure widget."),
    ],
)
def test_an_unterminated_summary_ends_at_the_next_section(docstring, expected):
    assert extract_summary_from_docstring(docstring) == expected


@pytest.mark.parametrize(
    "docstring",
    [
        # unterminated openers made the section pattern quadratic (26 s for the
        # second); it runs after parse_file, outside the parse budget
        "<remarks>" * 20000,
        "<param name='a'>" * 20000,
        "<summary>" * 20000,
        "<remarks>" * 450 + "<summary>x</summary>",
        "<p>" + "<summary>" * 20000,
    ],
    # the ids go into an environment variable, capped at 32767 chars on Windows
    ids=["remarks", "param", "summary", "remarks-then-summary", "p-then-summary"],
)
def test_hostile_markup_is_bounded(docstring):
    import time

    start = time.perf_counter()
    extract_summary_from_docstring(docstring)
    # generous: measured in milliseconds; the defect took tens of seconds
    assert time.perf_counter() - start < 1.0


def test_xml_doc_is_recognised_by_its_first_token():
    from jcodemunch_mcp.summarizer.xml_doc import is_xml_doc

    assert is_xml_doc("<summary>x</summary>")
    assert is_xml_doc("  \n<remarks>r</remarks><summary>x</summary>")
    assert is_xml_doc('<summary xml:space="preserve">x</summary>')
    assert not is_xml_doc("Strips <summary> tags.")
    assert not is_xml_doc("List<int> of ids.")
    assert not is_xml_doc("< summary>")
    assert not is_xml_doc("")


def test_the_120_char_cap_still_applies():
    long_sentence = "x" * 200
    assert len(extract_summary_from_docstring(f"<summary>{long_sentence}</summary>")) == 120
    assert len(extract_summary_from_docstring(long_sentence)) == 120


# ---------------------------------------------------------------------------
# End to end, against the vendored Microsoft fixture
# ---------------------------------------------------------------------------


def test_against_the_public_microsoft_designer_fixture():
    """The docstring that exposed this, from MIT-licensed Microsoft code.

    See tests/fixtures/dotnet/SOURCES.md -- `Site.Master.designer.cs` in
    microsoft/dotnet-framework-docker.
    """
    from pathlib import Path

    from jcodemunch_mcp.parser import parse_file

    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "dotnet"
        / "webforms_site.master.designer.cs"
    )
    symbols = parse_file(
        fixture.read_text(encoding="utf-8-sig"), "Site.Master.designer.cs", "csharp"
    )
    field = next(s for s in symbols if s.name == "HeadContent")

    # the parser's half was always correct
    assert "<summary>" in (field.docstring or "")
    # the summarizer's half now is too
    assert extract_summary_from_docstring(field.docstring) == "HeadContent control."


# ---------------------------------------------------------------------------
# AL: the second copy of the rule, now reading the same one
# ---------------------------------------------------------------------------


def _al_proc(doc_lines: list[str]):
    from jcodemunch_mcp.parser import parse_file

    source = "codeunit 50100 \"Doc Test\"\n{\n" + "".join(
        f"    /// {line}\n" for line in doc_lines
    ) + "    procedure Run()\n    begin\n    end;\n}\n"
    return next(s for s in parse_file(source, "DocTest.al", "al") if s.name == "Run")


@pytest.mark.parametrize(
    "doc_lines,expected",
    [
        (["<summary>", "Saves the order.", "</summary>"], "Saves the order."),
        # the parse-layer `.replace("<summary>", "")` was case-sensitive and
        # blind to attributes, so both of these summarised as the tag
        (["<Summary>Saves the order.</Summary>"], "Saves the order."),
        (['<summary xml:space="preserve">Saves it.</summary>'], "Saves it."),
        # a reference keeps its name, as for C#
        (['<summary>Posts <see cref="SalesHeader"/> now.</summary>'], "Posts SalesHeader now."),
        # no period: the old rule carried the `<param>` markup into the summary
        (["<summary>Saves the order</summary>", '<param name="x">The x</param>'], "Saves the order"),
        # a `///` comment that is not XML is untouched
        (["Saves the order. More detail."], "Saves the order."),
    ],
)
def test_an_al_doc_comment_summarises_through_the_shared_rule(doc_lines, expected):
    proc = _al_proc(doc_lines)
    assert extract_summary_from_docstring(proc.docstring) == expected


def test_an_al_docstring_keeps_its_xml_as_csharp_does():
    proc = _al_proc(["<summary>", "Saves the order.", "</summary>"])
    assert proc.docstring == "<summary> Saves the order. </summary>"


@pytest.mark.parametrize("language", ["csharp", "al"])
def test_an_empty_self_closing_summary_is_not_the_next_section(language):
    """`/// <summary/>` then `/// <param>`: through the parser and the summarizer,
    the symbol summarises as its signature, never as the parameter's text."""
    from jcodemunch_mcp.parser import parse_file
    from jcodemunch_mcp.summarizer.batch_summarize import summarize_symbols_simple

    doc = ["<summary/>", '<param name="x">The x value.</param>']
    if language == "al":
        sym = _al_proc(doc)
    else:
        sym = next(
            s for s in parse_file(_cs("\n".join(doc)), "Orders.cs", "csharp") if s.name == "Save"
        )
        assert "<summary/>" in sym.docstring
    (sym,) = summarize_symbols_simple([sym])
    assert sym.summary and "x value" not in sym.summary and "<" not in sym.summary


# ---------------------------------------------------------------------------
# History: an index built before the fix is repaired by the next index run
# ---------------------------------------------------------------------------

def _cs(doc: str) -> str:
    lines = "".join(f"        /// {line}\n" for line in doc.splitlines())
    return f"namespace N\n{{\n    public class Orders\n    {{\n{lines}        public void Save() {{ }}\n    }}\n}}\n"


def _al(doc: str) -> str:
    lines = "".join(f"    /// {line}\n" for line in doc.splitlines())
    return f'codeunit 50100 "Orders"\n{{\n{lines}    procedure Save()\n    begin\n    end;\n}}\n'


# Each stale value is what the PRE-fix code stored for that docstring. Only the
# first starts with `<summary`: a repair keyed on that spelling kept the rest.
_STALE_CASES = [
    pytest.param("Orders.cs", _cs("<summary>\nSaves the order.\n</summary>"),
                 "<summary>", "Saves the order.", id="cs-tag"),
    pytest.param("Orders.cs", _cs("<remarks>R.</remarks>\n<summary>Saves.</summary>"),
                 "<remarks>R.", "Saves.", id="cs-remarks-first"),
    pytest.param("Orders.al", _al('<summary>Posts <see cref="SalesHeader"/> now.</summary>'),
                 'Posts <see cref="SalesHeader"/> now.', "Posts SalesHeader now.", id="al-cref"),
    pytest.param("Orders.al", _al('<summary>Saves the order</summary>\n<param name="x">The x</param>'),
                 'Saves the order <param name="x">The x</param>', "Saves the order", id="al-param-tail"),
]


def _index_with_a_stale_summary(tmp_path, filename: str, source: str, stale: str, *, generation: int):
    """Index once, then rewrite the store as a pre-fix index would hold it."""
    import sqlite3

    from jcodemunch_mcp.tools.index_folder import index_folder

    src, store = tmp_path / "src", tmp_path / "store"
    src.mkdir()
    store.mkdir()
    (src / filename).write_text(source, encoding="utf-8")
    assert index_folder(str(src), use_ai_summaries=False, storage_path=str(store))["success"]

    (db,) = store.glob("*.db")
    with sqlite3.connect(db) as conn:
        assert conn.execute("UPDATE symbols SET summary = ? WHERE name = 'Save'", (stale,)).rowcount == 1
        conn.execute(
            "UPDATE meta SET value = ? WHERE key = 'parser_generation'", (str(generation),)
        )
    conn.close()

    def reindex_and_read() -> str:
        assert index_folder(str(src), use_ai_summaries=False, storage_path=str(store))["success"]
        with sqlite3.connect(db) as c:
            (summary,) = c.execute("SELECT summary FROM symbols WHERE name = 'Save'").fetchone()
        c.close()
        return summary

    return reindex_and_read


@pytest.mark.parametrize("filename,source,stale,fresh", _STALE_CASES)
def test_a_stale_summary_is_repaired_by_the_generation_reparse(tmp_path, filename, source, stale, fresh):
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 21
    reindex = _index_with_a_stale_summary(
        tmp_path, filename, source, stale, generation=PARSER_GENERATION - 1
    )
    assert reindex() == fresh


@pytest.mark.parametrize("filename,source,stale,fresh", _STALE_CASES)
def test_the_reparse_alone_would_keep_the_stale_summary(
    tmp_path, monkeypatch, filename, source, stale, fresh
):
    """Non-vacuity: the re-parse REUSES an unchanged file's stored summaries, so
    the bump repairs nothing unless reuse recomputes the docstring-derived ones."""
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION
    from jcodemunch_mcp.tools import _indexing_pipeline

    reindex = _index_with_a_stale_summary(
        tmp_path, filename, source, stale, generation=PARSER_GENERATION - 1
    )
    monkeypatch.setattr(_indexing_pipeline, "_stored_summary_was_free", lambda row: False)
    assert reindex() == stale


def test_a_paid_summary_on_an_al_empty_stub_survives_the_reparse(tmp_path):
    """An empty `<summary>` stub was NO docstring before, so with AI on it earned
    a paid AI summary. The stub must stay no docstring, and the re-parse must
    keep that summary rather than re-bill or downgrade it."""
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    reindex = _index_with_a_stale_summary(
        tmp_path, "Orders.al", _al("<summary>\n</summary>"),
        "Persists an order to the store.", generation=PARSER_GENERATION - 1,
    )
    assert reindex() == "Persists an order to the store."


def test_an_al_empty_stub_is_no_docstring():
    assert _al_proc(["<summary>", "</summary>"]).docstring == ""
    assert _al_proc(["<Summary></Summary>"]).docstring == ""
    # the old case-sensitive strip left this one as a docstring
    assert _al_proc(["<summary/>"]).docstring == ""
    assert _al_proc(["<summary>", "Saves.", "</summary>"]).docstring == "<summary> Saves. </summary>"


def test_reuse_keys_on_the_stored_docstring_not_the_new_parse():
    """A stored summary beside a STORED docstring is free to recompute (no AI
    summarizer is given a symbol with a docstring); one stored without may be a
    paid AI summary and is kept -- whatever the new parse now says, because the
    summary was produced against the docstring it was stored with."""
    from jcodemunch_mcp.tools._indexing_pipeline import preservable_summaries

    def row(name: str, summary: str, docstring: str = "", file: str = "a.cs") -> dict:
        return {"file": file, "name": name, "kind": "method", "summary": summary, "docstring": docstring}

    stored = [
        row("Tag", "<summary>", "<summary>Saves.</summary>"),
        row("Plain", "Saves the order.", "Saves the order."),
        row("AiSummarised", "Persists an order to the store."),
        row("NoSummary", ""),
        row("Changed", "Old.", file="b.cs"),
    ]
    assert preservable_summaries(stored, {"a.cs"}) == {
        ("a.cs", "AiSummarised", "method"): "Persists an order to the store.",
    }


@pytest.mark.parametrize("module", ["index_folder", "index_repo"])
def test_both_indexers_build_the_reuse_map_through_the_one_rule(module):
    """`index_repo` is covered end to end by nothing here, so a return to its own
    inline comprehension would pass every other test: the second-call-site shape.
    Each indexer must call `preservable_summaries` and build no reuse dict itself."""
    import ast
    import importlib
    import inspect

    tree = ast.parse(inspect.getsource(importlib.import_module(f"jcodemunch_mcp.tools.{module}")))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) == "preservable_summaries"
    ]
    assert calls, f"{module} no longer builds its reuse map through preservable_summaries"
    inline = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.DictComp)
        and "summary" in ast.unparse(node.value)
    ]
    assert not inline, f"{module} builds a summary reuse dict of its own: {ast.unparse(inline[0])}"
