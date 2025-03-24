FROM python:3.10-slim

WORKDIR /app

# 의존성 설치를 위한 requirements.txt 복사 및 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 코드 복사
COPY . .

# 다국어 처리를 위한 번역 파일 컴파일
RUN pybabel compile -d translations

# 환경 변수 설정
ENV FLASK_HOST=0.0.0.0
ENV FLASK_PORT=5000
ENV PYTHONUNBUFFERED=1

# 다운로드 폴더 생성 및 권한 설정
RUN mkdir -p /app/downloads && chmod 777 /app/downloads

# 포트 노출
EXPOSE 5000

# Flask 앱 실행 (gunicorn 대신 flask로 실행)
CMD ["python", "app.py"]
