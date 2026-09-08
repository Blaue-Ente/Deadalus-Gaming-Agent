FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE .env.example ./
COPY daedalus ./daedalus
COPY app ./app
COPY ui ./ui
COPY .agents ./.agents

RUN pip install --no-cache-dir -e .
# chromadb is a required dep. SmolVM is optional (pip install -e ".[computer]") and needs KVM.

ENV DAEDALUS_STANDALONE=1 \
    DAEDALUS_DATA=/var/lib/daedalus \
    DAEDALUS_PLAYGROUND=/var/lib/daedalus/playground \
    HOST=0.0.0.0 \
    PORT=8787 \
    PYTHONUNBUFFERED=1

RUN mkdir -p /var/lib/daedalus/chroma /var/lib/daedalus/playground

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8787}/health" || exit 1

CMD ["python", "-m", "app.cli", "serve"]
