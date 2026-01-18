FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    curl unzip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

# https://github.com/yt-dlp/yt-dlp/issues/14404
RUN pip install --no-cache-dir --user -r requirements.txt && \
    pip install --no-cache-dir --user --upgrade "yt-dlp[default]"

# deno 설치 (yt-dlp JavaScript 런타임)
RUN curl -fsSL https://deno.land/install.sh | sh

FROM python:3.11-slim

# 런타임 의존성: ffmpeg (yt-dlp 비디오+오디오 병합에 필요)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# builder에서 빌드된 결과물만 복사
# - /root/.local: Python 패키지 (yt-dlp, flask 등)
# - /root/.deno: deno 바이너리 (yt-dlp JS 런타임)
COPY --from=builder /root/.local /root/.local
COPY --from=builder /root/.deno /root/.deno
ENV PATH="/root/.deno/bin:/root/.local/bin:${PATH}"

RUN mkdir -p /app/downloads /app/logs && \
    touch /app/logs/error.log /app/logs/app.log && \
    chmod 777 /app/downloads /app/logs /app/logs/error.log /app/logs/app.log

COPY . .

## 번역 파일 컴파일
#RUN pybabel compile -d translations

ENV PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    FLASK_HOST=0.0.0.0 \
    FLASK_PORT=5000 \
    FLASK_DEBUG=false

EXPOSE 5000

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:5000 \
    --forwarded-allow-ips='*' \
    --workers ${GUNICORN_WORKERS:-2} \
    --threads ${GUNICORN_THREADS:-4} \
    --timeout 300 \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --error-logfile ${GUNICORN_ERROR_LOGFILE:-/app/logs/error.log} \
    --access-logfile ${GUNICORN_ACCESS_LOGFILE:-/dev/null} \
    --log-level ${GUNICORN_LOG_LEVEL:-error} \
    app:app"]
