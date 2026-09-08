# zvec-grep 0.2.2 (docs/competitive/fairness/zvec_grep.md), installed from npm
# the way its README installs it (`npm install -g @zvec/zvec-grep`), with
# every package pinned by version and integrity hash in
# zvec_grep.package-lock.json (248 packages, 76 non-optional, generated with
# `npm install --package-lock-only`; `npm ci` refuses anything that does not
# match it). Its native pieces are prebuilt binaries fetched by their own
# postinstall scripts (@vscode/ripgrep, onnxruntime-node); build tools cover
# the rest and are removed after. python3 is for the adapter's uncharged
# tools/list capture only. Network is used ONLY here, at build: the install
# and the embedding model (`local/potion-code-16m-v2`, HF
# minishlab/potion-code-16M-v2 at the revision the tool's own catalog pins),
# warmed into /opt/zg-models by indexing a one-file workspace, so the run
# under --network none loads it from disk or fails loudly.
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git python3 make g++ \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/zg
COPY zvec_grep.package.json /opt/zg/package.json
COPY zvec_grep.package-lock.json /opt/zg/package-lock.json
RUN npm ci --no-audit --no-fund \
    && ln -s /opt/zg/node_modules/.bin/zg /usr/local/bin/zg \
    && zg --version \
    && apt-get purge -y make g++ && apt-get autoremove -y
# The model warm step: one throwaway workspace indexed in direct mode with the
# pinned model, the download landing in the cache the run points at.
RUN mkdir -p /opt/zg-models /tmp/warm && printf 'def warm():\n    return 1\n' > /tmp/warm/warm.py \
    && ZVEC_GREP_HOME=/tmp/zg-home ZVEC_GREP_MODE=direct ZVEC_GREP_MODEL_CACHE=/opt/zg-models \
       zg index /tmp/warm --embedding local/potion-code-16m-v2 \
    && rm -rf /tmp/warm /tmp/zg-home \
    && chmod -R a+rX /opt/zg-models
# The run mounts the corpus read-only at /corpus, one writable /out and a
# uid-owned tmpfs at /private (sandbox.run private_home=True); the adapter
# copies the corpus there because the index lives inside the workspace. The
# tool's own home (ZVEC_GREP_HOME), mode (direct), embedding and model cache
# are passed by the adapter as documented environment variables.
ENV HOME=/private
USER 65534:65534
WORKDIR /corpus
ENTRYPOINT ["/bin/sh"]
