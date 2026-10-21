"""Pillow is installed only on Python 3.11+.

Every current Pillow security fix is in 13.0.0, which requires Python 3.11.
The ``groq-explain`` and ``all`` extras pulled Pillow on Python 3.10 too, where
the newest release is 12.3.0 and unpatched; each new advisory then raised an
alert nobody could fix and a failing Dependabot update job. The extras now
carry ``python_version >= '3.11'``, so 3.10 installs no Pillow, and the
explainer tells a 3.10 user why instead of suggesting an install that cannot
help.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10: pytest depends on tomli there
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def _extra(name: str) -> list[str]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"][name]


@pytest.mark.parametrize("extra", ["groq-explain", "all"])
def test_every_extra_pulling_pillow_requires_python_311(extra):
    reqs = [r for r in _extra(extra) if r.lower().startswith("pillow")]
    assert reqs, extra
    for r in reqs:
        assert re.search(r"python_version\s*>=\s*['\"]3\.11['\"]", r), r
        assert ">=13.0.0" in r, r


def test_no_extra_pulls_pillow_without_the_marker():
    """Non-vacuity over the whole table: a new extra adding Pillow must carry it too."""
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    bare = [f"{k}: {r}" for k, v in extras.items() for r in v if r.lower().startswith("pillow") and "3.11" not in r]
    assert bare == []


def test_the_lock_has_no_pillow_for_python_310():
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    versions = re.findall(r'\[\[package\]\]\nname = "pillow"\nversion = "([^"]+)"', text.replace("\r\n", "\n"))
    assert versions == ["13.0.0"], versions


def _missing_pillow_message(version_info):
    from jcodemunch_mcp.groq import explainer

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("no PIL")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import), \
            patch.object(explainer.shutil, "which", return_value="/usr/bin/ffmpeg"), \
            patch.object(explainer.sys, "version_info", version_info):
        return explainer._check_deps()


def test_on_python_310_the_explainer_says_why_instead_of_suggesting_the_extra():
    msg = _missing_pillow_message((3, 10, 20))
    assert msg and "Python 3.11" in msg
    assert "pip install" not in msg


def test_on_python_311_the_explainer_still_suggests_the_extra():
    msg = _missing_pillow_message((3, 11, 0))
    assert msg and 'pip install "jcodemunch-mcp[groq-explain]"' in msg

