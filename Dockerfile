FROM python:3.9-slim

WORKDIR /app

# ffmpeg 설치 (yt-dlp에 필요)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Python 패키지 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드 복사
COPY . .

# 다운로드/로그 디렉토리 생성
RUN mkdir -p downloads logs

# 환경 변수 설정
ENV PYTHONUNBUFFERED=1 \
    DOWNLOAD_FOLDER=/app/downloads \
    MAX_FILE_AGE=14 \
    MAX_FILE_SIZE=2147483648

EXPOSE 5000

# gunicorn으로 실행
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "2", "--timeout", "120", "app:app"]
