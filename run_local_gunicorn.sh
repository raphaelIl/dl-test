#!/bin/bash

# 환경 변수 설정
export FLASK_ENV=development
export DOWNLOAD_FOLDER="downloads"
export MAX_WORKERS=4
export MAX_FILE_AGE=14
export MAX_FILE_SIZE=1073741824
export ALLOWED_HEALTH_IPS="127.0.0.1"
export DOWNLOAD_LIMITS="300 per hour, 20 per minute"

# 필요한 디렉토리 생성
mkdir -p downloads logs

# gunicorn 실행
gunicorn --bind 127.0.0.1:5000 \
         --workers 2 \
         --threads 2 \
         --timeout 300 \
         --access-logfile logs/access.log \
         --error-logfile logs/error.log \
         --reload \
         app:app
