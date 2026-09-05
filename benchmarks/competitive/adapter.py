"""The adapter interface of the competitive tier (docs/competitive/DESIGN.md s1).

purpose:  one interface every measured tool presents to the tier, jCodeMunch
          and both null alternatives included, so a comparison is the same
          corpus, the same tasks and the same counter for every row
invokes:  nothing; pure data types plus the registry the runner reads
produces: the dataclasses a result file is built from
refuses:  an adapter without a pin, a name, or a category set; a payload
          that is not bytes-exact (tokens are counted over what an agent
          would receive, ARCHAEOLOGY R15)
pinned:   n/a (this module measures nothing)
fairness: docs/competitive/fairness/<tool>.md per adapter; the nulls and
          jCodeMunch are described in DESIGN s1.2 and s1.4
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Protocol, runtime_checkable

CATEGORIES = ("P1", "P2", "P4", "P5", "T")
"""P1 definition lookup, P2 reference finding, P4 file dependencies, P5 dead
code (reported only), T token task with no expected set (DESIGN s4.1)."""

SCHEMA = "jcm-competitive-result/v1"


@dataclass(frozen=True)
class Pin:
    registry: str          # "pypi" | "npm" | "github-release" | "tree" | "none"
    package: str
    version: str
    digest: str = ""       # registry hash or release checksum when the registry supplies one
    dockerfile_sha256: str = ""  # empty until the tool runs in a container (DESIGN D2)


@dataclass(frozen=True)
class Corpus:
    id: str                # "owner/repo@sha" or "self@<commit>"
    path: Path
    sha256: str            # over the file list and contents, sorted (R44 shape)
    files: tuple[str, ...]  # the shared file set, relative paths, sorted


@dataclass(frozen=True)
class Task:
    id: str
    corpus: str
    category: str
    query: str
    expected: tuple[tuple[str, int], ...] = ()
    tolerance_lines: int = 0
    source: str = ""
    capability_only: bool = False

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"task {self.id}: unknown category {self.category!r}")
        if self.category == "T" and self.expected:
            raise ValueError(f"task {self.id}: a T task carries no expected set")


@dataclass
class Answer:
    payload: str                       # every tool result the agent would receive, in call order
    tokens: int                        # cl100k over payload
    calls: int
    latency_ms: list[float]            # one entry per call, in call order
    cited: frozenset[tuple[str, int]]  # (relative file, line) the payload names
    cites_all: bool = False            # null_readall: the payload IS the corpus
    error: Optional[str] = None


@dataclass
class IndexReport:
    seconds: Optional[float]           # None = NOT COMPARABLE (the tool has no index step)
    ok: bool
    files_indexed: Optional[int] = None
    stderr_tail: str = ""


@runtime_checkable
class Adapter(Protocol):
    name: str
    pin: Pin
    categories: frozenset[str]
    interface: str                     # "mcp-stdio" | "cli" | "python" | "null"

    def index(self, corpus: Corpus, scratch: Path) -> IndexReport: ...
    def answer(self, corpus: Corpus, task: Task, scratch: Path) -> Answer: ...
    def tools_list_tokens(self) -> Optional[int]: ...
    def version(self) -> str: ...


def validate(adapter: object) -> Adapter:
    """Refuse an adapter that could produce a row with a hole in it."""
    for attr in ("name", "pin", "categories", "interface", "index", "answer", "tools_list_tokens", "version"):
        if not hasattr(adapter, attr):
            raise TypeError(f"adapter {adapter!r} lacks {attr}")
    if not isinstance(adapter.pin, Pin):
        raise TypeError(f"adapter {adapter.name}: pin must be a Pin")
    bad = set(adapter.categories) - set(CATEGORIES)
    if bad:
        raise TypeError(f"adapter {adapter.name}: unknown categories {sorted(bad)}")
    if not adapter.categories:
        raise TypeError(f"adapter {adapter.name}: declares no category")
    return adapter  # type: ignore[return-value]


def corpus_digest(root: Path, files: Iterable[str]) -> str:
    h = hashlib.sha256()
    for rel in sorted(files):
        p = root / rel
        if p.is_file():
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def read_file(corpus: Corpus, rel: str) -> str:
    try:
        return (corpus.path / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


_ENC = None


def count_tokens(text: str) -> int:
    """cl100k_base, the tokenizer every published figure uses (R14)."""
    global _ENC
    if _ENC is None:
        import tiktoken

        _ENC = tiktoken.get_encoding("cl100k_base")
    return len(_ENC.encode(text))


REGISTRY: dict[str, str] = {
    # name -> "module:factory"; the runner imports lazily so a broken adapter
    # fails its own row, not the run.
    "null_readall": "adapters.null_readall:make",
    "null_grep": "adapters.null_grep:make",
    "jcodemunch": "adapters.jcodemunch:make",
    "cymbal": "adapters.cymbal:make",
    "codebase_memory": "adapters.codebase_memory:make",
    "code_review_graph": "adapters.code_review_graph:make",
    "serena": "adapters.serena:make",
    "codegraph": "adapters.codegraph:make",
    "graft": "adapters.graft:make",
    "aider": "adapters.aider:make",
}
