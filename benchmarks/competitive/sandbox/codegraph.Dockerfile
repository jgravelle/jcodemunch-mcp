# CodeGraph v1.6.0 (docs/competitive/fairness/codegraph.md), installed from
# the GitHub release bundle for linux-x64 (a self-contained build: the tool's
# JavaScript, its Rust kernel as a Node addon, and a bundled Node runtime),
# verified against the published SHA256SUMS. The README's `install.sh` fetches
# the same bundle for the host's platform and links `bin/codegraph` onto PATH;
# this does the link by hand so no installer script runs. Python is in the
# image for sandbox/mcp_driver.py only. Network is used ONLY here, at build;
# the run is --network none.
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
ARG CG_VERSION=1.6.0
ARG CG_SHA256=de3391f79ed42622d937e6cd5b7642a7ea8bb7d1473607e80b879ba73ef216b0
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL -o /tmp/cg.tar.gz \
       "https://github.com/colbymchenry/codegraph/releases/download/v${CG_VERSION}/codegraph-linux-x64.tar.gz" \
    && echo "${CG_SHA256}  /tmp/cg.tar.gz" | sha256sum -c - \
    && mkdir -p /opt && tar -xzf /tmp/cg.tar.gz -C /opt \
    && mv /opt/codegraph-linux-x64 /opt/codegraph \
    && ln -s /opt/codegraph/bin/codegraph /usr/local/bin/codegraph \
    && rm -f /tmp/cg.tar.gz \
    && apt-get purge -y curl && apt-get autoremove -y \
    && codegraph version
COPY mcp_driver.py /opt/mcp_driver.py
# The run mounts the corpus read-only at /corpus, one writable /out and a
# uid-owned tmpfs at /private (sandbox.run private_home=True). The tool keeps
# its index INSIDE the project root (`.codegraph/`, a plain directory name by
# design), so the adapter copies the corpus to /private/project and indexes
# that copy (fairness note). DO_NOT_TRACK is the tool's documented off-switch
# for telemetry and the update check; CODEGRAPH_NO_DAEMON is its documented
# setting for sandboxed environments (one process serves one stdio client).
ENV HOME=/private DO_NOT_TRACK=1 CODEGRAPH_NO_DAEMON=1
USER 65534:65534
WORKDIR /corpus
ENTRYPOINT ["/bin/sh"]
