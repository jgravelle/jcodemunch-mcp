"""XML documentation comments (C# and AL): the one place they are read.

These comments put MARKUP on their first line, so "the first line is the first
sentence" -- true for Python, JSDoc, Go and Rust -- yields the literal string
`<summary>` for every `///`-documented symbol. This module unwraps the
`<summary>` element into the sentence it holds.

C# and AL are the indexed languages whose parsers keep these comments. VB.NET
is not indexed, and the F# parser extracts no doc comment; either would read
through here once it does.

⚠⚠ **Gated on the docstring OPENING with an XML element, never on `<summary`
appearing anywhere.** A substring guard sent every docstring that merely
MENTIONS the tag in prose down this path -- including the docstring of the
function that calls this one -- and summarised it as a fragment.

⚠ Stdlib only, a leaf: the summarizer reads summaries through it. A change
here reaches an existing index because the re-index reuse path recomputes
every docstring-derived summary (`tools/_indexing_pipeline.preservable_summaries`).
Parsers keep the XML in `docstring` and do NOT strip it themselves -- the AL
parser's own `.replace("<summary>", "")` was a second copy of these rules, and
removing it is why this is one.
"""

import html
import re

# The docstring's first non-blank token opens an element: `<summary>`,
# `<remarks>`, `<summary xml:space="preserve">`. `List<int>` and `a < b` do not.
_OPENS_WITH_ELEMENT_RE = re.compile(r"\s*<[A-Za-z][\w:.-]*(?=[\s/>])")

_SUMMARY_OPEN_RE = re.compile(r"<summary\b[^>]*>", re.I)
_SUMMARY_CLOSE_RE = re.compile(r"</summary\s*>", re.I)

# ⚠⚠ Every pattern here runs over at most this many characters. The section
# pattern pairs a lazy `.*?` with a backreference, which is QUADRATIC on
# unterminated openers (`"<param name='a'>" * 20000` took 26 s), and this runs
# after `parse_file`, outside `JCODEMUNCH_PARSE_BUDGET_SECONDS`. A summary is
# one sentence at the top; nothing past this point can be part of it.
_SCAN_LIMIT = 4096

# The XML documentation sections that are NOT a summary. A docstring made of
# nothing else (`<inheritdoc/>`, a lone `<remarks>`, `<param>`s) has no sentence
# to offer, and a `<summary>` may only follow these (`<remarks>` first is legal).
# ⚠⚠ A NAMED set, never "any element": `<b>Deprecated</b> use Foo`,
# `<code>null</code> if absent` and `<p>Html first.</p>` are prose in Javadoc,
# JSDoc, PHPDoc and C, and must keep their words -- and a `<summary>` MENTIONED
# after such prose (`<p>Renders a <summary> element.</p>`) is not one.
_XML_DOC_SECTIONS = (
    "remarks|param|typeparam|returns|exception|value|example|permission|seealso"
    "|para|list|filterpriority|exclude|inheritdoc|include|threadsafety"
)
_XML_DOC_SECTION_RE = re.compile(
    rf"<(?P<tag>{_XML_DOC_SECTIONS})\b[^>]*(?<!/)>.*?</(?P=tag)\s*>"
    rf"|<(?:{_XML_DOC_SECTIONS})\b[^>]*/>",
    re.DOTALL | re.I,
)
# Where an UNTERMINATED `<summary>` ends: the next section that can only stand
# beside a summary. `<para>` and `<list>` are left out because they also nest
# INSIDE one.
_NEXT_SECTION_RE = re.compile(
    r"<(?:remarks|param|typeparam|returns|exception|value|example|permission"
    r"|seealso|filterpriority|exclude|inheritdoc|include|threadsafety)\b",
    re.I,
)

# A self-closing reference names an identifier, and that identifier is the
# most searchable word in the sentence: `<see cref="Foo"/>`,
# `<paramref name="x"/>`, `<see langword="null"/>`. Keep the name, drop the tag.
_SELF_CLOSING_REF_RE = re.compile(
    r"<(?:see|seealso|paramref|typeparamref)\b([^<>]*?)/>", re.I
)
_ATTR_RE = re.compile(r"""\b(cref|langword|name)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_ATTR_PREFERENCE = ("cref", "langword", "name")

# A REAL tag: a name, then only `attr="value"` pairs. `a < b and c > d` is not
# one -- a looser `<[^>]+>` ate everything between the two comparisons.
_TAG_RE = re.compile(
    r"""</?[A-Za-z][\w:.-]*(?:\s+[\w:.-]+\s*=\s*(?:"[^"]*"|'[^']*'))*\s*/?>"""
)
# Where a tag stood. It becomes a space between two words, so they do not fuse,
# and nothing where it sits against punctuation -- and ONLY there: spacing the
# author wrote (`x ? y : z`, `a ( b )`) is never touched.
_MARK = "\x00"
_MARK_BEFORE_CLOSER_RE = re.compile(r"\x00+(?=[.,;:!?)\]])")
_MARK_AFTER_OPENER_RE = re.compile(r"(?<=[(\[])\x00+")


def _cref_name(cref: str) -> str:
    """`M:Ns.Type.Run(System.Int32)` -> `Run`; `T:System.String` -> `String`."""
    name = re.sub(r"^[A-Za-z]:", "", cref.strip())  # documentation-id prefix
    name = name.split("(", 1)[0]  # a method's parameter list
    name = re.sub(r"\{.*\}$", "", name)  # generic arguments, `List{T}`
    name = name.rsplit(".", 1)[-1]
    return name.split("`", 1)[0]  # generic arity, `List`1`


def _reference_text(match: "re.Match[str]") -> str:
    attrs = {
        m.group(1).lower(): m.group(2) if m.group(2) is not None else m.group(3)
        for m in _ATTR_RE.finditer(match.group(1))
    }
    for key in _ATTR_PREFERENCE:
        value = attrs.get(key)
        if value:
            name = _cref_name(value) if key == "cref" else value.strip()
            return f"{_MARK}{name}{_MARK}"
    return _MARK


def is_xml_doc(docstring: str) -> bool:
    """True when the docstring is XML documentation, i.e. OPENS with an element."""
    return bool(docstring) and _OPENS_WITH_ELEMENT_RE.match(docstring) is not None


def unwrap_xml_doc_summary(docstring: str) -> str:
    """Return the sentence inside an XML doc comment's `<summary>`.

    Inline references keep the identifier they name, other inline tags are
    dropped, entities are decoded and a summary wrapped for width is joined
    into one line. A `<summary>` counts only when nothing but documentation
    sections comes before it; otherwise it is prose that mentions the tag. An
    empty `<summary/>` returns "". ⚠ A docstring that OPENS with `<summary>` is
    read as XML documentation even with no closing tag, so prose that starts
    with the tag itself (`<summary> element toggles...`) loses it; nothing
    tells the two apart. A docstring with no such `<summary>` is returned
    byte-for-byte, unless it
    consists ONLY of documentation sections (`<inheritdoc/>`, a lone
    `<remarks>`, `<param>`s): that returns "" -- markup is never a summary, and
    an empty Tier 1 answer hands the symbol to the signature fallback.
    """
    if not is_xml_doc(docstring):
        return docstring

    head = docstring[:_SCAN_LIMIT]
    opening = _SUMMARY_OPEN_RE.search(head)
    if opening is None or _XML_DOC_SECTION_RE.sub("", head[: opening.start()]).strip():
        only_sections = len(docstring) <= _SCAN_LIMIT and not _XML_DOC_SECTION_RE.sub(
            "", docstring
        ).strip()
        return "" if only_sections else docstring

    # `<summary/>` is an EMPTY summary, not an unterminated one: read as the
    # latter, the `<param>` after it became the symbol's summary.
    if opening.group().endswith("/>"):
        return ""

    body = head[opening.end():]
    # Unterminated tag: better to take the text after it, up to the next
    # section, than to fall back to the first line, which is the tag itself.
    closing = _SUMMARY_CLOSE_RE.search(body)
    if closing is None:
        following = _NEXT_SECTION_RE.search(body)
        inner = body[: following.start()] if following else body
    else:
        inner = body[: closing.start()]
    inner = inner.replace(_MARK, "")
    inner = _SELF_CLOSING_REF_RE.sub(_reference_text, inner)
    inner = html.unescape(_TAG_RE.sub(_MARK, inner))
    inner = _MARK_AFTER_OPENER_RE.sub("", _MARK_BEFORE_CLOSER_RE.sub("", inner))
    return " ".join(inner.replace(_MARK, " ").split())
