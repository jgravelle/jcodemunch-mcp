# cymbal v0.14.0 (docs/competitive/fairness/cymbal.md), pinned by release
# checksum. Network is used ONLY here, at build; the run is --network none.
FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171
ARG CYMBAL_VERSION=0.14.0
ARG CYMBAL_SHA256=bfc951722b773b5f07c3a291530684ea737b012ad866505c6971a92d6bd9810d
# jq stays in the image: the adapter's script reads the top-3 result names with it.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl jq \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL -o /tmp/cymbal.tar.gz \
       "https://github.com/1broseidon/cymbal/releases/download/v${CYMBAL_VERSION}/cymbal_v${CYMBAL_VERSION}_linux_x86_64.tar.gz" \
    && echo "${CYMBAL_SHA256}  /tmp/cymbal.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/cymbal.tar.gz -C /tmp \
    && install -m 0755 "$(find /tmp -maxdepth 2 -type f -name cymbal | head -1)" /usr/local/bin/cymbal \
    && rm -rf /tmp/cymbal* \
    && apt-get purge -y curl && apt-get autoremove -y
# The run mounts the corpus read-only at /corpus and one writable /out; HOME
# points at /out so cymbal's index (~/.cache/cymbal) lands there.
ENV HOME=/out
USER 65534:65534
WORKDIR /corpus
ENTRYPOINT ["/bin/sh"]
