# Competitive tier — 2026-10-19T01:02:18Z at bf118f74 (1.108.378)

A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: 3.

Corpora: `self@bf118f74` 295 files, sha256 `17faf099cb31`; `expressjs/express@1faf228` 234 files, sha256 `01d66b1a7167`; `fastapi/fastapi@a64dfbb` 2939 files, sha256 `d224adeb56fe`; `gin-gonic/gin@75ccf94` 120 files, sha256 `581f4ed0c5f6`; `lodash/lodash@f299b52` 91 files, sha256 `43d8e1a4a3df`; `psf/requests@0e322af` 119 files, sha256 `f0a00825e6d9`; `nestjs/nest@6494a6c` 2300 files, sha256 `0a5c9fff93a0`; `spring-projects/spring-framework@82a6b40` 11316 files, sha256 `712269bd145b`; `angular/angular@468b65b` 10328 files, sha256 `e26cd31e5520`

Sandbox: `docker` (every row in the D2 container: --network none, read-only rootfs, no capabilities, uid 65534, 8g, 512 pids); tree dirty: False; scorer sha256 `b92be8f0ed53`

Pins: `null_readall` none:read-all@baseline-A (ran as baseline-A); `null_grep` none:grep-top-3@baseline-B (ran as baseline-B); `jcodemunch` tree:jcodemunch-mcp@bf118f74 (ran as bf118f74, image `3c3ff65a9eca`, built in 16.7 s, 380.6 MiB, 0 prerequisites, fairness `ca0521a06434`); `jcodemunch_counter` tree:jcodemunch-mcp@bf118f74 (ran as bf118f74, image `f0cef2ce9da5`, built in 12.5 s, 380.6 MiB, 0 prerequisites, fairness `ca0521a06434`); `cymbal` github-release:1broseidon/cymbal@0.14.0 (ran as 0.14.0, image `f962069d0442`, built in 6.5 s, 125.4 MiB, 3 prerequisites (ca-certificates, curl, jq), fairness `a8fb42009099`); `codebase_memory` github-release:DeusData/codebase-memory-mcp@0.10.8 (ran as 0.10.8, image `63dca8467f80`, built in 9.9 s, 484.4 MiB, 4 prerequisites (ca-certificates, curl, git, jq), fairness `4f98331002dd`); `code_review_graph` pypi:code-review-graph@2.3.8 (ran as 2.3.8, image `a95e7addea64`, built in 21.2 s, 502.8 MiB, 1 prerequisites (git), fairness `b9728719f7f1`); `serena` pypi:serena-agent@1.7.0 (ran as 1.7.0, image `4cb89847c355`, built in 24.1 s, 686.6 MiB, 1 prerequisites (git), fairness `33137a52deaa`); `codegraph` github-release:colbymchenry/codegraph@1.6.0 (ran as 1.6.0, image `57566576c2cd`, built in 10.9 s, 483.0 MiB, 3 prerequisites (ca-certificates, curl, git), fairness `9ff5e959dba7`); `graft` npm:@nanonets/graft@0.16.0 (ran as 0.16.0, image `7a6b9bc076d0`, built in 57.3 s, 1026.7 MiB, 5 prerequisites (ca-certificates, g++, git, make, python3), fairness `9c92ece21869`); `aider` pypi:aider-chat@0.86.2 (ran as 0.86.2, image `3cd56d89cd04`, built in 37.7 s, 821.8 MiB, 1 prerequisites (git), fairness `1d25e6277ad1`); `cocoindex` pypi:cocoindex-code@0.2.41 (ran as 0.2.41, image `1bb71e785934`, built in 107.3 s, 1793.5 MiB, 1 prerequisites (git), fairness `f2b3009a9029`); `zvec_grep` npm:@zvec/zvec-grep@0.2.2 (ran as 0.2.2, image `e0909e0ce031`, built in 46.9 s, 2754.1 MiB, 5 prerequisites (ca-certificates, g++, git, make, python3), fairness `9787945593c7`)

## tokens_per_task (ratio vs jcm)

| tool | self@bf118f74 | expressjs/express@1faf228 | fastapi/fastapi@a64dfbb | gin-gonic/gin@75ccf94 | lodash/lodash@f299b52 | psf/requests@0e322af | nestjs/nest@6494a6c | spring-projects/spring-framework@82a6b40 | angular/angular@468b65b |
|---|---|---|---|---|---|---|---|---|---|
| null_readall | 1.4e+06 spread 0 [NOT COMPARABLE] | 2.06e+05 spread 0 [NOT COMPARABLE] | 9.079e+06 spread 0 [NOT COMPARABLE] | 2.134e+05 spread 0 [NOT COMPARABLE] | 1.239e+06 spread 0 [NOT COMPARABLE] | 2.397e+06 spread 0 [NOT COMPARABLE] | 1.412e+06 spread 0 [NOT COMPARABLE] | 1.334e+07 spread 0 [NOT COMPARABLE] | 1.578e+07 spread 0 [NOT COMPARABLE] |
| null_grep | 1.407e+05 spread 0 [NOT COMPARABLE] | 2.354e+04 spread 0 [NOT COMPARABLE] | 1.689e+05 spread 0 [NOT COMPARABLE] | 3.575e+04 spread 0 [NOT COMPARABLE] | 3.924e+05 spread 0 [NOT COMPARABLE] | 3.17e+04 spread 0 [NOT COMPARABLE] | 4.249e+04 spread 0 [NOT COMPARABLE] | 5.855e+04 spread 0 [NOT COMPARABLE] | 2.227e+05 spread 0 [NOT COMPARABLE] |
| jcodemunch | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| jcodemunch_counter (variant of jcodemunch) | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cymbal | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| codebase_memory | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| code_review_graph | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| serena | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| codegraph | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| graft | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| aider | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cocoindex | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| zvec_grep | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |

## calls_per_task (ratio vs jcm)

| tool | self@bf118f74 | expressjs/express@1faf228 | fastapi/fastapi@a64dfbb | gin-gonic/gin@75ccf94 | lodash/lodash@f299b52 | psf/requests@0e322af | nestjs/nest@6494a6c | spring-projects/spring-framework@82a6b40 | angular/angular@468b65b |
|---|---|---|---|---|---|---|---|---|---|
| null_readall | 295 spread 0 [NOT COMPARABLE] | 234 spread 0 [NOT COMPARABLE] | 2939 spread 0 [NOT COMPARABLE] | 120 spread 0 [NOT COMPARABLE] | 91 spread 0 [NOT COMPARABLE] | 119 spread 0 [NOT COMPARABLE] | 2300 spread 0 [NOT COMPARABLE] | 1.132e+04 spread 0 [NOT COMPARABLE] | 1.033e+04 spread 0 [NOT COMPARABLE] |
| null_grep | 3.6 spread 0 [NOT COMPARABLE] | 3.143 spread 0 [NOT COMPARABLE] | 4 spread 0 [NOT COMPARABLE] | 4 spread 0 [NOT COMPARABLE] | 3.741 spread 0 [NOT COMPARABLE] | 3.464 spread 0 [NOT COMPARABLE] | 3.2 spread 0 [NOT COMPARABLE] | 3.4 spread 0 [NOT COMPARABLE] | 3.4 spread 0 [NOT COMPARABLE] |
| jcodemunch | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| jcodemunch_counter (variant of jcodemunch) | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cymbal | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| codebase_memory | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| code_review_graph | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| serena | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| codegraph | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| graft | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| aider | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cocoindex | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| zvec_grep | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |

## latency_call_ms (ratio vs jcm)

Median wall time of ONE call, over every call of every task. The operations differ by tool (a symbol fetch, a whole-file read), so this is what an agent waits per call, not a like-for-like operation.

| tool | self@bf118f74 | expressjs/express@1faf228 | fastapi/fastapi@a64dfbb | gin-gonic/gin@75ccf94 | lodash/lodash@f299b52 | psf/requests@0e322af | nestjs/nest@6494a6c | spring-projects/spring-framework@82a6b40 | angular/angular@468b65b |
|---|---|---|---|---|---|---|---|---|---|
| null_readall | 9.75 spread 0.13 [NOT COMPARABLE] | 5.41 spread 0.03 [NOT COMPARABLE] | 92.42 spread 0.9 [NOT COMPARABLE] | 3.08 spread 0.06 [NOT COMPARABLE] | 3.42 spread 0.14 [NOT COMPARABLE] | 25.78 spread 0.52 [NOT COMPARABLE] | 51.34 spread 0.82 [NOT COMPARABLE] | 285.9 spread 3.66 [NOT COMPARABLE] | 270 spread 3.79 [NOT COMPARABLE] |
| null_grep | 0.24 spread 0 [NOT COMPARABLE] | 0.04 spread 0.01 [NOT COMPARABLE] | 0.25 spread 0.02 [NOT COMPARABLE] | 0.06 spread 0.01 [NOT COMPARABLE] | 0.2 spread 0.02 [NOT COMPARABLE] | 0.08 spread 0 [NOT COMPARABLE] | 0.04 spread 0.01 [NOT COMPARABLE] | 0.07 spread 0.01 [NOT COMPARABLE] | 0.18 spread 0.02 [NOT COMPARABLE] |
| jcodemunch | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| jcodemunch_counter (variant of jcodemunch) | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cymbal | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| codebase_memory | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| code_review_graph | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| serena | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| codegraph | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| graft | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| aider | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cocoindex | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| zvec_grep | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |

## f1_P1 (difference vs jcm)

| tool | self@bf118f74 | expressjs/express@1faf228 | fastapi/fastapi@a64dfbb | gin-gonic/gin@75ccf94 | lodash/lodash@f299b52 | psf/requests@0e322af | nestjs/nest@6494a6c | spring-projects/spring-framework@82a6b40 | angular/angular@468b65b |
|---|---|---|---|---|---|---|---|---|---|
| null_readall | 0 spread 0 [NOT COMPARABLE] | 0.0001 spread 0 [NOT COMPARABLE] | NOT COMPARABLE | NOT COMPARABLE | 0 spread 0 [NOT COMPARABLE] | 0.0001 spread 0 [NOT COMPARABLE] | 0 spread 0 [NOT COMPARABLE] | 0 spread 0 [NOT COMPARABLE] | 0 spread 0 [NOT COMPARABLE] |
| null_grep | 0.2299 spread 0 [NOT COMPARABLE] | 0.204 spread 0 [NOT COMPARABLE] | NOT COMPARABLE | NOT COMPARABLE | 0.00809 spread 0 [NOT COMPARABLE] | 0.01524 spread 0 [NOT COMPARABLE] | 0.2222 spread 0 [NOT COMPARABLE] | 0.0023 spread 0 [NOT COMPARABLE] | 0.0017 spread 0 [NOT COMPARABLE] |
| jcodemunch | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| jcodemunch_counter (variant of jcodemunch) | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cymbal | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| codebase_memory | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| code_review_graph | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| serena | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| codegraph | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| graft | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| aider | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cocoindex | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| zvec_grep | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |

## f1_P2 (difference vs jcm)

| tool | self@bf118f74 | expressjs/express@1faf228 | fastapi/fastapi@a64dfbb | gin-gonic/gin@75ccf94 | lodash/lodash@f299b52 | psf/requests@0e322af | nestjs/nest@6494a6c | spring-projects/spring-framework@82a6b40 | angular/angular@468b65b |
|---|---|---|---|---|---|---|---|---|---|
| null_readall | 0 spread 0 [NOT COMPARABLE] | 0.00055 spread 0 [NOT COMPARABLE] | NOT COMPARABLE | NOT COMPARABLE | 0.00047 spread 0 [NOT COMPARABLE] | 0.00191 spread 0 [NOT COMPARABLE] | 0.0001 spread 0 [NOT COMPARABLE] | 0.0001 spread 0 [NOT COMPARABLE] | 0.00015 spread 0 [NOT COMPARABLE] |
| null_grep | 0.1818 spread 0 [NOT COMPARABLE] | 0.3885 spread 0 [NOT COMPARABLE] | NOT COMPARABLE | NOT COMPARABLE | 0.1024 spread 0 [NOT COMPARABLE] | 0.02802 spread 0 [NOT COMPARABLE] | 0.1363 spread 0 [NOT COMPARABLE] | 0.071 spread 0 [NOT COMPARABLE] | 0.00225 spread 0 [NOT COMPARABLE] |
| jcodemunch | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| jcodemunch_counter (variant of jcodemunch) | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cymbal | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| codebase_memory | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| code_review_graph | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| serena | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| codegraph | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| graft | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| aider | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cocoindex | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| zvec_grep | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |

## f1_P4 (difference vs jcm)

| tool | self@bf118f74 | expressjs/express@1faf228 | fastapi/fastapi@a64dfbb | gin-gonic/gin@75ccf94 | lodash/lodash@f299b52 | psf/requests@0e322af | nestjs/nest@6494a6c | spring-projects/spring-framework@82a6b40 | angular/angular@468b65b |
|---|---|---|---|---|---|---|---|---|---|
| null_readall | 0.0004 spread 0 [NOT COMPARABLE] | 0.00035 spread 0 [NOT COMPARABLE] | NOT COMPARABLE | NOT COMPARABLE | 2.5e-05 spread 0 [NOT COMPARABLE] | 0.00016 spread 0 [NOT COMPARABLE] | 0 spread 0 [NOT COMPARABLE] | 0 spread 0 [NOT COMPARABLE] | 0 spread 0 [NOT COMPARABLE] |
| null_grep | 0 spread 0 [NOT COMPARABLE] | 0 spread 0 [NOT COMPARABLE] | NOT COMPARABLE | NOT COMPARABLE | 0 spread 0 [NOT COMPARABLE] | 0 spread 0 [NOT COMPARABLE] | 0 spread 0 [NOT COMPARABLE] | 0 spread 0 [NOT COMPARABLE] | 0 spread 0 [NOT COMPARABLE] |
| jcodemunch | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| jcodemunch_counter (variant of jcodemunch) | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cymbal | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| codebase_memory | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| code_review_graph | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| serena | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| codegraph | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| graft | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| aider | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| cocoindex | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |
| zvec_grep | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE | NOT COMPARABLE |

## Not runnable

- `aider`: index failed: /out/run.sh: 4: cannot create /out/index.txt: Permission denied
/out/run.sh: 5: cannot create /out/angular-T-1.txt: Permission denied
/out/run.sh: 6: cannot create /out/angular-T-2.txt: Permission den
- `aider`: index failed: /out/run.sh: 4: cannot create /out/index.txt: Permission denied
/out/run.sh: 5: cannot create /out/ex-T-1.txt: Permission denied
/out/run.sh: 6: cannot create /out/ex-T-2.txt: Permission denied
/out/r
- `aider`: index failed: /out/run.sh: 4: cannot create /out/index.txt: Permission denied
/out/run.sh: 5: cannot create /out/fastapi-T-1.txt: Permission denied
/out/run.sh: 6: cannot create /out/fastapi-T-2.txt: Permission den
- `aider`: index failed: /out/run.sh: 4: cannot create /out/index.txt: Permission denied
/out/run.sh: 5: cannot create /out/gin-T-1.txt: Permission denied
/out/run.sh: 6: cannot create /out/gin-T-2.txt: Permission denied
/out
- `aider`: index failed: /out/run.sh: 4: cannot create /out/index.txt: Permission denied
/out/run.sh: 5: cannot create /out/ld-T-1.txt: Permission denied
/out/run.sh: 6: cannot create /out/ld-T-2.txt: Permission denied
/out/r
- `aider`: index failed: /out/run.sh: 4: cannot create /out/index.txt: Permission denied
/out/run.sh: 5: cannot create /out/nest-T-1.txt: Permission denied
/out/run.sh: 6: cannot create /out/nest-T-2.txt: Permission denied
/o
- `aider`: index failed: /out/run.sh: 4: cannot create /out/index.txt: Permission denied
/out/run.sh: 5: cannot create /out/rq-T-1.txt: Permission denied
/out/run.sh: 6: cannot create /out/rq-T-2.txt: Permission denied
/out/r
- `aider`: index failed: /out/run.sh: 4: cannot create /out/index.txt: Permission denied
/out/run.sh: 5: cannot create /out/self-T-router.txt: Permission denied
/out/run.sh: 6: cannot create /out/self-T-middleware.txt: Permis
- `aider`: index failed: /out/run.sh: 4: cannot create /out/index.txt: Permission denied
/out/run.sh: 5: cannot create /out/spring-T-1.txt: Permission denied
/out/run.sh: 6: cannot create /out/spring-T-2.txt: Permission denie
- `cocoindex`: index failed: init/index did not run
- `cocoindex`: index failed: init/index did not run
- `cocoindex`: index failed: init/index did not run
- `cocoindex`: index failed: init/index did not run
- `cocoindex`: index failed: init/index did not run
- `cocoindex`: index failed: init/index did not run
- `cocoindex`: index failed: init/index did not run
- `cocoindex`: index failed: init/index did not run
- `cocoindex`: index failed: init/index did not run
- `code_review_graph`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `code_review_graph`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `code_review_graph`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `code_review_graph`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `code_review_graph`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `code_review_graph`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `code_review_graph`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `code_review_graph`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `code_review_graph`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `codebase_memory`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `codebase_memory`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `codebase_memory`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `codebase_memory`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `codebase_memory`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `codebase_memory`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `codebase_memory`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `codebase_memory`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `codebase_memory`: index failed: Traceback (most recent call last):
  File "/opt/mcp_driver.py", line 183, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/mcp_driver.py", line 178, in main
    with o
- `codegraph`: index failed: : Permission denied
/bin/sh: 1: cannot create /out/0-init.txt: Permission denied
/bin/sh: 1: cannot create /out/0-status.log: Permission denied
Traceback (most recent call last):
  File "/opt/mcp_driv
- `codegraph`: index failed: : Permission denied
/bin/sh: 1: cannot create /out/0-init.txt: Permission denied
/bin/sh: 1: cannot create /out/0-status.log: Permission denied
Traceback (most recent call last):
  File "/opt/mcp_driv
- `codegraph`: index failed: : Permission denied
/bin/sh: 1: cannot create /out/0-init.txt: Permission denied
/bin/sh: 1: cannot create /out/0-status.log: Permission denied
Traceback (most recent call last):
  File "/opt/mcp_driv
- `codegraph`: index failed: : Permission denied
/bin/sh: 1: cannot create /out/0-init.txt: Permission denied
/bin/sh: 1: cannot create /out/0-status.log: Permission denied
Traceback (most recent call last):
  File "/opt/mcp_driv
- `codegraph`: index failed: : Permission denied
/bin/sh: 1: cannot create /out/0-init.txt: Permission denied
/bin/sh: 1: cannot create /out/0-status.log: Permission denied
Traceback (most recent call last):
  File "/opt/mcp_driv
- `codegraph`: index failed: : Permission denied
/bin/sh: 1: cannot create /out/0-init.txt: Permission denied
/bin/sh: 1: cannot create /out/0-status.log: Permission denied
Traceback (most recent call last):
  File "/opt/mcp_driv
- `codegraph`: index failed: : Permission denied
/bin/sh: 1: cannot create /out/0-init.txt: Permission denied
/bin/sh: 1: cannot create /out/0-status.log: Permission denied
Traceback (most recent call last):
  File "/opt/mcp_driv
- `codegraph`: index failed: : Permission denied
/bin/sh: 1: cannot create /out/0-init.txt: Permission denied
/bin/sh: 1: cannot create /out/0-status.log: Permission denied
Traceback (most recent call last):
  File "/opt/mcp_driv
- `codegraph`: index failed: : Permission denied
/bin/sh: 1: cannot create /out/0-init.txt: Permission denied
/bin/sh: 1: cannot create /out/0-status.log: Permission denied
Traceback (most recent call last):
  File "/opt/mcp_driv
- `cymbal`: index failed: cannot create /out/angular-p4-02.0.json: Permission denied
/out/run.sh: 18: cannot create /out/angular-T-1.0.txt: Permission denied
/out/run.sh: 19: cannot create /out/angular-T-1.0.json: Permission d
- `cymbal`: index failed: : Permission denied
/out/run.sh: 54: cannot create /out/ex-T-2.0.json: Permission denied
/out/run.sh: 56: cannot create /out/ex-T-3.0.txt: Permission denied
/out/run.sh: 57: cannot create /out/ex-T-3.
- `cymbal`: index failed: : cannot create /out/fastapi-T-2.0.json: Permission denied
/out/run.sh: 10: cannot create /out/fastapi-T-3.0.txt: Permission denied
/out/run.sh: 11: cannot create /out/fastapi-T-3.0.json: Permission d
- `cymbal`: index failed: ission denied
/out/run.sh: 8: cannot create /out/gin-T-2.0.json: Permission denied
/out/run.sh: 10: cannot create /out/gin-T-3.0.txt: Permission denied
/out/run.sh: 11: cannot create /out/gin-T-3.0.js
- `cymbal`: index failed: Permission denied
/out/run.sh: 51: cannot create /out/ld-p4-05.0.json: Permission denied
/out/run.sh: 52: cannot create /out/ld-T-1.0.txt: Permission denied
/out/run.sh: 53: cannot create /out/ld-T-1.
- `cymbal`: index failed: ied
/out/run.sh: 17: cannot create /out/nest-p4-02.0.json: Permission denied
/out/run.sh: 18: cannot create /out/nest-T-1.0.txt: Permission denied
/out/run.sh: 19: cannot create /out/nest-T-1.0.json: 
- `cymbal`: index failed: Permission denied
/out/run.sh: 53: cannot create /out/rq-p4-05.0.json: Permission denied
/out/run.sh: 54: cannot create /out/rq-T-1.0.txt: Permission denied
/out/run.sh: 55: cannot create /out/rq-T-1.
- `cymbal`: index failed: 
/out/run.sh: 23: cannot create /out/self-P1-ProgressReporter.0.txt: Permission denied
/out/run.sh: 24: cannot create /out/self-P1-ProgressReporter.0.json: Permission denied
/out/run.sh: 25: cannot cr
- `cymbal`: index failed: h: 17: cannot create /out/spring-p4-02.0.json: Permission denied
/out/run.sh: 18: cannot create /out/spring-T-1.0.txt: Permission denied
/out/run.sh: 19: cannot create /out/spring-T-1.0.json: Permissi
- `graft`: index failed: build did not run
- `graft`: index failed: build did not run
- `graft`: index failed: build did not run
- `graft`: index failed: build did not run
- `graft`: index failed: build did not run
- `graft`: index failed: build did not run
- `graft`: index failed: build did not run
- `graft`: index failed: build did not run
- `graft`: index failed: build did not run
- `jcodemunch`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch_counter`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch_counter`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch_counter`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch_counter`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch_counter`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch_counter`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch_counter`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch_counter`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `jcodemunch_counter`: index failed: Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in <module>
    sys.exit(main(sys.argv))
             ~~~~^^^^^^^^^^
  File "/opt/jcm_worker.py", line 79, in main
    r = ind
- `serena`: index failed: mkdir: cannot create directory '/out/serena-home': Permission denied

- `serena`: index failed: mkdir: cannot create directory '/out/serena-home': Permission denied

- `serena`: index failed: mkdir: cannot create directory '/out/serena-home': Permission denied

- `serena`: index failed: mkdir: cannot create directory '/out/serena-home': Permission denied

- `serena`: index failed: mkdir: cannot create directory '/out/serena-home': Permission denied

- `serena`: index failed: mkdir: cannot create directory '/out/serena-home': Permission denied

- `serena`: index failed: mkdir: cannot create directory '/out/serena-home': Permission denied

- `serena`: index failed: mkdir: cannot create directory '/out/serena-home': Permission denied

- `serena`: index failed: mkdir: cannot create directory '/out/serena-home': Permission denied

- `zvec_grep`: index failed: ular-p4-01.0.txt: Permission denied
/out/run.sh: 12: cannot create /out/angular-p4-02.0.txt: Permission denied
/out/run.sh: 13: cannot create /out/angular-T-1.0.txt: Permission denied
/out/run.sh: 14:
- `zvec_grep`: index failed: h: 29: cannot create /out/ex-T-1.0.txt: Permission denied
/out/run.sh: 30: cannot create /out/ex-T-2.0.txt: Permission denied
/out/run.sh: 31: cannot create /out/ex-T-3.0.txt: Permission denied
/out/r
- `zvec_grep`: index failed: out/fastapi-T-1.0.txt: Permission denied
/out/run.sh: 7: cannot create /out/fastapi-T-2.0.txt: Permission denied
/out/run.sh: 8: cannot create /out/fastapi-T-3.0.txt: Permission denied
/out/run.sh: 9:
- `zvec_grep`: index failed: : 6: cannot create /out/gin-T-1.0.txt: Permission denied
/out/run.sh: 7: cannot create /out/gin-T-2.0.txt: Permission denied
/out/run.sh: 8: cannot create /out/gin-T-3.0.txt: Permission denied
/out/ru
- `zvec_grep`: index failed: 8: cannot create /out/ld-p4-04.0.txt: Permission denied
/out/run.sh: 29: cannot create /out/ld-p4-05.0.txt: Permission denied
/out/run.sh: 30: cannot create /out/ld-T-1.0.txt: Permission denied
/out/r
- `zvec_grep`: index failed: create /out/nest-p4-01.0.txt: Permission denied
/out/run.sh: 12: cannot create /out/nest-p4-02.0.txt: Permission denied
/out/run.sh: 13: cannot create /out/nest-T-1.0.txt: Permission denied
/out/run.s
- `zvec_grep`: index failed: 9: cannot create /out/rq-p4-04.0.txt: Permission denied
/out/run.sh: 30: cannot create /out/rq-p4-05.0.txt: Permission denied
/out/run.sh: 31: cannot create /out/rq-T-1.0.txt: Permission denied
/out/r
- `zvec_grep`: index failed: out/run.sh: 12: cannot create /out/self-P1-validate_path.0.txt: Permission denied
/out/run.sh: 13: cannot create /out/self-P1-ProgressReporter.0.txt: Permission denied
/out/run.sh: 14: cannot create /
- `zvec_grep`: index failed: t/spring-p4-01.0.txt: Permission denied
/out/run.sh: 12: cannot create /out/spring-p4-02.0.txt: Permission denied
/out/run.sh: 13: cannot create /out/spring-T-1.0.txt: Permission denied
/out/run.sh: 1

## Movement

First recorded run: nothing to compare against yet (history.jsonl has no earlier line for this table).
