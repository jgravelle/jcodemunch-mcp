# Cache & Invalidation Specification

## Storage Layout

```
~/.code-index/                    (or CODE_INDEX_PATH)
├── {owner}-{name}.json           Index metadata + symbols
├── {owner}-{name}.json.tmp       Temporary file during atomic writes
└── {owner}-{name}/               Raw file content directory
    ├── src/
    │   └── main.py
    └── tests/
        └── test_main.py
```

---

## Cache Keys

Each repository is identified by `{owner}-{name}`:

* **GitHub repositories**: owner and repository name from the URL
  Example: `pydantic-monty`
* **Local folders**: `local-{folder_name}`
  Example: `local-myproject`

---

## Index Schema

```json
{
  "repo": "owner/name",
  "owner": "owner",
  "name": "name",
  "indexed_at": "2025-01-15T10:30:00",
  "index_version": 2,
  "git_head": "abc123...",
  "file_hashes": {
    "src/main.py": "sha256hex...",
    "src/utils.py": "sha256hex..."
  },
  "source_files": ["src/main.py", "src/utils.py"],
  "languages": {"python": 2},
  "symbols": [...]
}
```

---

## Index Versioning

* `index_version` is stored in every index JSON.
* The current version is defined by the `INDEX_VERSION` constant.
* If a stored index has a **newer** version than the running software, the index is rejected.
* Older versions are loaded with missing optional fields populated using defaults.
* Increment `INDEX_VERSION` only for **breaking schema changes**.

---

## File Hash Change Detection

Each indexed file stores a SHA-256 content hash in `file_hashes`.

During incremental indexing:

1. Compute hashes for current files.
2. Compare with stored hashes.
3. Classify files as:

   * **Changed** — hash differs
   * **New** — not present in prior index
   * **Deleted** — present previously but missing now

---

## Incremental Indexing

When `incremental=True` and a prior index exists:

1. Detect file changes via `IndexStore.detect_changes()`.
2. Re-parse only changed and new files.
3. Remove symbols belonging to changed or deleted files.
4. Merge newly extracted symbols into the index.
5. Update file hashes, source file lists, and language counts.
6. Save the updated index atomically.

If no prior index exists, a full index is created automatically.

---

## Git Branch Switching

For Git-based repositories:

* `git_head` records the repository HEAD commit at index time.
* On re-index, a changed HEAD indicates potential file changes.
* A full or incremental re-index is triggered depending on configuration.
* Commit detection uses `git rev-parse HEAD` with a bounded execution timeout.

---

## Invalidation

### Manual Invalidation

* `invalidate_cache(repo)` MCP tool deletes the index JSON and cached file directory.
* `IndexStore.delete_index(owner, name)` performs the same operation programmatically.

### Automatic Invalidation

* Re-indexing overwrites existing indexes.
* Index-version mismatches cause the stored index to be ignored automatically.

---

## Atomic Writes

Indexes are written using a safe two-step process:

1. Write to `{owner}-{name}.json.tmp`
2. Rename to `{owner}-{name}.json`

This prevents corrupted indexes caused by partial writes or process interruptions.

---

## Hash Strategy

* **File hashes**: SHA-256 of UTF-8 encoded file content
* **Symbol hashes**: SHA-256 of raw symbol source bytes (used for drift detection)
* All hashes are stored as hexadecimal strings
