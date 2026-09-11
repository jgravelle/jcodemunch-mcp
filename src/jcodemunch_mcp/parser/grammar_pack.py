"""Which generation of tree-sitter-language-pack is installed, and what that costs.

⚠ The pin is `<1.0.0` and this module exists for the install that overrode it
(#608, @kecsap: `pip install -U tree-sitter-language-pack` works and nothing
checked). The two generations differ in KIND, not in version number:

- 0.x (`bundled`): every grammar ships in the wheel; parsing is local.
- 1.x (`download`): the wheel ships no grammar. `get_parser` fetches each one
  over the network from a remote manifest into a cache directory at first
  parse (#382 and the 2026-09-11 probe: 68 shared libraries for our registry,
  16 s), and the nim grammar changed upstream so our extractor reads it as
  empty. ⚠ Three registry names are absent from the 1.x manifest
  (`autohotkey`, `ejs`, `verse`) and #382 recorded them as dropped languages;
  the 2026-09-11 probe found all three are parsed by our OWN regex extractors
  and never ask tree-sitter for a grammar, so nim is the one language 1.x
  loses. 1.17.0's config has a `cache_dir` override and no offline switch.
- `absent`: no pack at all; nothing parses.

⚠ The extractor swallowed every grammar failure as `[]` ("indexed for text
search only"), so on a `download` pack an unavailable grammar, and on an
airgapped box EVERY grammar, produced zero symbols with no warning anywhere.
Failures are recorded here per grammar name, once, by the `get_parser`
wrapper below that every loader site imports, and every surface that reports
capability (`index_folder` warnings, the capability certificate,
`install-status`) reads this module rather than re-deriving the rule.

⚠ Nothing here imports the pack's network-touching API: `manifest_languages()`
is a remote call, so the unavailable-language list is what the extractor SAW
fail, never a lookup we performed. A leaf: stdlib and importlib.metadata only.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

PACKAGE = "tree-sitter-language-pack"

_failures: dict[str, str] = {}
_lock = threading.Lock()


def pack_version() -> Optional[str]:
    """Installed version string, or None when the distribution is absent."""
    import importlib.metadata as md

    try:
        return md.version(PACKAGE)
    except Exception:
        logger.debug("%s distribution not readable; reporting absent", PACKAGE, exc_info=True)
        return None


def generation(version: Optional[str] = "<live>") -> str:
    """`bundled` (0.x), `download` (1.x and later) or `absent`.

    Derived from the major alone: 1.0.0 is the release that stopped bundling
    grammars, and every later major keeps the download model.
    """
    if version == "<live>":
        version = pack_version()
    if not version:
        return "absent"
    head = str(version).split(".", 1)[0]
    if not head.isdigit():
        return "absent"
    return "download" if int(head) >= 1 else "bundled"


def cache_dir() -> Optional[str]:
    """The pack's grammar cache directory on a download-generation pack, else None.

    Read from the pack's own API (a local path computation, no network); None
    when the API is absent, which is every 0.x release.
    """
    try:
        import tree_sitter_language_pack as tslp  # type: ignore

        fn = getattr(tslp, "cache_dir", None)
        return str(fn()) if callable(fn) else None
    except Exception:
        logger.debug("grammar pack cache_dir unreadable", exc_info=True)
        return None


def get_parser(name: str):
    """The pack's `get_parser`, with a failure recorded per grammar name before it re-raises.

    ⚠ THE ONE IMPORT SITE for the pack's parser loader (review of #608, round 1):
    the extractor has some forty `get_parser` calls across its dedicated
    parsers, and wiring the record at four of them left nim, the one language
    1.x loses, unrecorded. Every site imports this name instead, so a site
    written next inherits the rule; `tests/test_language_pack_generation.py`
    fails on a bare `from tree_sitter_language_pack import get_parser`
    anywhere else under `src/`. Re-raises unchanged, so every caller's own
    handling is what it was.
    """
    from tree_sitter_language_pack import get_parser as _real  # type: ignore

    try:
        return _real(name)
    except Exception as exc:
        record_failure(name, exc)
        raise


def record_failure(language: str, exc: BaseException) -> None:
    """Remember that `language`'s grammar could not be loaded, once per language."""
    entry = f"{type(exc).__name__}: {str(exc)[:200]}"
    with _lock:
        first = language not in _failures
        _failures[language] = entry
    if first:
        logger.warning(
            "grammar for %s unavailable (%s); files in that language are indexed for text search only",
            language, entry,
        )


def failures() -> dict[str, str]:
    """language -> `ExceptionType: message`, for every grammar that failed to load."""
    with _lock:
        return dict(_failures)


def reset_failures() -> None:
    with _lock:
        _failures.clear()


def notice() -> Optional[dict]:
    """The facts an install must disclose, or None on a bundled pack with no failures.

    ⚠ None means "nothing to say", so a 0.x result stays byte-identical.
    """
    version = pack_version()
    gen = generation(version)
    failed = failures()
    if gen == "bundled" and not failed:
        return None
    block: dict = {"generation": gen, "version": version, "grammar_failures": failed}
    if gen == "download":
        block["cache_dir"] = cache_dir()
        block["fetches_grammars_over_network"] = True
    return block


def warnings_for(block: dict) -> list[str]:
    """Human-readable warnings for `block` (from `notice()`)."""
    out: list[str] = []
    gen = block.get("generation")
    if gen == "download":
        out.append(
            f"{PACKAGE} {block.get('version')} is a download-generation pack: it ships no grammars and "
            f"fetches each one over the network at first parse into {block.get('cache_dir') or 'its cache directory'}. "
            "Airgapped installs parse nothing on it; the shipped pin is <1.0.0 (README, Security section)."
        )
    elif gen == "absent":
        out.append(f"{PACKAGE} is absent from this install: no file is parsed for symbols.")
    failed = block.get("grammar_failures") or {}
    if failed:
        names = ", ".join(sorted(failed))
        out.append(
            f"grammar unavailable for: {names} (files in these languages are indexed for text search only)"
        )
    return out


def attach(result: dict) -> None:
    """Add `grammar_pack` and its warnings to an index result; no-op when there is nothing to say."""
    block = notice()
    if block is None:
        return
    result["grammar_pack"] = block
    extra = warnings_for(block)
    if extra:
        result.setdefault("warnings", [])
        result["warnings"].extend(extra)
