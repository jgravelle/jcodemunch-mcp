"""Can a name-based reference search see this symbol at all? (#714)

THE ONE ANSWER to that question, because the consumers that ask it are the ones
that recommend deleting things.

Most symbols are invoked by writing their name: `foo()` contains `foo`, so an
absence of the token `foo` across the corpus is evidence about `foo`. A whole
class of members is not invoked that way. A C# operator is invoked by writing
`a + b`; an indexer by writing `a[0]`; a conversion operator by writing
`(string)a` or nothing at all. The declaration's name -- `operator +`,
`this[]`, `explicit operator string` -- **cannot appear at any call site by
construction**, so "no references found" carries no information about it.

⚠⚠ This matters because the absence is not merely unhelpful, it is CONFIDENT.
Measured on a two-file C# corpus where `Vec.cs` declares the members and
`Use.cs` uses every one of them, `check_delete_safe` returned
`internal_uses_blocking` for the ordinary method and **`safe_to_delete` at
confidence 1.0** for `operator +` and `this[]`, with the recommendation "No
callers or refs found." The tool was right about the ordinary method and
maximally wrong about the operator, for the same input.

⚠ The test is the NAME, not the language. A name that is not a plain identifier
cannot be a call-site token in any language this project indexes, which is why
this asks a property of the string rather than keeping a list of node types.
Python's `__add__` is the opposite case and is deliberately NOT covered: it is a
valid identifier, it does appear in the corpus when written explicitly, and
whether an implicit `a + b` should count is a different question with a
different answer (a language-aware one). Claiming it here would be guessing.
"""

import re


# A call site can only carry a name the language's lexer would produce as one
# token. Anything with a space, a bracket or an operator character is a
# DECLARATION spelling that no call site repeats.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Identifier-shaped member names a language FORBIDS writing at a call site, so
#: the string test above cannot see them. ⚠ A fact of the language, never a
#: judgement about how a name is usually called: Swift rejects an explicit
#: `x.deinit()` at compile time, and the runtime calls it on every release
#: (#754). Python's `__add__` stays out for the reason the module docstring
#: gives -- it CAN be written, so its absence is evidence.
_NEVER_WRITTEN_AT_A_CALL_SITE: dict[str, frozenset[str]] = {
    "swift": frozenset({"deinit"}),
}


def name_can_appear_at_a_call_site(name: str, language: str | None = None) -> bool:
    """False when no reference search keyed on this name can ever find a use.

    Deliberately conservative in the SAFE direction: an unrecognised or empty
    name returns False, so a symbol we cannot reason about never earns an
    absence claim. The cost of a False here is a refused verdict; the cost of a
    wrong True is a deletion. `language` adds the names a language forbids
    writing at a call site; without it only the string is asked.
    """
    if not name:
        return False
    # A qualified name (`Foo.Bar`, `math_utils::multiply`) still ends in an
    # identifier that a call site writes, so it is reachable.
    tail = re.split(r"[.:]{1,2}", name)[-1]
    if tail in _NEVER_WRITTEN_AT_A_CALL_SITE.get(language or "", frozenset()):
        return False
    return bool(_IDENTIFIER.match(tail))

