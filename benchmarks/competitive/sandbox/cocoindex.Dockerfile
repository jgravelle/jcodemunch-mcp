# CocoIndex Code 0.2.41 (docs/competitive/fairness/cocoindex.md), installed
# from PyPI with the documented [full] extra and every dependency pinned by
# version AND hash in cocoindex.requirements.txt (`uv pip compile
# --generate-hashes`; torch from PyTorch's CPU wheel index, the one
# deployment choice, stated in the note). Python 3.12 (the package declares
# >=3.11). Network is used ONLY here, at build: the pip install and the
# [full] default embedding model, downloaded once into HF_HOME so the run
# under --network none loads it from disk.
FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY cocoindex.requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir --require-hashes --no-deps --extra-index-url https://download.pytorch.org/whl/cpu -r /opt/requirements.txt \
    && ccc version
ENV HF_HOME=/opt/hf
RUN mkdir -p /opt/hf \
    && python -c "from sentence_transformers import SentenceTransformer as S; S('Snowflake/snowflake-arctic-embed-xs')" \
    && chmod -R a+rX /opt/hf
COPY mcp_driver.py /opt/mcp_driver.py
# The documented telemetry off-switch; the offline switches for the model
# libraries so a missing weight fails at build, never at run.
ENV COCOINDEX_DISABLE_USAGE_TRACKING=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
USER 65534:65534
WORKDIR /corpus
ENTRYPOINT ["/bin/sh"]
