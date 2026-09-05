# Aider RepoMap: aider-chat 0.86.2 (docs/competitive/fairness/aider.md),
# installed from PyPI with every dependency pinned by version AND hash in
# aider.requirements.txt (108 packages, `uv pip compile --generate-hashes`).
# Python 3.12: the package declares <3.13 and the docs state 3.9-3.12.
# Network is used ONLY here, at build: the pip install and the model's
# tiktoken encodings, cached into the image so token counting under the
# run's --network none fetches nothing.
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY aider.requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir --require-hashes --no-deps -r /opt/requirements.txt \
    && aider --version
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken LITELLM_LOCAL_MODEL_COST_MAP=True
RUN mkdir -p /opt/tiktoken \
    && python -c "import tiktoken; tiktoken.get_encoding('o200k_base'); tiktoken.get_encoding('cl100k_base')" \
    && chmod -R a+rX /opt/tiktoken
# The documented off-switches as their documented variables (fairness note,
# "Environment"); the run's HOME is the uid-owned tmpfs.
ENV AIDER_ANALYTICS=false AIDER_CHECK_UPDATE=false AIDER_GITIGNORE=false
USER 65534:65534
WORKDIR /corpus
ENTRYPOINT ["/bin/sh"]
