# Fairness note: jcodemunch (our own row)

Our row is driven the way the tool's own descriptions say (DESIGN s1.4;
ARCHAEOLOGY R27, R28): the shipped defaults, no config file, AI summaries
off, one configuration (the `counter` surface and `include_call_chain`
variants have no producer yet, CF-54). Every call is charged; nothing is
tuned to a task set's gold.

## Call plan

| category | calls | why this and not another |
|---|---|---|
| P1, T | `search_symbols(query, max_results=5, detail_level="standard")`, then `get_symbol_source` on the top 3 | the documented lookup path; three source reads is what an agent does with a five-row hit list (R27) |
| P2 | `check_references(identifier=query)` at its defaults | the tool whose own description is the question ("is this identifier referenced anywhere: imports + file content"). Its content search is a case-insensitive substring match per line with the identifier's own definition spans excluded, capped at 20 FILES (not lines) by `max_content_results`. The cap is the tool's shipped default and stays: raising it for a gold set that has 95 usage lines would be tuning our row to the benchmark, the thing every competitor note forbids. A corpus whose usages sit in more than 20 files scores capped recall on our row, and the cap is the finding |
| P4 | `find_importers(file_path=query)` | the documented file-dependency tool; files cited at line 0, as the gold expects |

## What the first mapping got wrong (CF-51)

Until 2026-09-06 the P2 call was `find_references(identifier=query)`. That
tool is the IMPORT-graph tool: its description says it answers "where is
this imported / re-exported?" and "does NOT exhaustively enumerate every
call site". On a single-file library nothing imports `map`, so the reply
was `reference_count 0` with a tip naming `search_text` and
`check_references`. The 0 was a harness mapping defect AND a real loss: a
user who reaches for `find_references` for the usage-site question gets
the same answer, and the guidance that steers users there is outside this
tier (a product-doc finding, not an adapter one). The recorded run
`results/2026-09-06-0e3a1706.json` carries the 0 rows; the next recorded
run carries the new mapping, and the trend section will show the move as
OUR movement, which it is.

## Disadvantages this note accepts

1. The content match is substring, not word: `map` also matches `mapping`,
   and every such line is charged and counted against precision. Not
   narrowed, because the tool ships that way.
2. The 20-file cap bounds recall on large corpora. Not raised (above).
3. `find_importers` cites files at line 0; a gold that carries import lines
   is matched at file level only, which the scorer's tolerance allows for
   P4 by construction.
4. The P2 reply's import-graph rows are files, not lines. The worker cites
   such a file at line 0 only when none of its lines is already a content
   citation (a file that imports X contains the text X, so the two halves
   of the reply name the same file); citing both would be the worker's
   own duplicate, not the tool's, and would cost precision on every task.
   The first draft did that, and review round 1 of CF-51 caught it before
   a number was recorded.
