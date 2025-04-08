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
    ffmpeg curl \
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

# 환경 변수 설정 - 필수 기본값만 유지
ENV PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    FLASK_HOST=0.0.0.0 \
    FLASK_PORT=5000 \
    FLASK_DEBUG=false

# 포트 노출
EXPOSE 5000

# 1코어 서버에 최적화된 설정
# 환경 변수를 확장하기 위해 sh -c 사용
CMD sh -c 'gunicorn --bind 0.0.0.0:5000 \
           --forwarded-allow-ips='*' \
           --workers ${GUNICORN_WORKERS} \
           --threads ${GUNICORN_THREADS} \
           --timeout 300 \
           --max-requests 1000 \
           --max-requests-jitter 100 \
           --access-logfile /app/logs/access.log \
           --error-logfile /app/logs/error.log \
           app:app'
