# codebase-memory-mcp v0.10.8 (docs/competitive/fairness/codebase_memory.md),
# installed from the GitHub release PORTABLE archive (the non-portable build
# needs glibc 2.38, newer than the pinned bookworm base) verified against the published
# checksums.txt, NOT from the 14 KB PyPI launcher (which fetches the native
# runtime on first run: a network step this sandbox forbids after build).
# Python is in the image for sandbox/mcp_driver.py only; the tool is a
# static native binary. Network is used ONLY here, at build; the run is
# --network none.
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
ARG CBM_VERSION=0.10.8
ARG CBM_SHA256=6eef49652bc0c7820f43114125044d40bf7f4d97c11b2592f6b0f6a307702325
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl jq git \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL -o /tmp/cbm.tar.gz \
       "https://github.com/DeusData/codebase-memory-mcp/releases/download/v${CBM_VERSION}/codebase-memory-mcp-linux-amd64-portable.tar.gz" \
    && echo "${CBM_SHA256}  /tmp/cbm.tar.gz" | sha256sum -c - \
    && mkdir -p /tmp/cbm && tar -xzf /tmp/cbm.tar.gz -C /tmp/cbm \
    && ls -la /tmp/cbm \
    && install -m 0755 "$(find /tmp/cbm -type f -name 'codebase-memory-mcp*' ! -name '*.sh' ! -name '*.ps1' ! -name '*.md' | head -1)" /usr/local/bin/codebase-memory-mcp \
    && rm -rf /tmp/cbm /tmp/cbm.tar.gz \
    && apt-get purge -y curl && apt-get autoremove -y
COPY mcp_driver.py /opt/mcp_driver.py
# The run mounts the corpus read-only at /corpus, one writable /out and a
# uid-owned 0700 tmpfs at /private (sandbox.run private_home=True): the tool
# refuses a cache whose parent it does not own, which the /out bind mount is. HOME
# points at /out so its cache (~/.cache/codebase-memory-mcp) lands there.
ENV HOME=/private CBM_CACHE_DIR=/private/cbm-cache
USER 65534:65534
WORKDIR /corpus
ENTRYPOINT ["/bin/sh"]
