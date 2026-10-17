# Third-party SQL corpora — sources

The `.sql` files under this directory are **not this project's code**. They are
vendored from the projects below and used only as parser test fixtures.

Per-file provenance — upstream repository, ref, path, and the sha256 of the
artifact each file came from — is in `../public_sql_corpora.json`. Every file
was verified against that hash as it was vendored.

| Directory | Project | Copyright | Licence |
|---|---|---|---|
| `chinook/` | [lerocha/chinook-database](https://github.com/lerocha/chinook-database) | © 2008–2024 Luis Rocha | MIT — [LICENSE.md](https://github.com/lerocha/chinook-database/blob/master/LICENSE.md) |
| `sakila/` | [jOOQ/sakila](https://github.com/jOOQ/sakila) | © Oracle and/or its affiliates; jOOQ ports | BSD-2-Clause — [LICENSE](https://github.com/jOOQ/sakila/blob/main/LICENSE) |
| `sqlfluff_tsql/` | [sqlfluff/sqlfluff](https://github.com/sqlfluff/sqlfluff) (`test/fixtures/dialects/tsql`) | © SQLFluff contributors | MIT — [LICENSE.md](https://github.com/sqlfluff/sqlfluff/blob/main/LICENSE.md) |
| `adventureworks/` | [microsoft/sql-server-samples](https://github.com/microsoft/sql-server-samples) | © Microsoft Corporation | MIT — [license.txt](https://github.com/microsoft/sql-server-samples/blob/master/license.txt) |

⚠ The Chinook files are a **DDL slice**: everything before the first
line-initial `INSERT` of the upstream file. Those files are ~590 KB each and
all but the first ~220 lines is INSERT data no parser test reads. The rule is
deterministic, so the slice is reproducible from the upstream sha256 in the
manifest. Every other file is byte-for-byte upstream.

Vendored rather than downloaded on demand: a test that reaches the network
fails for reasons unrelated to the change under review, and cannot run in an
air-gapped checkout at all.
