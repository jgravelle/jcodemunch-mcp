set +e
ms() { s=$(date +%s%N); "$@"; r=$?; e=$(date +%s%N); echo "$LABEL rc=$r ms=$(( (e-s)/1000000 ))" >> /out/timings.txt; return $r; }
cp -r /corpus /private/project && cd /private/project || exit 1
LABEL=index; ms zg index . --embedding local/potion-code-16m-v2 > /out/index.txt 2>&1
zg status . > /out/status.txt 2>&1
LABEL=self-T-router.0; ms zg query 'router route handler' > /out/self-T-router.0.txt 2>/out/self-T-router.0.err
LABEL=self-T-middleware.0; ms zg query middleware > /out/self-T-middleware.0.txt 2>/out/self-T-middleware.0.err
LABEL=self-T-error.0; ms zg query 'error exception' > /out/self-T-error.0.txt 2>/out/self-T-error.0.err
LABEL=self-T-request.0; ms zg query 'request response' > /out/self-T-request.0.txt 2>/out/self-T-request.0.err
LABEL=self-T-context.0; ms zg query 'context bind' > /out/self-T-context.0.txt 2>/out/self-T-context.0.err
LABEL=self-P1-cache_put.0; ms zg query --prefer-symbol cache_put > /out/self-P1-cache_put.0.txt 2>/out/self-P1-cache_put.0.err
LABEL=self-P1-validate_path.0; ms zg query --prefer-symbol validate_path > /out/self-P1-validate_path.0.txt 2>/out/self-P1-validate_path.0.err
LABEL=self-P1-ProgressReporter.0; ms zg query --prefer-symbol ProgressReporter > /out/self-P1-ProgressReporter.0.txt 2>/out/self-P1-ProgressReporter.0.err
LABEL=self-P2-cache_put.0; ms zg query --rg -w cache_put > /out/self-P2-cache_put.0.txt 2>/out/self-P2-cache_put.0.err
LABEL=self-P4-token_tracker.0; ms zg query --rg -F token_tracker > /out/self-P4-token_tracker.0.txt 2>/out/self-P4-token_tracker.0.err
python3 /out/tools_list.py > /out/tools_list.log 2>&1
