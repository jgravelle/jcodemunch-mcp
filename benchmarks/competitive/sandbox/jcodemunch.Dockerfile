# jCodeMunch from the checkout under test (docs/competitive/DESIGN.md s1.4,
# FINDINGS CF-3): the same container shape as every competitor. The build
# context is what a commit of the working tree would contain (run.py copies
# `git ls-files --cached --others --exclude-standard`); a dirty tree is
# stamped in the result header. Dependencies come from uv.lock, exported by
# the adapter into requirements.txt, so a rebuild at the same commit is the
# same dependency set (DESIGN s9.3). Two stages: the tree is a layer of the
# BUILD stage only; the final image holds the wheel, its pinned
# dependencies and the worker (review round 1, findings 7 and 11).
# Network is used ONLY at build (pip); the run is --network none.
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e AS build
COPY . /src
RUN pip wheel --no-cache-dir --disable-pip-version-check --no-deps -w /wheels /src

FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
COPY --from=build /wheels /wheels
COPY --from=build /src/requirements.txt /opt/requirements.txt
COPY --from=build /src/benchmarks/competitive/sandbox/jcm_worker.py /opt/jcm_worker.py
RUN pip install --no-cache-dir --disable-pip-version-check -r /opt/requirements.txt \
    && pip install --no-cache-dir --disable-pip-version-check --no-deps /wheels/*.whl \
    && rm -rf /wheels /root/.cache
# The run mounts the corpus read-only at /corpus and one writable /out;
# CODE_INDEX_PATH under /out, the live journal off, no config file.
ENV HOME=/out CODE_INDEX_PATH=/out/jcm-store JCODEMUNCH_LIVE_JOURNAL=0 JCODEMUNCH_TRUSTED_FOLDERS=/corpus
USER 65534:65534
WORKDIR /corpus
ENTRYPOINT ["python", "/opt/jcm_worker.py"]
