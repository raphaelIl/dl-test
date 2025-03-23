# TODO(2025.03.23.Sun): celery를 사용하여 비동기로 다운로드 제공할 수 있게 해야할수도
from flask import Flask, render_template, request, send_file, url_for, redirect, Response
import yt_dlp
import os
import uuid
import re
import threading
import time
import logging
from datetime import datetime
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from werkzeug.utils import secure_filename
from urllib.parse import quote
from slugify import slugify  # pip install python-slugify가 필요할 수 있음

# 환경 변수 로드
load_dotenv()

app = Flask(__name__)

# 다운로드 파일 저장 폴더 및 설정
DOWNLOAD_FOLDER = os.getenv('DOWNLOAD_FOLDER', 'downloads')
MAX_FILE_AGE = int(os.getenv('MAX_FILE_AGE', 14))  # 일 단위
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', 2 * 1024 * 1024 * 1024))  # 기본 2GB

# 다운로드 상태를 저장할 딕셔너리
download_status = {}

# 로깅 설정
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# 요청 제한 설정
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)
limiter.init_app(app)

if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# 스레드 풀 초기화
executor = ThreadPoolExecutor(max_workers=3)

# URL 정보 캐싱 (재요청 시 빠른 응답)
@lru_cache(maxsize=100)
def get_video_info(url):
    with yt_dlp.YoutubeDL({'quiet': True, 'simulate': True}) as ydl:
        return ydl.extract_info(url, download=False)

# 비동기 다운로드 함수
def download_video(video_url, file_id, download_path):
    try:
        download_status[file_id] = {'status': 'downloading', 'progress': 0}

        def progress_hook(d):
            if d['status'] == 'downloading':
                if 'total_bytes' in d and d['total_bytes'] > 0:
                    progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
                elif 'total_bytes_estimate' in d and d['total_bytes_estimate'] > 0:
                    progress = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
                else:
                    progress = 0
                download_status[file_id] = {'status': 'downloading', 'progress': progress}
            elif d['status'] == 'finished':
                download_status[file_id] = {'status': 'processing', 'progress': 100}

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',  # 출력 형식을 mp4로 강제 지정
            'outtmpl': download_path + '/%(title)s.%(ext)s',
            'noplaylist': True,
            'retries': 3,
            'fragment_retries': 3,
            'socket_timeout': 15,
            'max_filesize': MAX_FILE_SIZE,
            'noprogress': True,  # 프로그레스바 비활성화로 메모리 절약
            'buffersize': 1024,  # 버퍼 크기 조정
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            },
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'progress_hooks': [progress_hook],
        }

        # 비디오 정보 추출
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            download_status[file_id] = {
                'status': 'completed',
                'title': info.get('title', '알 수 없는 제목'),
                'url': video_url
            }
            logging.info(f"다운로드 성공: {info.get('title')} ({video_url})")
            return info
    except Exception as e:
        download_status[file_id] = {'status': 'error', 'error': str(e)}
        logging.error(f"다운로드 오류 (URL: {video_url}): {str(e)}")
        return None
    finally:
        # 명시적 리소스 정리
        import gc
        gc.collect()

@app.route('/', methods=['GET', 'POST'])
# @limiter.limit("5 per minute")
def index():
    if request.method == 'POST':
        video_url = request.form['video_url']

        if not video_url:
            return render_template('index.html', error='URL을 입력해주세요.')

        try:
            # 고유한 파일 이름 생성
            file_id = str(uuid.uuid4())
            download_path = os.path.join(DOWNLOAD_FOLDER, file_id)

            # 폴더 생성
            if not os.path.exists(download_path):
                os.makedirs(download_path)

            # 별도 스레드에서 다운로드 시작
            executor.submit(download_video, video_url, file_id, download_path)

            # 다운로드 대기 페이지로 리다이렉트
            return redirect(url_for('download_waiting', file_id=file_id))

        except Exception as e:
            logging.error(f"예상치 못한 오류 (URL: {video_url}): {str(e)}", exc_info=True)
            return render_template('index.html', error=f'다운로드 중 오류가 발생했습니다: {str(e)}')

    return render_template('index.html', max_file_size_gb=MAX_FILE_SIZE/(1024*1024*1024))

@app.route('/download-waiting/<file_id>')
def download_waiting(file_id):
    # file_id의 유효성 검증
    if not re.match(r'^[0-9a-f\-]+$', file_id):
        logging.warning(f"유효하지 않은 file_id 접근 시도: {file_id}")
        return redirect(url_for('index'))

    status = download_status.get(file_id, {'status': 'unknown'})

    if status['status'] == 'completed':
        return redirect(url_for('download_result', file_id=file_id, title=status.get('title', '다운로드 완료'), url=status.get('url', '')))

    return render_template('download_waiting.html', file_id=file_id, status=status)

@app.route('/check-status/<file_id>')
def check_status(file_id):
    if not re.match(r'^[0-9a-f\-]+$', file_id):
        logging.warning(f"유효하지 않은 file_id 상태 확인 시도: {file_id}")
        return {'status': 'error', 'error': '유효하지 않은 파일 ID'}

    status = download_status.get(file_id, {'status': 'unknown'})
    logging.info(f"상태 확인 요청: {file_id}, 현재 상태: {status}")
    return status

@app.route('/download-result')
def download_result():
    file_id = request.args.get('file_id')
    title = request.args.get('title')
    url = request.args.get('url', '')

    if not file_id:
        return redirect(url_for('index'))

    # file_id의 유효성 검증
    if not re.match(r'^[0-9a-f\-]+$', file_id):
        logging.warning(f"유효하지 않은 file_id 접근 시도: {file_id}")
        return redirect(url_for('index'))

    download_path = os.path.join(DOWNLOAD_FOLDER, file_id)

    # 파일 존재 확인 및 파일 경로 가져오기
    if not os.path.exists(download_path):
        logging.error(f"다운로드 경로를 찾을 수 없음: {download_path}")
        return render_template('index.html', error="다운로드 파일을 찾을 수 없습니다.")

    # 폴더 내 모든 파일 확인
    files = os.listdir(download_path)
    logging.info(f"다운로드 폴더({file_id})의 파일 목록: {files}")

    # 파일이 없는 경우
    if not files:
        logging.error(f"다운로드 폴더에 파일이 없음: {download_path}")
        return render_template('index.html', error="다운로드된 파일이 없습니다.")

    # 실제 파일 정보 확인
    file_name = files[0]
    file_path = os.path.join(download_path, file_name)
    file_size = os.path.getsize(file_path) if os.path.isfile(file_path) else 0

    # 파일 크기를 읽기 쉬운 형태로 변환
    def readable_size(size_bytes):
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    file_size_readable = readable_size(file_size)

    # 다운로드 성공 페이지 렌더링
    return render_template('download_result.html',
                           title=title,
                           file_id=file_id,
                           url=url,
                           file_name=file_name,
                           file_size=file_size_readable)

def slugify(text):
    """유니코드 텍스트를 URL 및 파일명에 안전한 ASCII 문자열로 변환"""
    import re
    import unicodedata

    # 유니코드 정규화
    text = unicodedata.normalize('NFKD', text)
    # ASCII로 변환 가능한 문자만 남기고 나머지는 제거
    text = re.sub(r'[^\w\s-]', '', text).strip().lower()
    # 공백을 하이픈으로 변환
    text = re.sub(r'[-\s]+', '-', text)

    # 비어있으면 기본값 제공
    if not text:
        text = 'download'

    return text

@app.route('/download-file/<file_id>')
def download_file(file_id):
    try:
        logging.info(f"파일 다운로드 시작: {file_id}")

        # file_id 유효성 검증
        if not re.match(r'^[0-9a-f\-]+$', file_id):
            logging.warning(f"유효하지 않은 file_id 다운로드 시도: {file_id}")
            return render_template('index.html', error="유효하지 않은 파일 ID입니다.")

        download_path = os.path.join(DOWNLOAD_FOLDER, file_id)

        if not os.path.exists(download_path):
            logging.error(f"다운로드 경로를 찾을 수 없음: {download_path}")
            return render_template('index.html', error="다운로드 파일을 찾을 수 없습니다.")

        files = os.listdir(download_path)
        if not files:
            logging.error(f"다운로드 폴더에 파일이 없음: {download_path}")
            return render_template('index.html', error="다운로드된 파일이 없습니다.")

        filename = files[0]
        file_path = os.path.join(download_path, filename)

        if not os.path.isfile(file_path):
            logging.error(f"파일이 아닌 경로: {file_path}")
            return render_template('index.html', error="유효하지 않은 파일입니다.")

        logging.info(f"파일 다운로드 제공: {file_id} - {filename}")

        # 안전한 ASCII 파일명 생성
        safe_filename = f"download-{file_id}.mp4"

        # RFC 6266에 따라 파일명 인코딩
        response = send_file(
            file_path,
            as_attachment=True,
            mimetype='video/mp4'
        )

        # UTF-8로 인코딩된 파일명을 포함한 Content-Disposition 헤더 설정
        encoded_filename = quote(filename)
        response.headers["Content-Disposition"] = f"attachment; filename=\"{safe_filename}\"; filename*=UTF-8''{encoded_filename}"

        return response
    except Exception as e:
        logging.error(f"파일 다운로드 중 오류: {str(e)}", exc_info=True)
        return render_template('index.html', error=f"파일 다운로드 중 오류가 발생했습니다: {str(e)}")

# 오래된 파일 정리 함수
def clean_old_files():
    try:
        now = datetime.now()
        cleaned_count = 0

        for folder_name in os.listdir(DOWNLOAD_FOLDER):
            folder_path = os.path.join(DOWNLOAD_FOLDER, folder_name)
            if os.path.isdir(folder_path):
                folder_creation_time = datetime.fromtimestamp(os.path.getctime(folder_path))
                days_old = (now - folder_creation_time).days

                if days_old > MAX_FILE_AGE:
                    # 폴더 내 모든 파일 삭제
                    for file_name in os.listdir(folder_path):
                        file_path = os.path.join(folder_path, file_name)
                        if os.path.isfile(file_path):
                            os.remove(file_path)

                    # 폴더 삭제
                    os.rmdir(folder_path)
                    cleaned_count += 1

        logging.info(f"파일 정리 완료: {cleaned_count}개 폴더 삭제됨")
    except Exception as e:
        logging.error(f"파일 정리 실행 중 오류 발생: {str(e)}", exc_info=True)

# 주기적인 파일 정리 함수
def schedule_cleaning():
    while True:
        try:
            clean_old_files()
            # 24시간마다 실행
            time.sleep(86400)
        except Exception as e:
            logging.error(f"예약된 파일 정리 중 오류: {str(e)}")
            time.sleep(3600)  # 오류 발생 시 1시간 후 재시도

# 오래된 상태 정보 정리
def clean_status_dict():
    while True:
        try:
            now = datetime.now()
            for file_id in list(download_status.keys()):
                status_age = (now - datetime.fromtimestamp(os.path.getctime(os.path.join(DOWNLOAD_FOLDER, file_id)))).days if os.path.exists(os.path.join(DOWNLOAD_FOLDER, file_id)) else MAX_FILE_AGE + 1

                if status_age > MAX_FILE_AGE:
                    del download_status[file_id]

            # 1시간마다 실행
            time.sleep(3600)
        except Exception as e:
            logging.error(f"상태 정보 정리 중 오류: {str(e)}")
            time.sleep(600)  # 오류 발생 시 10분 후 재시도

# 애플리케이션 초기화 함수
def init_app():
    # 서버 시작 시 오래된 파일 정리
    clean_old_files()

    # 백그라운드 스레드로 파일 정리 예약
    cleaning_thread = threading.Thread(target=schedule_cleaning)
    cleaning_thread.daemon = True
    cleaning_thread.start()

    # 상태 정보 정리 스레드
    status_cleaning_thread = threading.Thread(target=clean_status_dict)
    status_cleaning_thread.daemon = True
    status_cleaning_thread.start()

# 애플리케이션 초기화 (Gunicorn에서 사용)
init_app()

# 개발 환경에서 직접 실행할 때만 사용
if __name__ == '__main__':
    app.run(debug=True)
    # app.run(host='0.0.0.0', port=5000, debug=False)
