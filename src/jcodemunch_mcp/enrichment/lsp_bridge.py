"""LSP Bridge — optional enrichment layer for compiler-grade call graph resolution.

Manages LSP server lifecycles for supported languages and resolves call sites
that tree-sitter couldn't fully qualify.  Strictly additive: if a language
server isn't installed or fails, the system falls back to pure tree-sitter +
heuristic.  Zero behaviour change for users who don't opt in.

Supported language servers:
  - pyright (Python)
  - typescript-language-server (TypeScript / JavaScript)
  - gopls (Go)
  - rust-analyzer (Rust)

Configuration (in config.jsonc)::

    "enrichment": {
        "lsp_enabled": false,
        "lsp_servers": {
            "python": "pyright",
            "typescript": "typescript-language-server",
            "go": "gopls",
            "rust": "rust-analyzer"
        },
        "lsp_timeout_seconds": 30
    }
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────────
# Data types
# ───────────────────────────────────────────────────────────────────

@dataclass
class Position:
    """Zero-indexed line/character position in a text document."""
    line: int
    character: int


@dataclass
class CallSite:
    """An unresolved call site to be resolved via LSP."""
    file: str          # Absolute path to the source file
    position: Position # Position of the call expression
    called_name: str   # The called name as extracted by tree-sitter


@dataclass
class ResolvedRef:
    """A call site resolved to a concrete definition via LSP."""
    call_site: CallSite
    target_file: str        # Absolute path to the definition file
    target_line: int        # Zero-indexed line of the definition
    target_character: int   # Zero-indexed character of the definition
    target_name: str        # Resolved name of the target symbol
    resolution: str = "lsp_resolved"


@dataclass
class DispatchEdge:
    """An interface/trait method resolved to a concrete implementation via LSP."""
    interface_file: str     # Absolute path to file defining the interface/trait
    interface_name: str     # Name of the interface/trait/abstract class
    method_name: str        # Name of the method on the interface
    impl_file: str          # Absolute path to file containing the implementation
    impl_line: int          # Zero-indexed line of the concrete method
    impl_name: str          # Name of the implementing type (e.g. "FileWriter")
    resolution: str = "lsp_dispatch"


# ───────────────────────────────────────────────────────────────────
# Default server configurations
# ───────────────────────────────────────────────────────────────────

DEFAULT_LSP_SERVERS: dict[str, str] = {
    "python": "pyright",
    "typescript": "typescript-language-server",
    "javascript": "typescript-language-server",
    "go": "gopls",
    "rust": "rust-analyzer",
}

DEFAULT_LSP_TIMEOUT = 30  # seconds

# Map server binary names to their LSP start commands
_SERVER_COMMANDS: dict[str, list[str]] = {
    "pyright": ["pyright-langserver", "--stdio"],
    "typescript-language-server": ["typescript-language-server", "--stdio"],
    "gopls": ["gopls", "serve"],
    "rust-analyzer": ["rust-analyzer"],
}

# `initializationOptions` per server binary (L-198). rust-analyzer's defaults run
# the indexed project's build scripts and procedural macros, and `cargo check`
# on save does the same: code from the repository, started by a jcodemunch
# switch. Enrichment needs definitions and references, not macro expansion, so
# those are off. A server not listed here gets no options, as before.
_INITIALIZATION_OPTIONS: dict[str, dict] = {
    "rust-analyzer": {
        "cargo": {"buildScripts": {"enable": False}},
        "procMacro": {"enable": False},
        "checkOnSave": False,
    },
}


def _initialization_options(command: list[str]) -> Optional[dict]:
    """The options for the server `command` starts, keyed by its binary name."""
    if not command:
        return None
    binary = Path(command[0]).name
    for suffix in (".exe", ".cmd", ".bat"):
        if binary.lower().endswith(suffix):
            binary = binary[: -len(suffix)]
            break
    return _INITIALIZATION_OPTIONS.get(binary)


# ───────────────────────────────────────────────────────────────────
# LSP JSON-RPC helpers
# ───────────────────────────────────────────────────────────────────

def _encode_message(obj: dict) -> bytes:
    """Encode a JSON-RPC message with Content-Length header."""
    body = json.dumps(obj).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def _read_message(stream) -> Optional[dict]:
    """Read a single JSON-RPC message from a byte stream.

    Returns None on EOF or parse error.
    """
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        line_str = line.decode("ascii", errors="replace").strip()
        if not line_str:
            break
        if ":" in line_str:
            key, _, value = line_str.partition(":")
            headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", "0"))
    if content_length <= 0:
        return None

    body = stream.read(content_length)
    if len(body) < content_length:
        return None

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        logger.debug("LSP: failed to parse response body", exc_info=True)
        return None


# ───────────────────────────────────────────────────────────────────
# LSP Server Manager (per-language singleton)
# ───────────────────────────────────────────────────────────────────

class LSPServer:
    """Manages the lifecycle of a single LSP server process."""

    def __init__(
        self,
        language: str,
        command: list[str],
        root_path: str,
        timeout: int = DEFAULT_LSP_TIMEOUT,
    ):
        self.language = language
        self.command = command
        self.root_path = root_path
        self.timeout = timeout
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._initialized = False
        self._pending: dict[int, threading.Event] = {}
        self._responses: dict[int, Any] = {}
        self._reader_thread: Optional[threading.Thread] = None
        self._shutdown = False

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        """Start the LSP server process. Returns True on success."""
        if self.is_running:
            return True

        try:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.root_path,
                # On Windows, CREATE_NO_WINDOW prevents console flashing
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError:
            logger.debug(
                "LSP: server binary not found for %s (command: %s)",
                self.language, self.command,
            )
            return False
        except Exception:
            logger.debug("LSP: failed to start %s server", self.language, exc_info=True)
            return False

        self._shutdown = False
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True, name=f"lsp-reader-{self.language}",
        )
        self._reader_thread.start()

        if not self._initialize():
            self.stop()
            return False

        return True

    def stop(self) -> None:
        """Gracefully shut down the LSP server."""
        self._shutdown = True
        if self._process and self._process.poll() is None:
            try:
                self._send_request("shutdown", {})
                self._send_notification("exit", None)
            except Exception:
                pass
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
        self._process = None
        self._initialized = False

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send_request(self, method: str, params: Any) -> Optional[Any]:
        """Send a JSON-RPC request and wait for the response."""
        if not self.is_running:
            return None
        rid = self._next_id()
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        event = threading.Event()
        with self._lock:
            self._pending[rid] = event
        try:
            self._process.stdin.write(_encode_message(msg))
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            logger.debug("LSP: pipe broken for %s", self.language)
            with self._lock:
                self._pending.pop(rid, None)
            return None

        if not event.wait(timeout=self.timeout):
            logger.debug("LSP: request %s timed out for %s", method, self.language)
            with self._lock:
                self._pending.pop(rid, None)
            return None

        with self._lock:
            return self._responses.pop(rid, None)

    def _send_notification(self, method: str, params: Any) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self.is_running:
            return
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        try:
            self._process.stdin.write(_encode_message(msg))
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _read_loop(self) -> None:
        """Background thread reading LSP responses."""
        while not self._shutdown and self.is_running:
            try:
                msg = _read_message(self._process.stdout)
                if msg is None:
                    break
                rid = msg.get("id")
                if rid is not None:
                    with self._lock:
                        event = self._pending.pop(rid, None)
                        if event:
                            self._responses[rid] = msg.get("result")
                            event.set()
            except Exception:
                if not self._shutdown:
                    logger.debug("LSP: read error for %s", self.language, exc_info=True)
                break

    def _initialize(self) -> bool:
        """Send LSP initialize + initialized handshake."""
        root_uri = Path(self.root_path).as_uri()
        params = {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "rootPath": self.root_path,
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": False},
                    "implementation": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                },
            },
            "workspaceFolders": [{"uri": root_uri, "name": Path(self.root_path).name}],
        }
        options = _initialization_options(self.command)
        if options:
            params["initializationOptions"] = options
        result = self._send_request("initialize", params)
        if result is None:
            logger.debug("LSP: initialize failed for %s", self.language)
            return False

        self._send_notification("initialized", {})
        self._initialized = True
        logger.debug("LSP: %s server initialized for %s", self.language, self.root_path)
        return True

    def open_file(self, file_path: str, content: str, language_id: str) -> None:
        """Notify the server that a file has been opened."""
        uri = Path(file_path).as_uri()
        self._send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": language_id,
                "version": 1,
                "text": content,
            },
        })

    def close_file(self, file_path: str) -> None:
        """Notify the server that a file has been closed."""
        uri = Path(file_path).as_uri()
        self._send_notification("textDocument/didClose", {
            "textDocument": {"uri": uri},
        })

    def goto_definition(self, file_path: str, line: int, character: int) -> Optional[list[dict]]:
        """Request textDocument/definition for a position.

        Args:
            file_path: Absolute path to the file.
            line: Zero-indexed line number.
            character: Zero-indexed character offset.

        Returns:
            List of location dicts with uri/range, or None on failure.
        """
        uri = Path(file_path).as_uri()
        result = self._send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if result is None:
            return None
        # Normalize: result can be a single Location, a list of Locations, or a list of LocationLinks
        if isinstance(result, dict):
            result = [result]
        if not isinstance(result, list):
            return None
        return result

    def goto_implementation(self, file_path: str, line: int, character: int) -> Optional[list[dict]]:
        """Request textDocument/implementation for a position.

        Returns list of Location dicts (each concrete implementation), or None.
        Servers that don't support this method return None gracefully.

        Args:
            file_path: Absolute path to the file.
            line: Zero-indexed line number.
            character: Zero-indexed character offset.

        Returns:
            List of location dicts with uri/range, or None on failure.
        """
        uri = Path(file_path).as_uri()
        result = self._send_request("textDocument/implementation", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if result is None:
            return None
        if isinstance(result, dict):
            result = [result]
        if not isinstance(result, list):
            return None
        return result


# ───────────────────────────────────────────────────────────────────
# Bridge: manages servers and resolves references
# ───────────────────────────────────────────────────────────────────

class LSPBridge:
    """Manages multiple LSP servers and resolves call sites to definitions.

    Usage::

        bridge = LSPBridge("/path/to/project", config)
        resolved = bridge.resolve_references(call_sites, file_contents)
        bridge.shutdown()
    """

    def __init__(
        self,
        root_path: str,
        lsp_servers: Optional[dict[str, str]] = None,
        timeout: int = DEFAULT_LSP_TIMEOUT,
    ):
        self.root_path = str(Path(root_path).resolve())
        self.lsp_servers = lsp_servers or dict(DEFAULT_LSP_SERVERS)
        self.timeout = timeout
        self._servers: dict[str, LSPServer] = {}
        self._failed_languages: set[str] = set()

    def _get_server(self, language: str) -> Optional[LSPServer]:
        """Get or start the LSP server for a language. Returns None if unavailable."""
        if language in self._failed_languages:
            return None
        if language in self._servers:
            server = self._servers[language]
            if server.is_running:
                return server
            # Server died — clean up and try to restart once
            server.stop()
            del self._servers[language]

        server_name = self.lsp_servers.get(language)
        if not server_name:
            self._failed_languages.add(language)
            return None

        command = _SERVER_COMMANDS.get(server_name)
        if not command:
            logger.debug("LSP: unknown server %s for %s", server_name, language)
            self._failed_languages.add(language)
            return None

        # Check if the binary exists on PATH
        if not shutil.which(command[0]):
            logger.debug("LSP: %s not found on PATH", command[0])
            self._failed_languages.add(language)
            return None

        server = LSPServer(language, command, self.root_path, self.timeout)
        if not server.start():
            self._failed_languages.add(language)
            return None

        self._servers[language] = server
        return server

    def resolve_references(
        self,
        call_sites: list[CallSite],
        file_contents: dict[str, str],
        file_languages: dict[str, str],
    ) -> list[ResolvedRef]:
        """Resolve call sites via LSP textDocument/definition.

        Args:
            call_sites: Unresolved call sites from tree-sitter.
            file_contents: abs_path -> content for files with call sites.
            file_languages: abs_path -> language name for files with call sites.

        Returns:
            List of successfully resolved references.
        """
        if not call_sites:
            return []

        # Group call sites by language
        sites_by_lang: dict[str, list[CallSite]] = {}
        for site in call_sites:
            lang = file_languages.get(site.file)
            if lang:
                sites_by_lang.setdefault(lang, []).append(site)

        resolved: list[ResolvedRef] = []
        opened_files: dict[str, tuple[LSPServer, str]] = {}  # file -> (server, lang_id)

        try:
            for language, sites in sites_by_lang.items():
                server = self._get_server(language)
                if not server:
                    continue

                # LSP language IDs (some differ from our language names)
                lsp_lang_id = _to_lsp_language_id(language)

                # Open files needed for this batch
                files_for_lang = {s.file for s in sites}
                for fpath in files_for_lang:
                    if fpath not in opened_files:
                        content = file_contents.get(fpath)
                        if content:
                            server.open_file(fpath, content, lsp_lang_id)
                            opened_files[fpath] = (server, lsp_lang_id)

                # Small delay to let the server index opened files
                time.sleep(0.1)

                # Resolve each call site
                for site in sites:
                    try:
                        locations = server.goto_definition(
                            site.file, site.position.line, site.position.character,
                        )
                        if not locations:
                            continue

                        loc = locations[0]  # Take the first definition
                        target_uri = loc.get("uri") or loc.get("targetUri")
                        target_range = loc.get("range") or loc.get("targetRange")

                        if not target_uri or not target_range:
                            continue

                        # Convert file URI to path
                        target_file = _uri_to_path(target_uri)
                        if not target_file:
                            continue

                        start = target_range.get("start", {})
                        resolved.append(ResolvedRef(
                            call_site=site,
                            target_file=target_file,
                            target_line=start.get("line", 0),
                            target_character=start.get("character", 0),
                            target_name=site.called_name,
                        ))
                    except Exception:
                        logger.debug(
                            "LSP: definition request failed for %s at %s:%d:%d",
                            site.called_name, site.file,
                            site.position.line, site.position.character,
                            exc_info=True,
                        )
        finally:
            # Close all opened files
            for fpath, (server, _) in opened_files.items():
                try:
                    server.close_file(fpath)
                except Exception:
                    pass

        logger.info(
            "LSP bridge resolved %d/%d call sites",
            len(resolved), len(call_sites),
        )
        return resolved

    def resolve_implementations(
        self,
        interface_methods: list[dict],
        file_contents: dict[str, str],
        file_languages: dict[str, str],
    ) -> list[DispatchEdge]:
        """Resolve interface/trait methods to their concrete implementations via LSP.

        Args:
            interface_methods: List of dicts with keys:
                file (abs path), line (0-indexed), character (0-indexed),
                interface_name, method_name, language.
            file_contents: abs_path -> content for files with interfaces.
            file_languages: abs_path -> language name.

        Returns:
            List of DispatchEdge instances (one per concrete implementation).
        """
        if not interface_methods:
            return []

        # Cap at 50 implementations per interface method to bound response size
        MAX_IMPLS_PER_METHOD = 50

        dispatch_edges: list[DispatchEdge] = []
        opened_files: dict[str, tuple[LSPServer, str]] = {}

        try:
            # Group by language
            by_lang: dict[str, list[dict]] = {}
            for im in interface_methods:
                lang = im.get("language", "")
                if lang:
                    by_lang.setdefault(lang, []).append(im)

            for language, methods in by_lang.items():
                server = self._get_server(language)
                if not server:
                    continue

                lsp_lang_id = _to_lsp_language_id(language)

                # Open needed files
                files_needed = {m["file"] for m in methods}
                for fpath in files_needed:
                    if fpath not in opened_files:
                        content = file_contents.get(fpath)
                        if content:
                            server.open_file(fpath, content, lsp_lang_id)
                            opened_files[fpath] = (server, lsp_lang_id)

                time.sleep(0.1)

                for im in methods:
                    try:
                        locations = server.goto_implementation(
                            im["file"], im["line"], im["character"],
                        )
                        if not locations:
                            continue

                        for loc in locations[:MAX_IMPLS_PER_METHOD]:
                            target_uri = loc.get("uri") or loc.get("targetUri")
                            target_range = loc.get("range") or loc.get("targetRange")
                            if not target_uri or not target_range:
                                continue

                            target_file = _uri_to_path(target_uri)
                            if not target_file:
                                continue

                            start = target_range.get("start", {})
                            dispatch_edges.append(DispatchEdge(
                                interface_file=im["file"],
                                interface_name=im["interface_name"],
                                method_name=im["method_name"],
                                impl_file=target_file,
                                impl_line=start.get("line", 0),
                                impl_name="",  # Resolved later from symbol index
                            ))
                    except Exception:
                        logger.debug(
                            "LSP: implementation request failed for %s.%s at %s:%d:%d",
                            im["interface_name"], im["method_name"], im["file"],
                            im["line"], im["character"],
                            exc_info=True,
                        )
        finally:
            for fpath, (server, _) in opened_files.items():
                try:
                    server.close_file(fpath)
                except Exception:
                    pass

        logger.info(
            "LSP dispatch resolution: %d implementations for %d interface methods",
            len(dispatch_edges), len(interface_methods),
        )
        return dispatch_edges

    def shutdown(self) -> None:
        """Stop all running LSP servers."""
        for server in self._servers.values():
            try:
                server.stop()
            except Exception:
                logger.debug("LSP: error stopping server", exc_info=True)
        self._servers.clear()
        self._failed_languages.clear()


# ───────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────

def _to_lsp_language_id(language: str) -> str:
    """Convert jcodemunch language name to LSP languageId."""
    mapping = {
        "python": "python",
        "typescript": "typescript",
        "javascript": "javascript",
        "go": "go",
        "rust": "rust",
    }
    return mapping.get(language, language)


def _uri_to_path(uri: str) -> Optional[str]:
    """Convert a file:// URI to an absolute path."""
    if not uri.startswith("file://"):
        return None
    # Handle file:///C:/... on Windows and file:///path on Unix
    from urllib.parse import unquote, urlparse
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    # On Windows, urlparse gives /C:/... — strip the leading /
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return str(Path(path).resolve())


def is_lsp_enabled(repo: Optional[str] = None) -> bool:
    """Check whether LSP enrichment is enabled in config."""
    from .. import config as _config
    enrichment = _config.get("enrichment", {}, repo=repo)
    if isinstance(enrichment, dict):
        return bool(enrichment.get("lsp_enabled", False))
    return False


def get_lsp_config(repo: Optional[str] = None) -> dict:
    """Get the full LSP enrichment config, with defaults applied."""
    from .. import config as _config
    enrichment = _config.get("enrichment", {}, repo=repo)
    if not isinstance(enrichment, dict):
        enrichment = {}
    return {
        "lsp_enabled": bool(enrichment.get("lsp_enabled", False)),
        "lsp_servers": enrichment.get("lsp_servers", dict(DEFAULT_LSP_SERVERS)),
        "lsp_timeout_seconds": int(enrichment.get("lsp_timeout_seconds", DEFAULT_LSP_TIMEOUT)),
    }


def enrich_call_graph_with_lsp(
    root_path: str,
    symbols: list,
    file_contents: dict[str, str],
    file_languages: dict[str, str],
    repo: Optional[str] = None,
) -> list[dict]:
    """High-level entry point: resolve unqualified call sites via LSP.

    Called from index_folder after tree-sitter parsing when LSP is enabled.
    Returns a list of additional call graph edges with resolution="lsp_resolved".

    Args:
        root_path: Absolute path to the project root.
        symbols: Parsed Symbol objects (with call_references and line info).
        file_contents: rel_path -> content. If empty, reads from disk.
        file_languages: rel_path -> language name.
        repo: Repo identifier for config lookup.

    Returns:
        List of dicts: {caller_file, caller_name, caller_line, called_name,
                        target_file, target_line, resolution}
    """
    config = get_lsp_config(repo)
    if not config["lsp_enabled"]:
        return []

    # Build absolute path maps
    root = Path(root_path).resolve()
    abs_contents: dict[str, str] = {}
    abs_languages: dict[str, str] = {}
    for rel_path in file_languages:
        abs_path = str(root / rel_path)
        # Use provided content or read from disk
        content = file_contents.get(rel_path)
        if content is None:
            disk_path = root / rel_path
            if disk_path.is_file():
                try:
                    content = disk_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
        if content:
            abs_contents[abs_path] = content
        lang = file_languages.get(rel_path)
        if lang:
            abs_languages[abs_path] = lang

    # Collect call sites from symbols that tree-sitter extracted
    call_sites: list[CallSite] = []
    for sym in symbols:
        sym_file = getattr(sym, "file", None) or (sym.get("file") if isinstance(sym, dict) else None)
        sym_line = getattr(sym, "line", 0) or (sym.get("line", 0) if isinstance(sym, dict) else 0)
        sym_name = getattr(sym, "name", "") or (sym.get("name", "") if isinstance(sym, dict) else "")
        call_refs = getattr(sym, "call_references", []) or (sym.get("call_references", []) if isinstance(sym, dict) else [])

        if not sym_file or not call_refs:
            continue

        abs_file = str(root / sym_file)
        lang = abs_languages.get(abs_file)
        if not lang or lang not in config["lsp_servers"]:
            continue

        # For each called name, create a call site.
        # Position approximation: use the symbol's start line (the LSP server
        # will find the call within scope). A more precise approach would track
        # column offsets from tree-sitter, but the symbol start line is a
        # reasonable heuristic since goto_definition can often resolve from
        # the call name anywhere in the function body.
        content = abs_contents.get(abs_file, "")
        if not content:
            continue

        lines = content.splitlines()
        sym_start = max(0, sym_line - 1)  # Convert to 0-indexed

        for called_name in call_refs:
            if called_name == sym_name:
                continue  # Skip self-recursion
            # Search for the called name in the symbol's body to get a precise position
            pos = _find_call_position(lines, sym_start, called_name)
            if pos:
                call_sites.append(CallSite(
                    file=abs_file,
                    position=pos,
                    called_name=called_name,
                ))

    if not call_sites:
        return []

    logger.info("LSP bridge: %d call sites to resolve across %d languages",
                len(call_sites), len({abs_languages.get(s.file) for s in call_sites}))

    bridge = LSPBridge(
        root_path=root_path,
        lsp_servers=config["lsp_servers"],
        timeout=config["lsp_timeout_seconds"],
    )
    try:
        resolved = bridge.resolve_references(call_sites, abs_contents, abs_languages)
    finally:
        bridge.shutdown()

    # Convert resolved refs back to relative paths
    edges: list[dict] = []
    for ref in resolved:
        try:
            caller_rel = str(Path(ref.call_site.file).relative_to(root))
            target_rel = str(Path(ref.target_file).relative_to(root))
        except ValueError:
            continue  # Target outside the project
        edges.append({
            "caller_file": caller_rel.replace("\\", "/"),
            "called_name": ref.call_site.called_name,
            "target_file": target_rel.replace("\\", "/"),
            "target_line": ref.target_line + 1,  # Convert back to 1-indexed
            "resolution": "lsp_resolved",
        })

    return edges


def _find_call_position(
    lines: list[str], start_line: int, called_name: str,
) -> Optional[Position]:
    """Find the first occurrence of called_name in lines starting from start_line.

    Returns a Position (0-indexed) or None.
    """
    import re
    from ..word_rule import word_left
    pattern = re.compile(word_left(called_name) + re.escape(called_name) + r"\s*\(")
    for i in range(start_line, min(start_line + 200, len(lines))):
        m = pattern.search(lines[i])
        if m:
            return Position(line=i, character=m.start())
    return None


# ───────────────────────────────────────────────────────────────────
# Dispatch resolution: interface/trait → concrete implementations
# ───────────────────────────────────────────────────────────────────

# Keywords set by _detect_interface_keywords() in parser/extractor.py
_INTERFACE_KEYWORDS = frozenset({"interface", "trait", "abstract"})


def enrich_dispatch_edges(
    root_path: str,
    symbols: list,
    file_contents: dict[str, str],
    file_languages: dict[str, str],
    repo: Optional[str] = None,
) -> list[dict]:
    """Resolve interface/trait methods to concrete implementations via LSP.

    Called from index_folder after tree-sitter parsing when LSP is enabled.
    Returns a list of dispatch edge dicts for storage in context_metadata.

    Args:
        root_path: Absolute path to the project root.
        symbols: Parsed Symbol objects (with keywords indicating interface/trait).
        file_contents: rel_path -> content. If empty, reads from disk.
        file_languages: rel_path -> language name.
        repo: Repo identifier for config lookup.

    Returns:
        List of dicts: {interface_file, interface_name, method_name,
                        impl_file, impl_line, impl_name, resolution}
    """
    config = get_lsp_config(repo)
    if not config["lsp_enabled"]:
        return []

    root = Path(root_path).resolve()

    # Step 1: Find interface/trait symbols and their child methods
    interface_methods: list[dict] = []
    # Build parent_id -> [child_symbols] map
    children_by_parent: dict[str, list] = {}
    interface_syms: list = []

    for sym in symbols:
        kw = getattr(sym, "keywords", None) or (sym.get("keywords", []) if isinstance(sym, dict) else [])
        sym_id = getattr(sym, "id", None) or (sym.get("id", "") if isinstance(sym, dict) else "")
        parent_id = getattr(sym, "parent", None) or (sym.get("parent") if isinstance(sym, dict) else None)

        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(sym)

        if not _INTERFACE_KEYWORDS.intersection(kw):
            continue
        interface_syms.append(sym)

    if not interface_syms:
        return []

    # Build absolute path maps
    abs_contents: dict[str, str] = {}
    abs_languages: dict[str, str] = {}
    for rel_path in file_languages:
        abs_path = str(root / rel_path)
        content = file_contents.get(rel_path)
        if content is None:
            disk_path = root / rel_path
            if disk_path.is_file():
                try:
                    content = disk_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
        if content:
            abs_contents[abs_path] = content
        lang = file_languages.get(rel_path)
        if lang:
            abs_languages[abs_path] = lang

    # Step 2: For each interface, collect method positions
    for iface_sym in interface_syms:
        iface_id = getattr(iface_sym, "id", None) or (iface_sym.get("id", "") if isinstance(iface_sym, dict) else "")
        iface_name = getattr(iface_sym, "name", None) or (iface_sym.get("name", "") if isinstance(iface_sym, dict) else "")
        iface_file = getattr(iface_sym, "file", None) or (iface_sym.get("file", "") if isinstance(iface_sym, dict) else "")
        if not iface_file:
            continue

        abs_file = str(root / iface_file)
        lang = abs_languages.get(abs_file)
        if not lang or lang not in config["lsp_servers"]:
            continue

        # Get child methods of this interface
        children = children_by_parent.get(iface_id, [])
        if not children:
            # For Go interfaces, methods are declared inline (no separate child symbols).
            # Use the interface symbol position itself — gopls will resolve from there.
            iface_line = getattr(iface_sym, "line", 0) or (iface_sym.get("line", 0) if isinstance(iface_sym, dict) else 0)
            if iface_line:
                content = abs_contents.get(abs_file, "")
                if content:
                    lines = content.splitlines()
                    # Scan the interface body for method names
                    iface_end = getattr(iface_sym, "end_line", 0) or (iface_sym.get("end_line", 0) if isinstance(iface_sym, dict) else 0)
                    import re
                    for li in range(max(0, iface_line - 1), min(iface_end, len(lines))):
                        line_text = lines[li].strip()
                        # Go interface methods: "MethodName(args) rettype"
                        m = re.match(r'^([A-Z]\w*)\s*\(', line_text)
                        if m:
                            interface_methods.append({
                                "file": abs_file,
                                "line": li,
                                "character": lines[li].index(m.group(1)),
                                "interface_name": iface_name,
                                "method_name": m.group(1),
                                "language": lang,
                            })
            continue

        for child in children:
            child_name = getattr(child, "name", None) or (child.get("name", "") if isinstance(child, dict) else "")
            child_line = getattr(child, "line", 0) or (child.get("line", 0) if isinstance(child, dict) else 0)
            child_kind = getattr(child, "kind", None) or (child.get("kind", "") if isinstance(child, dict) else "")

            if child_kind not in ("method", "function"):
                continue
            if not child_name or not child_line:
                continue

            interface_methods.append({
                "file": abs_file,
                "line": child_line - 1,  # Convert to 0-indexed
                "character": 0,
                "interface_name": iface_name,
                "method_name": child_name,
                "language": lang,
            })

    if not interface_methods:
        return []

    logger.info(
        "LSP dispatch: %d interface methods to resolve across %d interfaces",
        len(interface_methods), len(interface_syms),
    )

    # Step 3: Resolve via LSP
    bridge = LSPBridge(
        root_path=root_path,
        lsp_servers=config["lsp_servers"],
        timeout=config["lsp_timeout_seconds"],
    )
    try:
        dispatch_edges = bridge.resolve_implementations(
            interface_methods, abs_contents, abs_languages,
        )
    finally:
        bridge.shutdown()

    # Step 4: Convert to relative paths and serializable dicts
    edges: list[dict] = []
    for de in dispatch_edges:
        try:
            iface_rel = str(Path(de.interface_file).relative_to(root))
            impl_rel = str(Path(de.impl_file).relative_to(root))
        except ValueError:
            continue
        edges.append({
            "interface_file": iface_rel.replace("\\", "/"),
            "interface_name": de.interface_name,
            "method_name": de.method_name,
            "impl_file": impl_rel.replace("\\", "/"),
            "impl_line": de.impl_line + 1,  # Convert to 1-indexed
            "impl_name": de.impl_name,
            "resolution": "lsp_dispatch",
        })

    return edges
