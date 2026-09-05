# jCodeMunch from the checkout under test (docs/competitive/DESIGN.md s1.4,
# FINDINGS CF-3): the same container shape as every competitor. The build
# context is `git archive HEAD` of the working tree (run.py exports it), so
# the image is the committed tree; a dirty tree is stamped in the result.
# Network is used ONLY here, at build (pip); the run is --network none.
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
COPY . /src
RUN pip install --no-cache-dir --disable-pip-version-check /src \
    && rm -rf /root/.cache \
    && cp /src/benchmarks/competitive/sandbox/jcm_worker.py /opt/jcm_worker.py \
    && rm -rf /src
# The run mounts the corpus read-only at /corpus and one writable /out;
# CODE_INDEX_PATH under /out, the live journal off, no config file.
ENV HOME=/out CODE_INDEX_PATH=/out/jcm-store JCODEMUNCH_LIVE_JOURNAL=0 JCODEMUNCH_TRUSTED_FOLDERS=/corpus
USER 65534:65534
WORKDIR /corpus
ENTRYPOINT ["python", "/opt/jcm_worker.py"]
