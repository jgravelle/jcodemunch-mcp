# Security Controls

jcodemunch-mcp indexes source code from local folders and GitHub repositories. This document describes the security controls that protect against common risks when handling arbitrary codebases.

---

## Path Traversal Prevention

All user-supplied paths are validated before any file is read or written.

* **`validate_path(root, target)`** resolves both paths to absolute form and verifies the target is a descendant of `root` using `os.path.commonpath()`.
* Applied during file discovery and again before each file read (defense in depth).
* Paths such as `../../etc/passwd` or absolute paths outside the repository root are rejected.

---

## Symlink Escape Protection

Symlinks can be used to escape the repository root and read arbitrary files.

* **Default:** `follow_symlinks=False` — symlinks are skipped during file discovery.
* When symlinks are followed (`follow_symlinks=True`), each symlink target is resolved and validated against the repository root. Escaping symlinks are skipped with a warning.
* **`is_symlink_escape(root, path)`** checks whether a symlink resolves outside the root.
* On Windows, environments without symlink support automatically skip symlink traversal.

---

## Default Ignore Policy

Files are filtered through multiple layers:

1. **SKIP_PATTERNS** — directories and files always excluded (e.g., `node_modules/`, `vendor/`, `.git/`, `build/`, `dist/`, generated files, lock files).
2. **`.gitignore`** — respected by default for both local folders and GitHub repositories (via the `pathspec` library).
3. **`extra_ignore_patterns`** — user-configurable additional gitignore-style patterns passed to indexing tools.

---

## Secret Exclusion

Files matching known secret patterns are excluded during indexing.

**Excluded patterns include:**

* Environment files: `.env`, `.env.*`, `*.env`
* Certificates / keys: `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.keystore`, `*.jks`
* SSH keys: `id_rsa*`, `id_ed25519*`, `id_dsa*`, `id_ecdsa*`
* Credentials: `credentials.json`, `service-account*.json`, `*.credentials`
* Auth files: `.htpasswd`, `.netrc`, `.npmrc`, `.pypirc`
* Generic secret indicators: `*secret*`, `*.secrets`, `*.token`

When a secret file is detected, a warning is included in the indexing response. Secret files are never stored in the index or cached content directory.

---

## File Size Limits

* **Default maximum:** 500 KB per file (configurable via `max_file_size`).
* Files exceeding the limit are skipped during discovery.
* A configurable **file count limit** (default: 500 files) prevents runaway indexing of extremely large repositories. Can be overridden using the `JCODEMUNCH_MAX_INDEX_FILES` environment variable.

---

## Binary File Detection

Binary files are excluded using a two-stage check:

1. **Extension-based detection** — common binary extensions (`.exe`, `.dll`, `.so`, `.png`, `.jpg`, `.zip`, `.wasm`, `.pyc`, `.class`, `.pdf`, `.db`, `.sqlite`, etc.).
2. **Content-based detection** — files containing null bytes within the first 8 KB are treated as binary and skipped, even if the extension suggests source code.

---

## Encoding Safety

* All file reads use `errors="replace"` to substitute invalid UTF-8 bytes with the Unicode replacement character (U+FFFD) instead of raising decode errors.
* Symbol content retrieval also uses `errors="replace"` to ensure safe decoding.
* Cached raw files are stored using UTF-8 encoding.

---

## Storage Safety

* Index storage defaults to `~/.code-index/`.
* The storage path can be overridden using the `CODE_INDEX_PATH` environment variable.
* Repository identifiers are derived from `{owner}-{name}`, preventing path injection in storage locations.
* Index files are stored as JSON and validated during load to ensure schema integrity.

---

## Summary of Controls

| Control                   | Location                       | Default                     |
| ------------------------- | ------------------------------ | --------------------------- |
| Path traversal validation | `security.validate_path()`     | Always enabled              |
| Symlink escape protection | `security.is_symlink_escape()` | Symlinks skipped by default |
| Secret file exclusion     | `security.is_secret_file()`    | Always enabled              |
| Binary file detection     | `security.is_binary_file()`    | Always enabled              |
| File size limit           | File discovery pipeline        | 500 KB                      |
| File count limit          | File discovery pipeline        | 500 files                   |
| `.gitignore` respect      | Indexing pipeline              | Enabled                     |
| UTF-8 safe decode         | All file reads                 | `errors="replace"`          |
