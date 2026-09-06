"""The competitive corpus set: pinned checkouts, fetched by SHA, cached outside
the tree (docs/competitive/DESIGN.md s3.4; the brief's Phase 3 item 3).

purpose:  put every corpus in corpora.json on disk at exactly its pinned
          commit, once, and refuse to run over a checkout whose HEAD is not
          that commit
invokes:  git (init, fetch --depth 1 <sha>, checkout) per corpus into
          $JCM_COMPETE_CORPORA or ~/.cache/jcm-compete/corpora/<owner>__<repo>
          -- never inside benchmarks/, which ships in the sdist
produces: {corpus id: Path} for run.py; `python corpora.py` fetches and
          verifies from the command line
refuses:  a corpora.json entry without a full 40-hex sha; a checkout whose
          HEAD differs from the pin (it is re-fetched, never used as is)
pinned:   corpora.json (every entry a full 40-hex SHA)
fairness: every corpus is a git repository at a recorded SHA (CF-10); the
          set is what corpus_check.py judges, not any one member
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "corpora.json"
_SHA = re.compile(r"^[0-9a-f]{40}$")


def cache_dir() -> Path:
    env = os.environ.get("JCM_COMPETE_CORPORA")
    return Path(env).expanduser().resolve() if env else Path.home() / ".cache" / "jcm-compete" / "corpora"


def load(manifest: Path = MANIFEST) -> list[dict]:
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    out = []
    for c in doc["corpora"]:
        if not _SHA.match(c.get("sha", "")):
            raise SystemExit(f"refused: corpora.json entry {c.get('id')!r} has no full 40-hex sha")
        out.append(c)
    return out


def _head(path: Path) -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True, encoding="utf-8", stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, OSError):
        return None


def fetch(entry: dict, root: Path | None = None, log=print) -> Path:
    """The checkout for one entry, fetched if absent or at the wrong commit.
    A shallow fetch by SHA: history is not part of any corpus."""
    root = root or cache_dir()
    dest = root / entry["repo"].replace("/", "__")
    if _head(dest) == entry["sha"]:
        return dest
    if dest.exists():
        import shutil

        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{entry['repo']}.git"
    log(f"fetching {entry['id']} from {url}")
    for args in (["init", "-q"], ["remote", "add", "origin", url], ["-c", "protocol.version=2", "fetch", "-q", "--depth", "1", "origin", entry["sha"]],
                 ["-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"]):
        subprocess.run(["git", *args], cwd=dest, check=True, capture_output=True)
    if _head(dest) != entry["sha"]:
        raise SystemExit(f"refused: {entry['id']} checked out at {_head(dest)}, pinned {entry['sha']}")
    return dest


def fetch_all(manifest: Path = MANIFEST, root: Path | None = None, only: set[str] | None = None, log=print) -> dict[str, Path]:
    out = {}
    for c in load(manifest):
        if only and c["id"] not in only:
            continue
        out[c["id"]] = fetch(c, root, log)
    return out


def main(argv: list[str] | None = None) -> int:
    paths = fetch_all(log=lambda s: print(s, file=sys.stderr))
    for cid, p in paths.items():
        print(f"{cid}\t{p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
