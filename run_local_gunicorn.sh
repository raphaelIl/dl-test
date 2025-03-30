#!/bin/bash

# 환경 변수 설정
export FLASK_ENV=development
export DOWNLOAD_FOLDER="downloads"
export MAX_WORKERS=3
export GUNICORN_WORKERS=1
export GUNICORN_THREADS=4
export MAX_FILE_AGE=7
export MAX_FILE_SIZE=1073741824
export ALLOWED_HEALTH_IPS="127.0.0.1,125.177.83.187,172.31.0.0/16"
export DOWNLOAD_LIMITS="300 per hour, 20 per minute"

# 필요한 디렉토리 생성
mkdir -p downloads logs

# gunicorn 실행
gunicorn --bind 127.0.0.1:5000 \
         --workers ${GUNICORN_WORKERS} \
         --threads ${GUNICORN_THREADS} \
         --timeout 300 \
         --access-logfile logs/access.log \
         --error-logfile logs/error.log \
         --reload \
         app:app
