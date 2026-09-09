ARG RUNTIME_PLATFORM=linux/amd64

FROM --platform=$RUNTIME_PLATFORM node:22-alpine AS frontend-build

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM --platform=$RUNTIME_PLATFORM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Taipei \
    HF_HOME=/app/.cache/huggingface \
    PYTHONMALLOC=malloc \
    MALLOC_TRIM_THRESHOLD_=65536

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
COPY backend/wheels ./wheels

RUN python -m pip install --upgrade pip \
    && pip install ./wheels/*.whl \
    && pip install -r requirements.txt

COPY backend/ .
COPY --from=frontend-build /frontend/dist ./static
RUN TZ=Asia/Taipei date +'%Y%m%d-%H%M' > ./BUILD_VERSION

EXPOSE 8000

CMD ["sh", "-c", "if [ \"$RUNTIME_PROFILE\" = \"render-reader\" ]; then exec python -m main.render_reader; else exec python -m main.live_trader; fi"]
