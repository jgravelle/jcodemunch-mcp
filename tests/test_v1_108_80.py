"""Regression tests for v1.108.80 — dataclass/Pydantic field extraction (#355).

Field-centric Python classes (dataclass / attrs / Pydantic) now surface their
annotated class-body assignments as `field` child symbols, so get_file_outline
exposes the class contract instead of just the class name.
"""

import os
import tempfile

from jcodemunch_mcp.parser.extractor import parse_file


def _fields(src: str, class_name: str | None = None):
    syms = parse_file(src, "m.py", "python")
    out = [s for s in syms if s.kind == "field"]
    if class_name:
        out = [s for s in out if s.qualified_name.startswith(f"{class_name}.")]
    return out


class TestDataclassFields:
    def test_dataclass_fields_extracted(self):
        src = (
            "from dataclasses import dataclass, field\n\n"
            "@dataclass\n"
            "class TableSpec:\n"
            "    key: str\n"
            "    tag: str\n"
            "    cols: list[str] = field(default_factory=list)\n"
        )
        fs = _fields(src)
        assert [f.name for f in fs] == ["key", "tag", "cols"]
        cols = fs[2]
        assert cols.kind == "field"
        assert cols.signature == "cols: list[str] = field(default_factory=list)"
        assert cols.parent.endswith("::TableSpec#class")
        assert cols.line == 7

    def test_classvar_is_not_a_field(self):
        src = (
            "from dataclasses import dataclass\n"
            "from typing import ClassVar\n\n"
            "@dataclass\n"
            "class C:\n"
            "    x: int\n"
            "    REGISTRY: ClassVar[dict] = {}\n"
        )
        assert [f.name for f in _fields(src)] == ["x"]

    def test_frozen_dataclass_call_decorator(self):
        src = (
            "from dataclasses import dataclass\n\n"
            "@dataclass(frozen=True, slots=True)\n"
            "class P:\n"
            "    a: int\n"
            "    b: str = 'x'\n"
        )
        assert [f.name for f in _fields(src)] == ["a", "b"]

    def test_pydantic_basemodel_subclass(self):
        src = (
            "import pydantic\n\n"
            "class Model(pydantic.BaseModel):\n"
            "    name: str\n"
            "    age: int = 0\n"
        )
        fs = _fields(src)
        assert [f.name for f in fs] == ["name", "age"]

    def test_attrs_define_decorator(self):
        src = (
            "from attrs import define\n\n"
            "@define\n"
            "class A:\n"
            "    n: int\n"
            "    m: str = 'y'\n"
        )
        assert [f.name for f in _fields(src)] == ["n", "m"]

    def test_a_plain_classs_state_is_indexed_and_a_constant_is_not_a_field(self):
        """Was `test_plain_class_fields_not_extracted`, which asserted `== []`:
        #355 left a plain class's attributes alone on purpose. jjg reversed that
        on 2026-09-19 (#784), so the absence it pinned is the defect now. Retired
        in `harness/retired.json`.

        ⚠ The half of the old docstring that survives: a field must not be
        conflated with a CONSTANT. `MAX` is state and is not a `field`.
        """
        src = (
            "class Plain:\n"
            "    MAX: int = 5\n"
            "    name: str = 'z'\n"
        )
        assert [f.name for f in _fields(src)] == ["name"]
        kinds = {s.name: s.kind for s in parse_file(src, "m.py", "python")}
        assert kinds == {"Plain": "class", "MAX": "constant", "name": "field"}

    def test_methods_still_extracted_once(self):
        src = (
            "from dataclasses import dataclass\n\n"
            "@dataclass\n"
            "class C:\n"
            "    x: int\n"
            "    def m(self):\n"
            "        return self.x\n"
        )
        syms = parse_file(src, "m.py", "python")
        methods = [s for s in syms if s.kind == "method"]
        fields = [s for s in syms if s.kind == "field"]
        assert [s.name for s in methods] == ["m"]
        assert [s.name for s in fields] == ["x"]

    def test_outline_surfaces_fields(self):
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp.tools.get_file_outline import get_file_outline

        d = tempfile.mkdtemp()
        with open(os.path.join(d, "spec.py"), "w", encoding="utf-8") as fh:
            fh.write(
                "from dataclasses import dataclass, field\n\n"
                "@dataclass\n"
                "class TableSpec:\n"
                "    key: str\n"
                "    cols: list[str] = field(default_factory=list)\n"
            )
        store = os.path.join(d, "store")
        res = index_folder(d, use_ai_summaries=False, storage_path=store)
        out = get_file_outline(repo=res["repo"], file_path="spec.py", storage_path=store)
        kinds = {s["name"]: s["kind"] for s in out["symbols"]}
        assert kinds.get("key") == "field"
        assert kinds.get("cols") == "field"
        # field nests under the class
        field_row = next(s for s in out["symbols"] if s["name"] == "cols")
        assert field_row["parent"].endswith("::TableSpec#class")

    def test_file_summary_counts_fields_separately(self):
        from jcodemunch_mcp.summarizer.file_summarize import _heuristic_summary

        src = (
            "from dataclasses import dataclass\n\n"
            "@dataclass\n"
            "class C:\n"
            "    x: int\n"
            "    y: int\n"
            "    def m(self):\n"
            "        return self.x\n"
        )
        syms = parse_file(src, "m.py", "python")
        summary = _heuristic_summary("m.py", syms)
        # ⚠ "1 methods" until #760. This test's subject is that fields are
        # counted SEPARATELY, and the plural was incidental to it; naming each
        # kind in the summary forced a plural rule (`property` -> `properties`
        # is irregular), and applying it to `method` is the same rule, not a
        # second one.
        # ⚠⚠ The whole string, because `"1 method" in "1 methods"` is True --
        # the first replacement for the old `"1 methods"` assertion passed on
        # the PRE-change tree too and discriminated nothing.
        assert summary == "Defines C class (1 method, 2 fields)"
