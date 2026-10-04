# Competitive tier — 2026-10-04T04:54:18Z at c3276130 (1.108.327)

A competitor's README figure is not on this page. Every number below was produced by this run on this corpus with this tokenizer (cl100k_base); `measured` is the median of the runs, `spread` is max minus min, `band` is max(5% of our median, 3x the larger spread); a delta is called meaningful only when both rows are inside the band and the gap exceeds it. ⚠ Runs in this file: 3.

Corpora: `self@c3276130` 286 files, sha256 `edc56a32239b`; `expressjs/express@1faf228` 234 files, sha256 `01d66b1a7167`; `fastapi/fastapi@a64dfbb` 2939 files, sha256 `d224adeb56fe`; `gin-gonic/gin@75ccf94` 120 files, sha256 `581f4ed0c5f6`; `lodash/lodash@f299b52` 91 files, sha256 `43d8e1a4a3df`; `psf/requests@0e322af` 119 files, sha256 `f0a00825e6d9`; `nestjs/nest@6494a6c` 2300 files, sha256 `0a5c9fff93a0`; `spring-projects/spring-framework@82a6b40` 11316 files, sha256 `712269bd145b`; `angular/angular@468b65b` 10328 files, sha256 `e26cd31e5520`

Sandbox: `docker` (every row in the D2 container: --network none, read-only rootfs, no capabilities, uid 65534, 8g, 512 pids); tree dirty: False; scorer sha256 `b92be8f0ed53`

Pins: `null_readall` none:read-all@baseline-A (ran as baseline-A); `null_grep` none:grep-top-3@baseline-B (ran as baseline-B); `jcodemunch` tree:jcodemunch-mcp@c3276130 (ran as c3276130, image `7d8754579765`, built in 15.2 s, 379.5 MiB, 0 prerequisites, fairness `ca0521a06434`); `jcodemunch_counter` tree:jcodemunch-mcp@c3276130 (ran as c3276130, image `991470e86cf8`, built in 12.2 s, 379.5 MiB, 0 prerequisites, fairness `ca0521a06434`); `cymbal` github-release:1broseidon/cymbal@0.14.0 (ran as 0.14.0, image `958fa62c802a`, built in 6.1 s, 125.4 MiB, 3 prerequisites (ca-certificates, curl, jq), fairness `a8fb42009099`); `codebase_memory` github-release:DeusData/codebase-memory-mcp@0.10.8 (ran as 0.10.8, image `f6e1b66d5c7d`, built in 8.1 s, 477.3 MiB, 4 prerequisites (ca-certificates, curl, git, jq), fairness `4f98331002dd`); `code_review_graph` pypi:code-review-graph@2.3.8 (ran as 2.3.8, image `076c8fa7fb2b`, built in 18.2 s, 495.8 MiB, 1 prerequisites (git), fairness `b9728719f7f1`); `serena` pypi:serena-agent@1.7.0 (ran as 1.7.0, image `78918d367481`, built in 23.3 s, 679.5 MiB, 1 prerequisites (git), fairness `33137a52deaa`); `codegraph` github-release:colbymchenry/codegraph@1.6.0 (ran as 1.6.0, image `0dcef788dfa1`, built in 8.7 s, 476.0 MiB, 3 prerequisites (ca-certificates, curl, git), fairness `9ff5e959dba7`); `graft` npm:@nanonets/graft@0.16.0 (ran as 0.16.0, image `cef50dfd2583`, built in 55.1 s, 1019.7 MiB, 5 prerequisites (ca-certificates, g++, git, make, python3), fairness `9c92ece21869`); `aider` pypi:aider-chat@0.86.2 (ran as 0.86.2, image `ec580dc0dc87`, built in 35.7 s, 814.7 MiB, 1 prerequisites (git), fairness `1d25e6277ad1`); `cocoindex` pypi:cocoindex-code@0.2.41 (ran as 0.2.41, image `4ed2bad1c12b`, built in 86.2 s, 1786.5 MiB, 1 prerequisites (git), fairness `f2b3009a9029`); `zvec_grep` npm:@zvec/zvec-grep@0.2.2 (ran as 0.2.2, image `9b0ca858db95`, built in 42.8 s, 2747.1 MiB, 5 prerequisites (ca-certificates, g++, git, make, python3), fairness `9787945593c7`)

## tokens_per_task (ratio vs jcm)

| tool | self@c3276130 | expressjs/express@1faf228 | fastapi/fastapi@a64dfbb | gin-gonic/gin@75ccf94 | lodash/lodash@f299b52 | psf/requests@0e322af | nestjs/nest@6494a6c | spring-projects/spring-framework@82a6b40 | angular/angular@468b65b |
|---|---|---|---|---|---|---|---|---|---|
| null_readall | 1.294e+06 spread 0 [NOT COMPARABLE] | 2.06e+05 spread 0 [NOT COMPARABLE] | 9.079e+06 spread 0 [NOT COMPARABLE] | 2.134e+05 spread 0 [NOT COMPARABLE] | 1.239e+06 spread 0 [NOT COMPARABLE] | 2.397e+06 spread 0 [NOT COMPARABLE] | 1.412e+06 spread 0 [NOT COMPARABLE] | 1.334e+07 spread 0 [NOT COMPARABLE] | 1.578e+07 spread 0 [NOT COMPARABLE] |
| null_grep | 1.311e+05 spread 0 [NOT COMPARABLE] | 2.354e+04 spread 0 [NOT COMPARABLE] | 1.689e+05 spread 0 [NOT COMPARABLE] | 3.575e+04 spread 0 [NOT COMPARABLE] | 3.924e+05 spread 0 [NOT COMPARABLE] | 3.17e+04 spread 0 [NOT COMPARABLE] | 4.249e+04 spread 0 [NOT COMPARABLE] | 5.855e+04 spread 0 [NOT COMPARABLE] | 2.227e+05 spread 0 [NOT COMPARABLE] |
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

| tool | self@c3276130 | expressjs/express@1faf228 | fastapi/fastapi@a64dfbb | gin-gonic/gin@75ccf94 | lodash/lodash@f299b52 | psf/requests@0e322af | nestjs/nest@6494a6c | spring-projects/spring-framework@82a6b40 | angular/angular@468b65b |
|---|---|---|---|---|---|---|---|---|---|
| null_readall | 286 spread 0 [NOT COMPARABLE] | 234 spread 0 [NOT COMPARABLE] | 2939 spread 0 [NOT COMPARABLE] | 120 spread 0 [NOT COMPARABLE] | 91 spread 0 [NOT COMPARABLE] | 119 spread 0 [NOT COMPARABLE] | 2300 spread 0 [NOT COMPARABLE] | 1.132e+04 spread 0 [NOT COMPARABLE] | 1.033e+04 spread 0 [NOT COMPARABLE] |
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

| tool | self@c3276130 | expressjs/express@1faf228 | fastapi/fastapi@a64dfbb | gin-gonic/gin@75ccf94 | lodash/lodash@f299b52 | psf/requests@0e322af | nestjs/nest@6494a6c | spring-projects/spring-framework@82a6b40 | angular/angular@468b65b |
|---|---|---|---|---|---|---|---|---|---|
| null_readall | 12.88 spread 0.38 [NOT COMPARABLE] | 7.91 spread 0.43 [NOT COMPARABLE] | 131.9 spread 5.44 [NOT COMPARABLE] | 4.38 spread 0.3 [NOT COMPARABLE] | 4.87 spread 0.17 [NOT COMPARABLE] | 31.45 spread 0.44 [NOT COMPARABLE] | 75.69 spread 0.39 [NOT COMPARABLE] | 423.4 spread 5.63 [NOT COMPARABLE] | 390.6 spread 2.63 [NOT COMPARABLE] |
| null_grep | 0.28 spread 0.06 [NOT COMPARABLE] | 0.06 spread 0 [NOT COMPARABLE] | 0.3 spread 0.01 [NOT COMPARABLE] | 0.08 spread 0.01 [NOT COMPARABLE] | 0.25 spread 0.01 [NOT COMPARABLE] | 0.09 spread 0.01 [NOT COMPARABLE] | 0.04 spread 0.01 [NOT COMPARABLE] | 0.08 spread 0 [NOT COMPARABLE] | 0.23 spread 0.03 [NOT COMPARABLE] |
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

| tool | self@c3276130 | expressjs/express@1faf228 | fastapi/fastapi@a64dfbb | gin-gonic/gin@75ccf94 | lodash/lodash@f299b52 | psf/requests@0e322af | nestjs/nest@6494a6c | spring-projects/spring-framework@82a6b40 | angular/angular@468b65b |
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

| tool | self@c3276130 | expressjs/express@1faf228 | fastapi/fastapi@a64dfbb | gin-gonic/gin@75ccf94 | lodash/lodash@f299b52 | psf/requests@0e322af | nestjs/nest@6494a6c | spring-projects/spring-framework@82a6b40 | angular/angular@468b65b |
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

| tool | self@c3276130 | expressjs/express@1faf228 | fastapi/fastapi@a64dfbb | gin-gonic/gin@75ccf94 | lodash/lodash@f299b52 | psf/requests@0e322af | nestjs/nest@6494a6c | spring-projects/spring-framework@82a6b40 | angular/angular@468b65b |
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
- `jcodemunch`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch_counter`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch_counter`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch_counter`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch_counter`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch_counter`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch_counter`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch_counter`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch_counter`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
- `jcodemunch_counter`: index failed: Deprecated: Using JCODEMUNCH_TRUSTED_FOLDERS environment variable. This will be removed in v2.0. Use config.jsonc instead.
Traceback (most recent call last):
  File "/opt/jcm_worker.py", line 161, in 
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
