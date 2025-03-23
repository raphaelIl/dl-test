FROM python:3.9-slim

WORKDIR /app

# 필요한 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ffmpeg 설치 (동영상 변환에 필요)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 애플리케이션 코드 복사
COPY . .

# 다운로드 폴더 생성
RUN mkdir -p downloads

# 환경 변수 설정
ENV DOWNLOAD_FOLDER=/app/downloads
ENV MAX_FILE_AGE=14
ENV MAX_FILE_SIZE=2147483648

# 컨테이너 실행 시 실행할 명령
CMD ["python", "app.py"]

# 포트 노출
EXPOSE 5000
