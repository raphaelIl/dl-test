# 베이스 이미지 선택
FROM python:3.10-slim AS builder

# 필요한 패키지만 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 작업 디렉토리 설정
WORKDIR /app

# 의존성 파일 먼저 복사하여 캐싱 활용
COPY requirements.txt .

# 의존성 설치
RUN pip install --no-cache-dir --user -r requirements.txt

# 최종 이미지
FROM python:3.10-slim

# 필요한 런타임 패키지만 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 작업 디렉토리 설정
WORKDIR /app

# builder 단계에서 설치한 파이썬 패키지 복사
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# 필요한 디렉토리 생성
RUN mkdir -p /app/downloads /app/logs /app/static && \
    chmod 777 /app/downloads /app/logs

# 소스 코드 복사
COPY . .

# 번역 파일 컴파일
RUN pybabel compile -d translations

# 환경 변수 설정
ENV FLASK_ENV=production \
    FLASK_HOST=0.0.0.0 \
    FLASK_PORT=5000 \
    FLASK_DEBUG=false \
    PYTHONUNBUFFERED=1 \
    DOWNLOAD_FOLDER="/app/downloads" \
    MAX_WORKERS=4 \
    MAX_FILE_AGE=14 \
    MAX_FILE_SIZE=1073741824 \
    ALLOWED_HEALTH_IPS="127.0.0.1,172.31.0.0/16,10.0.0.0/8" \
    DOWNLOAD_LIMITS="300 per hour, 20 per minute"

# 포트 노출
EXPOSE 5000

# 실행 명령어
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--threads", "2", "--timeout", "300", "--access-logfile", "/app/logs/access.log", "--error-logfile", "/app/logs/error.log", "app:app"]
