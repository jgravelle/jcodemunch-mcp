"""Compiler / linter diagnostics — pure parsers, no DB writes.

The user runs their own checker (in CI, a pre-commit hook, a shell) and hands
us the file it wrote. We never run a checker: no new process, no compiler on
our PATH, no new trust boundary. Five shapes are read and every one of them
yields the same ``Diagnostic`` record:

  mypy     ``mypy --output json``            JSON-Lines
  pyright  ``pyright --outputjson``          one JSON object, ``generalDiagnostics``
  tsc      ``tsc --noEmit --pretty false``   text, ``path(line,col): error TSnnnn: msg``
  ruff     ``ruff check --output-format json`` JSON array
  generic  ``{"file","line","severity","message","code"?,"tool"?}`` JSON-Lines

Detection is by CONTENT, never by file extension (``stack_log.py``'s
``fmt="auto"`` precedent): a ``.json`` extension says nothing about which tool
wrote it. Line bases differ on the wire (pyright is 0-based) and the PARSER
owns that conversion, so every ``Diagnostic.line`` is 1-based by the time the
mapper sees it. Fixtures for all four real tools were captured from the tools
themselves (``tests/fixtures/diagnostics/REGENERATE.md``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

FORMATS = ("mypy", "pyright", "tsc", "ruff", "generic")

_TSC_LINE = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\):\s+(?P<sev>error|warning|message)\s+"
    r"(?P<code>TS\d+):\s+(?P<msg>.*)$"
)


@dataclass(frozen=True)
class Diagnostic:
    """One checker finding, normalised.

    ``line`` is 1-based. ``column`` may be None. ``severity`` is one of
    ``error`` / ``warning`` / ``info``. ``code`` is the tool's rule id
    (``arg-type``, ``reportArgumentType``, ``TS2345``, ``F401``) or ``""``.
    """

    file: str
    line: int
    column: Optional[int]
    severity: str
    code: str
    message: str
    tool: str


def _normalise_severity(value: object, default: str = "error") -> str:
    s = str(value or "").strip().lower()
    if s in ("error", "err", "e", "fatal"):
        return "error"
    if s in ("warning", "warn", "w"):
        return "warning"
    if s in ("info", "information", "note", "hint", "message", "i", "n"):
        return "info"
    return default


def detect_format(text: str) -> str:
    """Name the tool that wrote ``text`` from its content. Raises ValueError
    when nothing matches: guessing would attach diagnostics to the wrong
    lines, and a refusal is cheaper than a wrong row."""
    stripped = text.lstrip()
    if not stripped:
        raise ValueError("empty diagnostics file; pass format= if this is deliberate")

    # tsc: the first non-blank line has the (line,col): error TSnnnn shape.
    first = stripped.splitlines()[0]
    if _TSC_LINE.match(first):
        return "tsc"

    if stripped.startswith("{"):
        # One object (pyright) or JSON-Lines (mypy / generic).
        first_line = stripped.splitlines()[0]
        try:
            obj = json.loads(first_line)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            if "severity" in obj and "file" in obj and "line" in obj:
                # mypy carries hint/end_line/end_column beside the common keys.
                if "hint" in obj or "end_column" in obj:
                    return "mypy"
                return "generic"
        try:
            whole = json.loads(stripped)
        except json.JSONDecodeError:
            whole = None
        if isinstance(whole, dict) and "generalDiagnostics" in whole:
            return "pyright"
        if isinstance(whole, dict) and "severity" in whole and "file" in whole:
            return "mypy" if ("hint" in whole or "end_column" in whole) else "generic"
        raise ValueError("JSON object is not a recognised diagnostics shape")

    if stripped.startswith("["):
        try:
            arr = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON array did not parse: {e}") from e
        if isinstance(arr, list) and (not arr or (isinstance(arr[0], dict) and "filename" in arr[0] and "location" in arr[0])):
            return "ruff"
        raise ValueError("JSON array is not ruff's --output-format json shape")

    raise ValueError("unrecognised diagnostics text; pass format= (mypy|pyright|tsc|ruff|generic)")


# ── parsers ────────────────────────────────────────────────────────────


def _parse_mypy(text: str) -> Iterator[Diagnostic]:
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or "file" not in obj:
            continue
        try:
            line = int(obj.get("line") or 0)
        except (TypeError, ValueError):
            continue
        if line < 1:
            continue
        col = obj.get("column")
        yield Diagnostic(
            file=str(obj["file"]),
            line=line,
            column=int(col) if isinstance(col, int) else None,
            severity=_normalise_severity(obj.get("severity"), "error"),
            code=str(obj.get("code") or ""),
            message=str(obj.get("message") or ""),
            tool="mypy",
        )


def _parse_pyright(text: str) -> Iterator[Diagnostic]:
    whole = json.loads(text)
    for d in whole.get("generalDiagnostics") or []:
        if not isinstance(d, dict) or not d.get("file"):
            continue
        start = (d.get("range") or {}).get("start") or {}
        try:
            line0 = int(start.get("line"))
        except (TypeError, ValueError):
            continue
        char = start.get("character")
        yield Diagnostic(
            file=str(d["file"]),
            line=line0 + 1,  # pyright is 0-based on the wire
            column=(int(char) + 1) if isinstance(char, int) else None,
            severity=_normalise_severity(d.get("severity"), "error"),
            code=str(d.get("rule") or ""),
            message=str(d.get("message") or ""),
            tool="pyright",
        )


def _parse_tsc(text: str) -> Iterator[Diagnostic]:
    for raw in text.splitlines():
        m = _TSC_LINE.match(raw.strip())
        if not m:
            continue  # continuation lines of a multi-line message
        yield Diagnostic(
            file=m.group("file"),
            line=int(m.group("line")),
            column=int(m.group("col")),
            severity=_normalise_severity(m.group("sev"), "error"),
            code=m.group("code"),
            message=m.group("msg"),
            tool="tsc",
        )


def _parse_ruff(text: str) -> Iterator[Diagnostic]:
    for d in json.loads(text) or []:
        if not isinstance(d, dict) or not d.get("filename"):
            continue
        loc = d.get("location") or {}
        try:
            line = int(loc.get("row"))
        except (TypeError, ValueError):
            continue
        col = loc.get("column")
        yield Diagnostic(
            file=str(d["filename"]),
            line=line,
            column=int(col) if isinstance(col, int) else None,
            # A lint finding is not a type error. ruff has no severity field;
            # everything it reports is a warning here, and `code` says which.
            severity="warning",
            code=str(d.get("code") or ""),
            message=str(d.get("message") or ""),
            tool="ruff",
        )


def _parse_generic(text: str) -> Iterator[Diagnostic]:
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or not obj.get("file"):
            continue
        try:
            line = int(obj.get("line"))
        except (TypeError, ValueError):
            continue
        col = obj.get("column")
        yield Diagnostic(
            file=str(obj["file"]),
            line=line,
            column=int(col) if isinstance(col, int) else None,
            severity=_normalise_severity(obj.get("severity"), "error"),
            code=str(obj.get("code") or ""),
            message=str(obj.get("message") or ""),
            tool=str(obj.get("tool") or "generic"),
        )


_PARSERS = {
    "mypy": _parse_mypy,
    "pyright": _parse_pyright,
    "tsc": _parse_tsc,
    "ruff": _parse_ruff,
    "generic": _parse_generic,
}


def iter_diagnostics_from_text(text: str, *, fmt: str = "auto") -> Iterator[Diagnostic]:
    """Yield ``Diagnostic`` records from checker output. ``fmt="auto"``
    detects by content; an explicit name overrides detection. An empty text
    under an explicit format yields nothing (a clean run IS a valid snapshot)."""
    if fmt == "auto":
        fmt = detect_format(text)
    if fmt not in _PARSERS:
        raise ValueError(f"unknown diagnostics format {fmt!r}; one of {FORMATS}")
    if not text.strip():
        return iter(())
    return _PARSERS[fmt](text)


def parse_diagnostics_file(path: str, *, fmt: str = "auto") -> tuple[str, list[Diagnostic]]:
    """Read one file; return ``(format, diagnostics)``."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if fmt == "auto":
        if not text.strip():
            raise ValueError(
                f"{path} is empty and no format= was given; an empty file is a clean "
                "run only when the tool is named"
            )
        fmt = detect_format(text)
    return fmt, list(iter_diagnostics_from_text(text, fmt=fmt))
