"""The language Floors are the shipped counts, not an old reading of them.

Set on 2026-09-03, `languages.registry_min` and `languages.extensions_min`
stayed put while the registry grew (ASP.NET Web Forms, #1012; ERB, #1307; ten
XML-family mappings). A ">=" Floor that sits below what ships lets a removal
pass unseen, and the jcodemunch.com site prints its language count from this
Floor, so it undersold the product too.

Adding a language or an extension fails this test until the Floor is raised in
`harness/thresholds.json` (old setting to `history`). That is the point: the
Floor, the shipped count and the site's figure move together. STANDARD.md
criterion 10 states the rule.
"""

from __future__ import annotations

from harness import thresholds
from jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS, LANGUAGE_REGISTRY


def test_the_registry_floor_is_the_shipped_language_count():
    floor, shipped = thresholds.floor("languages.registry_min"), len(LANGUAGE_REGISTRY)
    assert floor == shipped, (
        f"languages.registry_min is {floor}, the registry ships {shipped}: raise the Floor in "
        "harness/thresholds.json (old setting to `history`), then update JCM_LANGUAGES in "
        "jcodemunch.com _facts.php (the site cannot read the registry)"
    )


def test_the_extensions_floor_is_the_shipped_extension_count():
    floor, shipped = (
        thresholds.floor("languages.extensions_min"),
        len(LANGUAGE_EXTENSIONS),
    )
    assert floor == shipped, (
        f"languages.extensions_min is {floor}, the registry ships {shipped} extensions: raise the "
        "Floor in harness/thresholds.json (old setting to `history`)"
    )
