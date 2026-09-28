"""A C++ header whose classes hold only prototypes is read as C++ (LEDGER L-52).

A `.h` is parsed with both grammars and the better parse wins. The C
grammar reads `namespace n { class A { void f(); }; }` WITHOUT an error:
`namespace n` becomes a function returning `namespace`, and `class A` a
function nested in it. With both parses clean, the tie was broken by symbol
COUNT, and the misparse has more symbols than the class does, so the header
published `n#function`, `n.A#function` and `f#function` instead of
`n.A#class` and `n.A.f#method`. A declaration-only header, which is most
headers, lost every class it declares.

The property: a `.h` publishes what the same text publishes as `.cpp` when
its C++ parse holds a construct only C++ has, and what it publishes as `.c`
when it is C.
"""

from __future__ import annotations

import pytest

import jcodemunch_mcp.config as config
from jcodemunch_mcp.parser.extractor import parse_file

CPP_HEADERS = {
    "namespaced-class": "namespace n { class A { void f(); }; }\n",
    "class": "class A { void f(); int g(int x); };\n",
    "access-specifier": "class A {\n public:\n  void f();\n private:\n  void g();\n};\n",
    "namespaced-struct": "namespace n {\nstruct S {\n  void f();\n};\n}  // namespace n\n",
    "template-class": "template <class T>\nclass Box {\n  T get() const;\n  void set(T v);\n};\n",
    "namespace-prototypes": "namespace n {\nvoid f();\nint g(int);\n}\n",
    # A Qt `signals:` section costs the C++ parse an ERROR and C none, so an
    # equal-errors-only rule still chose C here (review of L-52).
    "qt-signals": "namespace n {\nclass A {\nsignals:\n void g();\n void f();\n};\n}\n",
    "include-guard":"#ifndef A_H\n#define A_H\nnamespace n {\nclass A {\n public:\n  void f();\n};\n}\n#endif\n",
}

C_HEADERS = {
    "struct-and-prototype": "struct S { int x; };\nvoid f(int);\n",
    "class-in-a-comment": "/* the class of errors */\nstruct S { int x; };\nint g(void);\n",
    "typedef": "typedef struct S S;\nint g(void);\nstatic inline int h(int x) { return x; }\n",
    # hiredis's `alloc.h` shape: C and C++ publish different rows for this
    # body, so the case fails if `extern "C"` ever counts as C++ (review).
    "extern-c-guard": (
        "#ifdef __cplusplus\nextern \"C\" {\n#endif\n"
        "typedef struct allocFuncs {\n    void *(*mallocFn)(size_t);\n    void (*freeFn)(void*);\n} allocFuncs;\n"
        "allocFuncs setAllocators(allocFuncs *ha);\n"
        "extern allocFuncs allocFns;\n"
        "static inline void *hi_malloc(size_t size) {\n    return allocFns.mallocFn(size);\n}\n"
        "#ifdef __cplusplus\n}\n#endif\n"
    ),
}


@pytest.fixture(autouse=True)
def _languages_on(monkeypatch):
    # This box's config can disable languages; the gate is read through
    # config's globals, so patch it there.
    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source: str, filename: str, language: str):
    return sorted(
        (s.id.split("::", 1)[1], (s.parent or "").split("::", 1)[-1], s.kind)
        for s in parse_file(source, filename, language)
    )


@pytest.mark.parametrize("case", sorted(CPP_HEADERS))
def test_a_cpp_header_publishes_what_the_cpp_file_publishes(case):
    source = CPP_HEADERS[case]
    expected = _rows(source, "a.cpp", "cpp")
    assert expected, source
    assert _rows(source, "a.h", "cpp") == expected


@pytest.mark.parametrize("case", sorted(C_HEADERS))
def test_a_c_header_publishes_what_the_c_file_publishes(case):
    """The other direction: a C header is still read as C."""
    source = C_HEADERS[case]
    expected = _rows(source, "a.c", "c")
    assert expected, source
    assert _rows(source, "a.h", "cpp") == expected
