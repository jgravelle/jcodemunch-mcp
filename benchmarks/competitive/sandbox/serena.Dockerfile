# Serena v1.7.0 (docs/competitive/fairness/serena.md), installed from PyPI
# with every dependency pinned by version AND hash in serena.requirements.txt
# (compiled once with `uv pip compile --generate-hashes`), together with
# pyright==1.1.403, the language-server version the tool itself pins. The
# pyright PyPI package fetches Node and the pyright npm package on first use:
# that happens HERE, at build, into /opt, and the run reads it. Network is
# used ONLY at build; the run is --network none.
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY serena.requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir --require-hashes --no-deps -r /opt/requirements.txt \
    && serena --help > /dev/null
# pyright's Node and npm package, pinned, fetched once at build (fairness note,
# "Language server"); world-readable so uid 65534 can run them.
ENV PYRIGHT_PYTHON_GLOBAL_NODE=0 \
    PYRIGHT_PYTHON_NODE_VERSION=22.18.0 \
    PYRIGHT_PYTHON_ENV_DIR=/opt/nodeenv \
    PYRIGHT_PYTHON_CACHE_DIR=/opt/pyright-cache \
    PYRIGHT_PYTHON_FORCE_VERSION=1.1.403
RUN mkdir -p /opt/nodeenv /opt/pyright-cache \
    && pyright --version \
    && chmod -R a+rX /opt/nodeenv /opt/pyright-cache
# Measured: while PYRIGHT_PYTHON_NODE_VERSION is set the wrapper re-runs its
# Node installer on EVERY start (`if path.exists() and not NODE_VERSION`),
# a network step that killed the language server under --network none. Unset
# for the run; the Node installed above is what runs.
ENV PYRIGHT_PYTHON_NODE_VERSION=
COPY mcp_driver.py /opt/mcp_driver.py
COPY serena_config.yml /opt/serena_config.yml
# The run mounts the corpus read-only at /corpus and one writable /out. The
# tool writes logs and language-server files under $SERENA_HOME and per-project
# data under project_serena_folder_location (both on /out); the shell that
# starts the driver copies the pinned config there first.
ENV HOME=/out SERENA_HOME=/out/serena-home
USER 65534:65534
WORKDIR /corpus
ENTRYPOINT ["/bin/sh"]
