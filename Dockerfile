# Backend image: serves agent recommendations over HTTP from models trained at build time.
#
#   docker build -t solar-ai-backend .
#   docker run --rm -p 8000:8000 solar-ai-backend          # API on http://localhost:8000 (docs at /docs)
#
# The image holds the code, the small committed datasets and models trained on
# the seasonal sample. It contains no API key and not the full 2023-2025
# dataset; mount your own saved models or pass a key at run time.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# XGBoost needs the OpenMP runtime, which the slim base image leaves out.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so code changes do not reinstall them. The extra index supplies
# the CPU build of PyTorch; the default Linux wheel bundles GPU libraries this never uses.
COPY requirements.txt .
RUN pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

COPY src ./src
COPY scripts ./scripts
COPY data/mock ./data/mock
COPY data/example ./data/example
COPY data/generated/gem_seasonal_sample ./data/generated/gem_seasonal_sample

# Train once on the committed sample so the server starts with models and never trains at request time.
RUN python -m scripts.train_models

# Run as an unprivileged user. /out is for one-off runs that write a file.
RUN useradd --create-home --uid 10001 solar \
    && mkdir -p /out \
    && chown solar /out
USER solar
VOLUME ["/out"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=4)"

CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
