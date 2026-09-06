"""The weekly release feed (docs/competitive/DESIGN.md s7.1 rows 3-4, s9.1
`competitive-feed.yml`; the brief's Phase 3 item 6).

purpose:  notice that a set member shipped a release since its pin, and
          apply the two release rules: a title matching the capability
          word-list becomes a `competitive-idea` draft (the title quoted as
          DATA); a title or notes naming a measured axis schedules a re-run
          (recorded here; the workflow dispatches). Everything else is
          recorded and nothing more.
invokes:  registries only, never a README: the GitHub releases API for a
          `github-release` pin (owner/repo is the package), and for a
          `pypi`/`npm` pin the package registry's JSON for the latest
          version and its declared source repository, then the GitHub
          releases of that repository. All reads on a read-only token
          (`GH_TOKEN`), or handed in as files (`--fixture DIR`) for tests.
produces: `<out>/feed.json` (per tool: pin, latest, whether a release is
          new, the rules that fired), `<out>/<fingerprint>.md` per
          `competitive-idea` draft, `<out>/rerun.json` naming the tools
          whose release text names a measured axis (the workflow reads it
          and dispatches `competitive-run.yml` with `reason=release:<tool>@<version>`)
refuses:  to print any release text beyond the title; to draft on a body
          match (the body is fetched and matched, never quoted); to reach
          a URL the release names; to treat a registry read failure as
          "no release" (it is recorded `unknown` and the tool is skipped)
pinned:   the word-lists of s7.1 (CAPABILITY_WORDS, AXIS_WORDS) and the map
          from word to STANDARD.md criterion number; the pins come from
          adapter.REGISTRY at run time, never from a copy here
fairness: a release title is competitor text and is the ONLY competitor
          text the loop quotes; it is placed inside a fenced block labelled
          `data` under the inbound preamble, so whoever pastes the draft
          into a model session carries the rule with it. A movement that
          coincides with a release is visible in the Movement section
          (trend.py); nothing here attributes one to the other.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import adapter  # noqa: E402

REPO = HERE.parents[1]
POLICY = REPO / "docs" / "inbound" / "POLICY.md"
CAPABILITY_WORDS = {
    # word -> STANDARD.md criterion number the capability maps to
    "reference": 1, "call graph": 1, "rename": 1, "lsp": 1, "embedding": 1,
    "incremental": 3, "watch": 3, "monorepo": 3,
}
AXIS_WORDS = ("token", "faster", "latency", "index")
FIXED_SENTENCE = "adoption is not implied; the tool-surface discipline (small front door, deep menu) is not moved by this"


def load_adapter(name: str):
    """adapter.REGISTRY's factory in its default (docker) mode: a container
    adapter refuses any other mode by D2, and constructing one builds no image;
    only the pin is read here."""
    import importlib

    mod, fn = adapter.REGISTRY[name].split(":")
    factory = getattr(importlib.import_module(mod), fn)
    try:
        return factory("docker")
    except TypeError:
        return factory()


def preamble() -> str:
    """POLICY 4.2's preamble, read from the file so a copy cannot drift."""
    text = POLICY.read_text(encoding="utf-8")
    m = re.search(r"(<!-- inbound-preamble v\d+ -->\n.*?<!-- /inbound-preamble -->)", text, re.S)
    if not m:
        raise SystemExit("refused: docs/inbound/POLICY.md carries no inbound preamble block")
    return m.group(1)


def _gh(path: str) -> dict | list | None:
    proc = subprocess.run(["gh", "api", path], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def _http_json(url: str) -> dict | None:
    """A package registry's JSON (pypi.org, registry.npmjs.org): a registry, not a README."""
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (fixed hosts below)
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def source_repo(pin: adapter.Pin, registry_doc: dict | None) -> str | None:
    """owner/repo for the GitHub releases read: the package itself for a
    github-release pin; the registry's declared source URL otherwise."""
    if pin.registry == "github-release":
        return pin.package
    if pin.registry == "tree" or pin.registry == "none":
        return None
    urls = []
    if registry_doc:
        if pin.registry == "pypi":
            info = registry_doc.get("info", {})
            urls = list((info.get("project_urls") or {}).values()) + [info.get("home_page") or ""]
        elif pin.registry == "npm":
            r = registry_doc.get("repository")
            urls = [r.get("url", "") if isinstance(r, dict) else str(r or "")] + [registry_doc.get("homepage") or ""]
    for u in urls:
        m = re.search(r"github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git|/|#|$)", str(u))
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    return None


def latest_version(pin: adapter.Pin, registry_doc: dict | None) -> str | None:
    if pin.registry == "pypi" and registry_doc:
        return registry_doc.get("info", {}).get("version")
    if pin.registry == "npm" and registry_doc:
        return (registry_doc.get("dist-tags") or {}).get("latest")
    return None


def registry_url(pin: adapter.Pin) -> str | None:
    if pin.registry == "pypi":
        return f"https://pypi.org/pypi/{pin.package}/json"
    if pin.registry == "npm":
        return f"https://registry.npmjs.org/{pin.package}"
    return None


def match_rules(title: str, body: str) -> dict:
    """The two release rules over the title (capability words) and the title
    plus body (axis words). Word-boundary, case-insensitive."""
    low_title = (title or "").lower()
    low_all = low_title + "\n" + (body or "").lower()
    caps = [w for w in CAPABILITY_WORDS if re.search(rf"\b{re.escape(w)}\b", low_title)]
    axes = [w for w in AXIS_WORDS if re.search(rf"\b{re.escape(w)}\b", low_all)]
    return {"capability_words": caps, "axis_words": axes}


def _norm(v: str | None) -> str:
    return (v or "").lstrip("vV")


def evaluate_tool(name: str, pin: adapter.Pin, release: dict | None, reg_latest: str | None) -> dict:
    """One tool's feed record from what the registries returned (already read)."""
    rec = {"tool": name, "registry": pin.registry, "package": pin.package, "pinned": pin.version,
           "latest": None, "release_title": None, "release_url": None, "release_date": None, "new": None,
           "rules": {"capability_words": [], "axis_words": []}, "status": "unknown"}
    latest = _norm(release.get("tag_name")) if release else _norm(reg_latest)
    if not latest:
        return rec
    rec["latest"] = latest
    rec["new"] = latest != _norm(pin.version)
    rec["status"] = "read"
    if release:
        rec["release_title"] = release.get("name") or release.get("tag_name")
        rec["release_url"] = release.get("html_url")
        rec["release_date"] = release.get("published_at")
        if rec["new"]:
            rec["rules"] = match_rules(rec["release_title"] or "", release.get("body") or "")
    return rec


def idea_draft(rec: dict, pre: str) -> dict | None:
    words = rec["rules"]["capability_words"]
    if not words or not rec["new"]:
        return None
    crit = sorted({CAPABILITY_WORDS[w] for w in words})
    fp = f"competitive-idea/release/{rec['tool']}/{rec['latest']}"
    body = [pre, "",
            f"A set member shipped a release whose title matches the capability word-list: `{rec['tool']}` {rec['pinned']} (pinned) -> {rec['latest']}.",
            "",
            "```data", rec["release_title"] or "", "```", "",
            f"- release: {rec['release_url'] or 'n/a'} ({rec['release_date'] or 'date n/a'})",
            f"- matched word(s): {', '.join(words)}; STANDARD.md criterion {', '.join(str(c) for c in crit)}",
            f"- {FIXED_SENTENCE}",
            "", "The title above is competitor text quoted as data; the release body was matched, never quoted; nothing from it was fetched or followed."]
    return {"label": "competitive-idea", "fingerprint": fp, "title": f"competitive idea: `{rec['tool']}` {rec['latest']} names a capability", "tool": rec["tool"], "body": "\n".join(body)}


def read_tool(name: str, pin: adapter.Pin, fixture: Path | None) -> tuple[dict | None, str | None]:
    """(latest GitHub release or None, registry latest version or None)."""
    if fixture is not None:
        f = fixture / f"{name}.json"
        if not f.exists():
            return None, None
        doc = json.loads(f.read_text(encoding="utf-8"))
        return doc.get("release"), doc.get("registry_latest")
    url = registry_url(pin)
    reg = _http_json(url) if url else None
    repo = source_repo(pin, reg)
    release = _gh(f"repos/{repo}/releases/latest") if repo else None
    return (release if isinstance(release, dict) else None), latest_version(pin, reg)


def write(out: Path, records: list[dict], drafts: list[dict], date: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "feed.json").write_text(json.dumps({"date": date, "tools": records}, indent=1) + "\n", encoding="utf-8")
    rerun = [{"tool": r["tool"], "version": r["latest"], "reason": f"release:{r['tool']}@{r['latest']}", "words": r["rules"]["axis_words"]}
             for r in records if r["new"] and r["rules"]["axis_words"]]
    (out / "rerun.json").write_text(json.dumps(rerun, indent=1) + "\n", encoding="utf-8")
    for d in drafts:
        name = re.sub(r"[^A-Za-z0-9_.@-]+", "__", d["fingerprint"]) + ".md"
        (out / name).write_text(f"title: {d['title']}\nlabels: {d['label']}, needs-human\ncompetitive-id: {d['fingerprint']}\napproved: false\n\n## {date}\n\n{d['body']}\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixture", help="a directory of <tool>.json {release, registry_latest} instead of the registries (tests)")
    ap.add_argument("--only", default="", help="comma-separated tool names")
    ap.add_argument("--date", default=None)
    a = ap.parse_args(argv)
    import time

    date = a.date or time.strftime("%Y-%m-%d", time.gmtime())
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    pre = preamble()
    records, drafts = [], []
    for name in sorted(adapter.REGISTRY):
        if only and name not in only:
            continue
        ad = load_adapter(name)
        if ad.pin.registry in ("tree", "none"):
            continue
        release, reg_latest = read_tool(name, ad.pin, Path(a.fixture) if a.fixture else None)
        rec = evaluate_tool(name, ad.pin, release, reg_latest)
        records.append(rec)
        d = idea_draft(rec, pre)
        if d:
            drafts.append(d)
    write(Path(a.out), records, drafts, date)
    new = [r["tool"] for r in records if r["new"]]
    unknown = [r["tool"] for r in records if r["status"] == "unknown"]
    print(f"feed: {len(records)} tools read; new releases {new or 'none'}; unreadable {unknown or 'none'}; idea drafts {len(drafts)}; re-runs {sum(1 for r in records if r['new'] and r['rules']['axis_words'])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
