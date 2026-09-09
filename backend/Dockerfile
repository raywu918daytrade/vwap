FROM node:22-alpine AS frontend-build

WORKDIR /frontend

ARG VITE_APP_VERSION=local
ENV VITE_APP_VERSION=${VITE_APP_VERSION}

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Taipei \
    HF_HOME=/app/.cache/huggingface

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

EXPOSE 8000

CMD ["python", "-m", "main.live_trader"]
