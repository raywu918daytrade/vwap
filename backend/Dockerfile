FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Taipei \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
COPY wheels ./wheels

RUN python -m pip install --upgrade pip \
    && pip install ./wheels/*.whl \
    && pip install -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["python", "-m", "main.live_trader"]
