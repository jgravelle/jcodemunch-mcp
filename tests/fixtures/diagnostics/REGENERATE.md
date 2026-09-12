# Regenerating the diagnostics fixtures

Captured 2026-09-12 from the real tools, not authored from their docs
(a fixture written from a schema tests nothing). `sample_mod.py` and
`sample_a.ts` are the inputs; every fixture is that tool's verbatim output
over them, with one edit: the absolute scratch prefix in `ruff.json` and
`pyright.json` is rewritten to `C:\work\diagfix` so the fixture is portable
while still exercising absolute-path resolution.

Layout at capture time: `<root>/pkg/mod.py` (= sample_mod.py, with an empty
`pkg/__init__.py`), `<root>/ts/a.ts` (= sample_a.ts) and
`<root>/ts/tsconfig.json` = `{"compilerOptions":{"strict":true,"noEmit":true,
"target":"es2020"},"include":["a.ts"]}`.

    ruff check --output-format json pkg > ruff.json        # ruff 0.7.1
    uvx mypy --output json pkg > mypy.jsonl                # mypy 1.x
    uvx pyright --outputjson pkg > pyright.json            # pyright 1.1.414
    cd ts && npx -y -p typescript tsc --noEmit --pretty false -p tsconfig.json > ../tsc.txt

All four exit 1 (they found errors). Re-capture when a tool changes its
output shape; the version that produced each file is in the tool's own
header where it prints one (`pyright.json` `version`).
