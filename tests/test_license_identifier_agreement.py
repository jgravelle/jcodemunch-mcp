"""Every surface that declares our license must name the same identifier.

#517 (@marcelruhf) switched packaging metadata to a PEP 639 expression so a
commercial user could allowlist the license BY IDENTIFIER rather than by the
full LICENSE text PyPI was publishing as `info.license`. That fixed the surface
they hit and left two others declaring `LicenseRef-Dual-Use` — no product
prefix, no version — so an allowlist keyed on the identifier still needed two
entries. Same defect as the one that was fixed, one surface over.

The version suffix is deliberate: LICENSE 1.2 must produce a NEW identifier, so
consent to 1.1's terms is not silently inherited by terms nobody read. That only
holds if the suffix tracks the file, which is the second assertion here.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
LICENSE = REPO_ROOT / "LICENSE"

# SPDX 3.0 §10.1: LicenseRef-[idstring], idstring = [A-Za-z0-9.-]+
_LICENSE_REF = re.compile(r"^LicenseRef-[A-Za-z0-9.-]+$")


def _declared_expression() -> str:
    """pyproject.toml is the source; every other surface is checked against it."""
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^license\s*=\s*"([^"]+)"', text, re.M)
    assert match, (
        "pyproject.toml no longer declares `license` as a bare string. If it "
        "reverted to `license = { file = ... }`, PyPI publishes the whole "
        "LICENSE text as info.license and the identifier is unallowlistable "
        "again (#517)."
    )
    return match.group(1)


def test_the_declared_expression_is_a_well_formed_license_ref() -> None:
    expression = _declared_expression()
    assert _LICENSE_REF.match(expression), (
        f"{expression!r} is not a valid SPDX LicenseRef; PyPI rejects a "
        "malformed license expression at upload, i.e. after the wheel is built"
    )


def test_plugin_manifest_names_the_same_identifier() -> None:
    declared = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["license"]
    assert declared == _declared_expression(), (
        f".claude-plugin/plugin.json says {declared!r}; pyproject.toml says "
        f"{_declared_expression()!r}. Two identifiers for one license means an "
        "allowlist needs two entries."
    )


def test_mcpb_manifest_derives_the_identifier_rather_than_copying_it() -> None:
    """`mcpb/manifest.json` is generated, so the check belongs on the generator.

    Asserting the built value (not the source text) is what makes this fail if
    someone reintroduces a literal, whatever they spell it.
    """
    sys.path.insert(0, str(REPO_ROOT / "mcpb"))
    try:
        from build import build_manifest  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    assert build_manifest()["license"] == _declared_expression()


# The LICENSE text as it stands at the declared major version. Bump the
# identifier's suffix AND this digest together when the terms change
# substantively; update the digest alone for an editorial change.
#
# ⚠ Over the NORMALISED text, never the raw bytes. Git rewrites line endings
# on checkout, so a byte digest is a property of the checkout rather than of the
# terms: this pin was red on all four Ubuntu legs and green on all four Windows
# legs on its first run. **A licence says the same thing in either encoding.**
_LICENSE_DIGEST = "a6982f160837edfc3a953484fda3339bca7fd61a154343c849730203ba97b7ec"


def _license_terms() -> bytes:
    """The licence text with line endings normalised, so the digest is portable."""
    normalised = LICENSE.read_text(encoding="utf-8").replace("\r\n", "\n")
    return normalised.encode("utf-8")


def test_the_suffix_and_the_license_major_version_imply_each_other() -> None:
    """The identifier tracks the MAJOR version, not the full version.

    A minor bump is editorial or clarifying and must not invalidate an
    allowlist; a major bump means the terms changed substantively and
    re-approval is the honest outcome. Requested by @marcelruhf, who operates
    an allowlist against this identifier and proposed the major-only form.

    ⚠ The implication runs BOTH WAYS — his improvement on the version we first
    shipped. Asserting a suffix exists unconditionally encodes this repo's
    accident: jdocmunch-mcp and jdatamunch-mcp state no version at all, and
    there the same assertion would demand one the file never makes.
    """
    expression = _declared_expression()
    suffix = re.search(r"-(\d+)$", expression)
    header = LICENSE.read_text(encoding="utf-8")[:400]
    in_file = re.search(r"^Version\s+(\d+)\.\d+", header, re.M)
    if in_file:
        assert suffix, (
            f"{expression!r} carries no version suffix but {LICENSE.name} "
            f"states a version"
        )
        assert suffix.group(1) == in_file.group(1), (
            f"the identifier claims licence major version {suffix.group(1)}; "
            f"{LICENSE.name} says {in_file.group(1)}"
        )
    else:
        assert not suffix, (
            f"{expression!r} carries version suffix {suffix.group(1)} but "
            f"{LICENSE.name} states no version"
        )


def test_the_license_text_cannot_change_without_a_decision_being_made() -> None:
    """A major-only identifier is a PROMISE; this is what makes it checkable.

    ⚠⚠ We have already broken it once. `f3c925c` (2026-07-10) ADDED a
    redistribution and attribution obligation to condition 2 — a substantive
    change to what a licensee may do — while the header stayed at
    `Version 1.1 — effective 2026-06-30`. **Nothing failed, because a version
    line is a convention and conventions do not fail builds.**

    So the identifier cannot rest on our discipline about bumping the version.
    This pins the terms text to the declared version: any edit to LICENSE fails
    here, and clearing the failure requires deciding which kind of edit it was.
    It cannot make that judgement — it forces it to be made at the moment the
    text moves, rather than discovered by a licensee later.
    """
    actual = hashlib.sha256(_license_terms()).hexdigest()
    assert actual == _LICENSE_DIGEST, (
        f"{LICENSE.name} changed (digest {actual}).\n"
        "  Substantive change to the terms -> bump the MAJOR version in the "
        "LICENSE header, bump the identifier suffix in pyproject.toml and "
        ".claude-plugin/plugin.json, and update _LICENSE_DIGEST.\n"
        "  Editorial change (typo, formatting, a clarification that grants and "
        "removes nothing) -> update _LICENSE_DIGEST alone.\n"
        "  Allowlists downstream key on the identifier, so the first case "
        "must be visible to them and the second must not churn them."
    )


def test_no_license_classifier_survives_beside_the_expression() -> None:
    """PEP 639: a `License ::` classifier alongside an expression is rejected.

    The build succeeds and the UPLOAD fails, which is the expensive ordering —
    step 4 of the release checklist exists for the same reason.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    offenders = re.findall(r'^\s*"(License :: [^"]+)"', text, re.M)
    assert not offenders, f"remove the classifier(s) {offenders}; the expression supersedes them"
