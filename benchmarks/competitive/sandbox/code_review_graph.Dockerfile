# code-review-graph v2.3.8 (docs/competitive/fairness/code_review_graph.md),
# installed from PyPI with every dependency pinned by version AND hash in
# code_review_graph.requirements.txt (compiled once with
# `uv pip compile --generate-hashes`); the base install only, no optional
# extra (no embeddings model, no igraph, no jedi): the README's default.
# Network is used ONLY here, at build; the run is --network none.
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY code_review_graph.requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir --require-hashes --no-deps -r /opt/requirements.txt \
    && code-review-graph --version
COPY mcp_driver.py /opt/mcp_driver.py
# The run mounts the corpus read-only at /corpus and one writable /out; the
# tool's default data dir is INSIDE the repo, so its documented CRG_DATA_DIR
# and CRG_HOME knobs point it at /out (fairness note, "Data dir").
ENV HOME=/out CRG_DATA_DIR=/out/crg-data CRG_HOME=/out/crg-home
USER 65534:65534
WORKDIR /corpus
ENTRYPOINT ["/bin/sh"]
