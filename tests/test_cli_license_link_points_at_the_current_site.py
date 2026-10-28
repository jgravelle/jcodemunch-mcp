"""The license link the CLI prints points at the current site, tagged.

`org/license.py` handed out `https://j.gravelle.us/jCodeMunch/#pricing`, the
retired site, while every other sales link in the CLI goes to jcodemunch.com
with `utm_source=cli`. A buyer following it reached a page no longer kept up,
and the visit was not counted as coming from the CLI.

`j.gravelle.us` still serves two live endpoints, the license check and the
savings counter. They stay; only a page link may not point there.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jcodemunch_mcp.org import license as lic

SRC = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"

# Live service endpoints on the old host, not pages a person is sent to.
LIVE_ENDPOINTS = {
    "https://j.gravelle.us/jCodeMunch/validate.php",
    "https://j.gravelle.us/APIs/savings/post.php",
}


def test_the_get_license_link_is_the_current_site_and_tagged():
    url = urlparse(lic.GET_LICENSE_URL)
    assert url.scheme == "https"
    assert url.netloc == "jcodemunch.com"
    assert url.fragment == "pricing"
    assert parse_qs(url.query).get("utm_source") == ["cli"]


def test_no_link_in_src_points_at_the_retired_site():
    found = set()
    for path in SRC.rglob("*.py"):
        for m in re.finditer(r"https?://j\.gravelle\.us[^\s\"')]*", path.read_text(encoding="utf-8")):
            found.add(m.group(0).rstrip(".,;:"))  # a sentence may end on the URL
    assert found <= LIVE_ENDPOINTS, sorted(found - LIVE_ENDPOINTS)
