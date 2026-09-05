# Graft 0.16.0 (docs/competitive/fairness/graft.md), installed from npm the
# way its README installs it (`npm install -g @nanonets/graft`), with every
# package pinned by version and integrity hash in graft.package-lock.json
# (46 packages, generated with `npm install --package-lock-only`; `npm ci`
# refuses anything that does not match it). The tree-sitter grammars it
# depends on are native addons: build tools are present for the install and
# removed after. python3 is for sandbox/mcp_driver.py only. Network is used
# ONLY here, at build; the run is --network none. The package's postinstall
# records an install event unless CI is set (TELEMETRY.md); CI=1 here.
FROM node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git python3 make g++ \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/graft
COPY graft.package.json /opt/graft/package.json
COPY graft.package-lock.json /opt/graft/package-lock.json
RUN CI=1 DO_NOT_TRACK=1 npm ci --no-audit --no-fund \
    && ln -s /opt/graft/node_modules/.bin/graft /usr/local/bin/graft \
    && graft --version \
    && apt-get purge -y make g++ && apt-get autoremove -y
COPY mcp_driver.py /opt/mcp_driver.py
# The run mounts the corpus read-only at /corpus, one writable /out and a
# uid-owned tmpfs at /private (sandbox.run private_home=True). The graph is
# written to a context dir outside the repo through the documented global
# `--dir` option (default <repo>/graft); `build --no-gitignore --no-ignore`
# are its documented switches for the two files it would otherwise write into
# the repo. DO_NOT_TRACK and CI are the documented telemetry off-switches; the
# daily npm version check has no network to reach.
ENV HOME=/private DO_NOT_TRACK=1 CI=1
USER 65534:65534
WORKDIR /corpus
ENTRYPOINT ["/bin/sh"]
