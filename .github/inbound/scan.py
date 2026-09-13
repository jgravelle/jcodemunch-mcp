"""Plain-text scan of inbound text before any model sees it
(docs/inbound/POLICY.md section 1 rule 1 and section 4.3).

purpose:  find the security keywords that make an item `security` and the
          instruction patterns that make it escalate, in the RAW text:
          HTML comments, <details>, code fences, entities and zero-width
          characters are all read, never stripped away first
invokes:  nothing outside the standard library
produces: JSON {"security": [...], "injection": [...]} with the matched
          text and its offset; exit 0 always (the caller decides)
refuses:  to classify; this is a tripwire, not the classifier
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
from pathlib import Path

# Characters an author can hide text with. Removed BEFORE matching so
# "cred​ential" still reads as "credential".
_ZERO_WIDTH = re.compile("[​‌‍⁠﻿­͏᠎]")

# A credential NOUN, for the exposure patterns below only. ⚠ A bare `token`
# is deliberately absent: in this repository it is overwhelmingly the LLM
# unit ("tokens saved", "token budget"), so only a qualified token counts.
_CRED = (
    r"(?:credentials?|secrets?|passwords?|passwd|api[_ -]?keys?|access[_ -]?keys?"
    r"|private[ -]keys?|signing[ -]keys?|ssh[ -]keys?|key ?material"
    r"|(?:auth(?:entication)?|access|refresh|session|bearer|oauth|api|http"
    r"|github|gh|pypi|npm|anthropic|openai|aws|slack|personal[ -]access)[_ -]tokens?)"
)
# Inflections are spelled out: a bare stem reads "service", "logic",
# "story", "postgres" and "missing package" as exposure verbs, which the
# corpus run showed it doing.
_EXPOSE_VERB = (
    r"\b(?:leak(?:s|ed|ing)?|expos(?:e|es|ed|ing)|disclos(?:e|es|ed|ing)"
    r"|reveal(?:s|ed|ing)?|dump(?:s|ed|ing)?|print(?:s|ed|ing)?|echo(?:es|ed|ing)?"
    r"|log(?:s|ged|ging)?|writ(?:e|es|ing|ten)|wrote|commit(?:s|ted|ting)?"
    r"|ship(?:s|ped|ping)?|publish(?:es|ed|ing)?|upload(?:s|ed|ing)?"
    r"|bundl(?:e|es|ed|ing)|embed(?:s|ded|ding)?|past(?:e|es|ed|ing)"
    r"|post(?:s|ed|ing)?|send(?:s|ing)?|sent|transmit(?:s|ted|ting)?"
    r"|return(?:s|ed|ing)?|serv(?:es|ed|ing)|index(?:es|ed|ing)?|cach(?:e|es|ed|ing)"
    r"|stor(?:e|es|ed|ing)|persist(?:s|ed|ing)?|hard[- ]?cod(?:e|es|ed|ing)"
    r"|ignor(?:e|es|ed|ing)|bypass(?:es|ed|ing)?)"
)
_EXPOSE_AFTER = (
    r"(?:leak\w*|expos\w*|disclos\w*|visible|readable|world[- ]readable|hard[- ]?coded"
    r"|checked[- ]in|committed|pushed|published|in (?:plain|clear) ?text"
    # a noun FIRST takes the passive only: "the token is printed", never
    # "rotate the token and store it"
    r"|printed|logged|revealed|dumped|written|shipped|uploaded|bundled|embedded"
    r"|pasted|posted|sent|transmitted|returned|served|indexed|cached|stored|persisted"
    r"|in (?:the |a |our |my |your |its )?(?:[\w-]+ )?(?:logs?|output|stdout|stderr|sdist"
    r"|wheel|package|tarball|artifact|repo(?:sitory)?|commit|git history|history|response"
    r"|index|cache|url|query string|screenshot|gist|public))"
)
# Up to four words between the two halves, none of them a negation:
# "stores nothing about secrets" and "never logs the token" are not exposures.
_GAP = r"(?:(?!(?:nothing|no|not|never|none|without)\b)[\w.'`\"/-]+\s+){0,4}?"

# POLICY section 1 rule 1. Word-ish boundaries; case-insensitive.
# ⚠ Credentials fire on EXPOSURE, never on MENTION (owner ruling 2026-09-13,
# "mentioning credentials is fine; exposing them is not"). The word list this
# replaced matched `credential`, `token`, `secret` and `api key` anywhere and
# flagged 75 of 321 issues, #670 twelve seconds after it was filed for
# describing a credential-free defect. Exposure is a secret-shaped VALUE in
# the text, or a credential noun within a few words of an exposure verb.
SECURITY_TERMS = [
    r"vulnerab\w*",
    r"exploit\w*",
    r"\bCVE-\d{4}-\d+",
    r"\bGHSA-[\w-]+",
    # values: a secret pasted into the item is an exposure by itself
    r"\bgh[pousr]_[A-Za-z0-9]{36,}",
    r"\bgithub_pat_[A-Za-z0-9_]{22,}",
    r"\bsk-ant-[A-Za-z0-9_-]{20,}",
    r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bpypi-[A-Za-z0-9_-]{50,}",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?:api[_-]?key|token|secret|password|passwd)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-/+=]{24,}",
    # words: a credential and what happened to it, in either order
    _EXPOSE_VERB + r"\s+" + _GAP + r"[\w`'\"./-]*?" + _CRED,
    _CRED + r"[\w`'\"]*\s+" + _GAP + _EXPOSE_AFTER,
    r"path (?:escape|traversal)",
    r"\btraversal\b",
    r"arbitrary (?:file )?(?:write|read|code)",
    r"cross[- ]repo\w*",
    r"(?:data|information) (?:exposure|leak\w*|disclos\w*)",
    r"redact\w* (?:fail|miss|bypass)\w*",
    r"\bRCE\b",
    r"remote code execution",
    r"privilege escalation",
    r"\bsandbox escape",
]

# POLICY section 4.3. Each is a pattern over normalised text.
INJECTION_PATTERNS = [
    r"(?:ignore|disregard|forget|drop|override)\s+(?:all |the |your |any )?(?:previous|prior|above|earlier|existing|original|system)\s+(?:instructions?|prompts?|rules?|policy|guidance)",
    # Repository controls only: env vars and Claude Code hooks are PRODUCT
    # features here (CLAUDE.md "Env Vars", `hook-*`), so a bare "variable" or
    # "hook" would tax the most ordinary bug reports (plumbing review, note 2).
    r"(?:disable|skip|bypass|remove|delete|drop|modify|change|edit|update|turn[ -]off|set|unset|flip|toggle|revoke|add)\s+(?:[\w.\-/`'\"]+\s+){0,4}?(?:workflow|github action|branch protection|codeowners|ruleset|deploy key|(?:repo(?:sitory)?|actions?|github|org(?:anization)?)\s+(?:secret|variable|permission)s?|inbound_enabled|inbound_autofix|\.claude/hooks|deny[_ ]guard|pre_commit\.py|pre_pr\.py)\b",
    r"(?:edit|change|modify|loosen|lower|raise|update|rewrite)\s+(?:the |your )?(?:standard\.md|thresholds?\.json|retired\.json|archaeology\.md|security\.md|license|policy\.md)",
    # Exfiltration verbs followed by a destination. github.com is where our own
    # work goes, so a pushed branch or a linked PR is not a destination here.
    r"(?:post|send|upload|report|exfiltrate|forward|curl|wget|submit|transmit|email|mail|dm|message)\b[^\n]{0,80}\b(?:https?://(?!github\.com/)|webhook|[\w.+-]+(?:@|\s*[\[(]at[\])]\s*)[\w-]+(?:\.|\s*[\[(]dot[\])]\s*)\w+|to my server|to this url|as a (?:public )?gist|hooks\.slack\.com|discord(?:app)?\.com|ngrok|pastebin|requestbin|(?<!github\.com/)\b(?!github\.com)[\w-]+\.(?:invalid|io|sh|xyz|ru|cn|net|org|co)/\w)",
    r"(?:approve|merge|close|tag|release|publish)\s+(?:this|the|your|my|that|pr\b|pull request|issue)",
    r"you must (?:now )?(?:approve|merge|close|tag|release|publish|comply|obey)",
    r"(?:print|reveal|show|dump|echo|output|read|cat|type|access|include|paste|put)\s+(?:the |your |any )?(?:[\w.\-/`'\"]+\s+){0,3}?(?:system prompt|instructions|configuration|config|secrets?|environment|env vars?|tokens?|api[_ -]?keys?|~/\.claude|settings\.json|anthropic_api_key|github_token)",
    r"pip install [^\n]*--(?:extra-)?index-url (?!https://pypi\.org)",
    r"(?:^|\n)\s*(?:assistant|ai)\s*:\s*(?:i will|sure|understood|ok)",
    r"\b(?:the maintainer|jgravelle|anthropic|github|the owner) (?:said|says|told|approved|authori[sz]ed|allows?|wants) (?:you|this|that)",
    r"you are (?:now )?(?:allowed|authori[sz]ed|permitted|cleared) to",
    r"\bnew (?:policy|instructions?|rules?)\b",
    r"(?:override|test|debug|maintenance|emergency|admin|developer) mode",
    # A pasted log line `assistant: calling search_symbols` is a bug report;
    # a fake assistant turn is caught by the compliance pattern below.
    r"(?:^|\n)\s*(?:system|developer)\s*:\s",
    r"\[INST\]|<\|im_start\|>|<\|system\|>|<<SYS>>|\[system\]",
    r"curl [^\n]*\|\s*(?:ba|z)?sh\b",
    r"(?:pipx?|uv pip|uv) install [^\n]*--(?:extra-)?index(?:-url)? (?!https://pypi\.org)",
    r"\$\((?:curl|wget) [^\n]*\)",
    r"run (?:the )?(?:attached|included|provided|this) [\w.-]+\.(?:sh|py|ps1|bat|cmd)\b",
    r"as (?:agreed|discussed|approved|instructed) (?:with|by|earlier|before)",
    r"(?:has|have|was|were|is|are) (?:already |now )?(?:been )?(?:approved|authori[sz]ed|signed off|cleared|okayed) by (?:the maintainer|jgravelle|anthropic|github|the owner|an? (?:admin|maintainer))",
    r"\b(?:the maintainer|jgravelle|the owner) has (?:already )?(?:approved|authori[sz]ed|agreed|okayed)",
    r"no (?:review|approval|check|test)s? (?:is |are )?(?:needed|required|necessary)[^\n]{0,40}\b(?:merge|approve|ship|release)",
    r"(?:ship it|cut (?:the |a )?release|tag v?\d+\.\d+|create (?:the |a )?(?:release|tag))",
    r"\b(?:gh auth token|printenv|env \||set \| grep|echo \$\w*(?:key|token|secret)\w*|base64)\b",
]

_SEC = [re.compile(p, re.IGNORECASE) for p in SECURITY_TERMS]
_INJ = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


def normalise(text: str) -> str:
    """Unescape entities, drop zero-width characters, fold compatibility
    forms (full-width letters, ligatures) to ASCII-ish. HTML comments,
    <details> and code fences are NOT removed: the point is to read them."""
    t = html.unescape(text or "")
    t = _ZERO_WIDTH.sub("", t)
    t = unicodedata.normalize("NFKC", t)
    return t


def scan(text: str) -> dict:
    t = normalise(text)
    out = {"security": [], "injection": []}
    for rx in _SEC:
        for m in rx.finditer(t):
            out["security"].append({"match": m.group(0), "at": m.start()})
    for rx in _INJ:
        for m in rx.finditer(t):
            out["injection"].append(
                {"match": m.group(0)[:120], "at": m.start(), "pattern": rx.pattern[:60]}
            )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "path", nargs="?", type=Path, help="file to scan; stdin when omitted"
    )
    args = ap.parse_args(argv)
    text = args.path.read_text(encoding="utf-8") if args.path else sys.stdin.read()
    res = scan(text)
    res["security_hit"] = bool(res["security"])
    res["injection_hit"] = bool(res["injection"])
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
