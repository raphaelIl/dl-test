FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --user -r requirements.txt && \
    pip install --no-cache-dir --user --upgrade yt-dlp

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

RUN mkdir -p /app/downloads /app/logs /app/static && \
    touch /app/logs/error.log /app/logs/app.log && \
    chmod 777 /app/downloads /app/logs /app/logs/error.log /app/logs/app.log

COPY . .

# 번역 파일 컴파일
RUN pybabel compile -d translations

ENV PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    FLASK_HOST=0.0.0.0 \
    FLASK_PORT=5000 \
    FLASK_DEBUG=false

EXPOSE 5000

# 환경 변수를 확장하기 위해 sh -c 사용
CMD sh -c 'mkdir -p /app/logs && \
           touch /app/logs/error.log /app/logs/app.log && \
           chmod 777 /app/logs /app/logs/error.log /app/logs/app.log && \
           gunicorn --bind 0.0.0.0:5000 \
           --forwarded-allow-ips='*' \
           --workers ${GUNICORN_WORKERS} \
           --threads ${GUNICORN_THREADS} \
           --timeout 300 \
           --max-requests 1000 \
           --max-requests-jitter 100 \
           --error-logfile ${GUNICORN_ERROR_LOGFILE:-/app/logs/error.log} \
           --access-logfile ${GUNICORN_ACCESS_LOGFILE:-/dev/null} \
           --log-level ${GUNICORN_LOG_LEVEL:-error} \
           app:app'
