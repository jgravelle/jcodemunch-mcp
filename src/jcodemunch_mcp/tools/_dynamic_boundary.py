"""Which recorded dynamic-import sites can reach a file (#876, LEDGER L-70).

`parser/imports.py` records a Python dynamic import whose target is not a
literal as a marker edge carrying a `dynamic_scope`:

- ``package``: built from the loader's own package name (the #569 shape), so it
  reaches files under the loader's directory;
- ``prefix:<dotted>``: a literal module prefix (`f"adapters.{name}"`) or
  `X.__name__` of an imported module, so it reaches files under that path;
- ``opaque``: a name computed from data, which could reach anything.

⚠⚠ **This is THE rule, and every absence claim reads it.** `get_blast_radius`
refuses an empty walk when a scoped site reaches the file; the dead-code tools
(`find_dead_code`, `get_dead_code_v2`, `check_delete_safe`) refuse to call such
a file dead. Before L-70 only the blast radius read it, so the same module was
"cannot prove nothing depends on it" in one tool and "provably unreachable,
confidence 1.0" in another. An opaque site is DISCLOSED and never refuses
(jjg, 2026-09-29): a registry-driven loader exists in most repos, and a
refusal that fires on every result teaches people to ignore it.

⚠ ``prefix:`` matches the dotted path at any directory depth, so
``prefix:adapters`` reaches every ``*/adapters/*.py``. That over-reaches, which
is the safe direction for an absence claim: a file wrongly treated as reachable
loses a proof it might have had; one wrongly treated as unreachable is
published dead.
"""

from __future__ import annotations

import posixpath

#: How many site files a refusal or disclosure names; the total rides beside it.
FILES_CAP = 10

_PY = (".py", ".pyi")


class DynamicBoundary:
    """The recorded dynamic-import sites of one index, read once."""

    __slots__ = ("_scoped", "opaque_sites")

    def __init__(self, imports) -> None:
        self._scoped: list[tuple[str, str]] = []
        opaque: set[str] = set()
        for site, edges in (imports or {}).items():
            for e in edges or []:
                if not (isinstance(e, dict) and e.get("dynamic_unresolved")):
                    continue
                scope = e.get("dynamic_scope") or "opaque"
                if scope == "opaque":
                    opaque.add(site)
                else:
                    self._scoped.append((site, scope))
        self.opaque_sites = frozenset(opaque)

    def __bool__(self) -> bool:
        return bool(self._scoped or self.opaque_sites)

    def reaching(self, file: str) -> list[str]:
        """Sites whose scope can load ``file``, sorted; [] for a non-Python file."""
        if not self._scoped or not file.endswith(_PY):
            return []
        probe = "/" + file
        out: set[str] = set()
        for site, scope in self._scoped:
            if scope == "package":
                pkg = posixpath.dirname(site)
                if not pkg or file.startswith(pkg + "/"):
                    out.add(site)
            elif scope.startswith("prefix:"):
                path = scope[len("prefix:"):].replace(".", "/")
                if path and ("/" + path + "/" in probe or probe.endswith("/" + path + ".py")):
                    out.add(site)
        return sorted(out)

    def opaque(self, excluding=()) -> list[str]:
        """Opaque sites, sorted, minus any already counted as reaching."""
        return sorted(self.opaque_sites - set(excluding))

    def disclosure(self, excluding=()) -> dict | None:
        """The `dynamic_imports_unfollowed` block, or None when there is nothing to say."""
        opaque = self.opaque(excluding)
        if not opaque:
            return None
        return {
            "files": opaque[:FILES_CAP],
            "files_total": len(opaque),
            "note": (
                f"{len(opaque)} file(s) import a module whose name is computed from data "
                "(a registry, config or argument), which static analysis cannot follow. "
                "This result does not account for them."
            ),
        }
