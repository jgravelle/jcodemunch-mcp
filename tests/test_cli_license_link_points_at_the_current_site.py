"""The license link the CLI prints points at the current site, tagged.

`org/license.py` handed out `https://j.gravelle.us/jCodeMunch/#pricing`, the
retired site, while every other sales link in the CLI goes to jcodemunch.com
with `utm_source=cli`. A buyer following it reached a page no longer kept up,
and the visit was not counted as coming from the CLI.

`j.gravelle.us` still serves two live endpoints, the license check and the
savings counter. They stay; only a page link may not point there.

What the scan sees: every spelling of the host in the package's Python source,
in any case, with or without a scheme or `www.`, and string literals Python
joins (`"https://j.gravelle" ".us/x"`). A bare host name with no scheme and no
path is prose (a comment saying the old copy is stale) and passes. What it
cannot see: a URL built at run time from a variable host, such as
`f"https://{HOST}/x"`, and files outside `src/jcodemunch_mcp/**/*.py`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jcodemunch_mcp.org import license as lic

SRC = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"

# Live service endpoints on the old host, not pages a person is sent to.
# Normalised: lower case, no scheme, no `www.`.
LIVE_ENDPOINTS = {
    "j.gravelle.us/jcodemunch/validate.php",
    "j.gravelle.us/apis/savings/post.php",
}

_HOST_RE = re.compile(
    r"(?P<scheme>https?://)?(?:www\.)?j\.gravelle\.us(?P<path>/[^\s\"')]*)?",
    re.IGNORECASE,
)


def _links(text: str) -> set[str]:
    """Each link to the old host in `text`, normalised. An e-mail address
    (`j@gravelle.us`) is not the host; a bare host in prose is not a link."""
    out = set()
    for m in _HOST_RE.finditer(text):
        if m.group("scheme") is None and m.group("path") is None:
            continue
        link = m.group(0).lower().rstrip(".,;:")  # a sentence may end on the URL
        link = re.sub(r"^https?://", "", link)
        link = re.sub(r"^www\.", "", link)
        out.add(link)
    return out


def _source_links(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found = _links(text)
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found |= _links(node.value)  # implicitly joined literals arrive whole
    return found


def test_the_get_license_link_is_the_current_site_and_tagged():
    url = urlparse(lic.GET_LICENSE_URL)
    assert url.scheme == "https"
    assert url.netloc == "jcodemunch.com"
    assert url.fragment == "pricing"
    assert parse_qs(url.query).get("utm_source") == ["cli"]


def test_no_link_in_src_points_at_the_retired_site():
    found = set()
    for path in SRC.rglob("*.py"):
        found |= _source_links(path)
    assert found <= LIVE_ENDPOINTS, sorted(found - LIVE_ENDPOINTS)


def test_the_scan_sees_every_spelling_it_claims_to(tmp_path):
    """The scan's own red arm: each spelling here is a link it must report."""
    spellings = {
        'u = "https://j.gravelle.us/jCodeMunch/#pricing"': "j.gravelle.us/jcodemunch/#pricing",
        'u = "http://j.gravelle.us/x"': "j.gravelle.us/x",
        'u = "https://www.j.gravelle.us/x"': "j.gravelle.us/x",
        'u = "HTTPS://J.GRAVELLE.US/X"': "j.gravelle.us/x",
        'u = "see j.gravelle.us/jCodeMunch/#pricing."': "j.gravelle.us/jcodemunch/#pricing",
        'u = "https://j.gravelle" ".us/x"': "j.gravelle.us/x",
        'u = "https://j.gravelle.us"': "j.gravelle.us",
    }
    for i, (code, want) in enumerate(spellings.items()):
        p = tmp_path / f"m{i}.py"
        p.write_text(code + "\n", encoding="utf-8")
        assert want in _source_links(p), code
    prose = tmp_path / "prose.py"
    prose.write_text(
        "# The legacy j.gravelle.us copy is stale.\n"
        'MAIL = "write to j@gravelle.us"\n',
        encoding="utf-8",
    )
    assert _source_links(prose) == set()
