from flask import Flask, render_template, request, send_file, url_for, redirect, Response
import yt_dlp
import os
import uuid
import re
import threading
import time
import logging
import shutil
import gc
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
import atexit

# 환경 변수 로드
load_dotenv()

app = Flask(__name__)

# 다운로드 파일 저장 폴더 및 설정
DOWNLOAD_FOLDER = os.getenv('DOWNLOAD_FOLDER', 'downloads')
MAX_FILE_AGE = int(os.getenv('MAX_FILE_AGE', 14))  # 일 단위
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', 2 * 1024 * 1024 * 1024))  # 기본 2GB

# 다운로드 상태를 저장할 딕셔너리
download_status = {}

# 요청 제한 설정
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)
limiter.init_app(app)

if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

if not os.path.exists('logs'):
    os.makedirs('logs')

# 로깅 설정
logging.basicConfig(
    filename='logs/app.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# 스레드 풀 초기화
executor = ThreadPoolExecutor(max_workers=6) # 코어당 3개 스레드

def safe_path_join(*paths):
    """안전한 경로 결합"""
    base = os.path.abspath(paths[0])
    for path in paths[1:]:
        joined = os.path.abspath(os.path.join(base, path))
        if not joined.startswith(base):
            raise ValueError("Invalid path")
        base = joined
    return base

def get_video_info(url):
    with yt_dlp.YoutubeDL({'quiet': True, 'simulate': True}) as ydl:
        return ydl.extract_info(url, download=False)

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
            # 'ffmpeg_location': r'C:\Users\raphael\Desktop\setup\ffmpeg-7.1.1-essentials_build\bin\ffmpeg.exe',  # Running on window
            'merge_output_format': 'mp4',
            'outtmpl': download_path + '/%(title)s.%(ext)s',
            'noplaylist': True,
            'retries': 3,
            'fragment_retries': 3,
            'socket_timeout': 15,
            'max_filesize': MAX_FILE_SIZE,
            'noprogress': True,
            'buffersize': 1024,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            },
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'progress_hooks': [progress_hook],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            download_status[file_id] = {
                'status': 'completed',
                'title': info.get('title', '알 수 없는 제목'),
                'url': video_url,
                'timestamp': datetime.now().timestamp()
            }
            logging.info(f"다운로드 성공: {info.get('title')} ({video_url})")
            return info
    except Exception as e:
        download_status[file_id] = {
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().timestamp()
        }
        if os.path.exists(download_path):
            shutil.rmtree(download_path)
        logging.error(f"다운로드 오류 (URL: {video_url}): {str(e)}")
        return None
    finally:
        gc.collect()

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        video_url = request.form['video_url']

        if not video_url:
            return render_template('index.html', error='URL을 입력해주세요.')

        try:
            file_id = str(uuid.uuid4())
            download_path = safe_path_join(DOWNLOAD_FOLDER, file_id)

            if not os.path.exists(download_path):
                os.makedirs(download_path)

            executor.submit(download_video, video_url, file_id, download_path)
            return redirect(url_for('download_waiting', file_id=file_id))

        except Exception as e:
            logging.error(f"예상치 못한 오류 (URL: {video_url}): {str(e)}", exc_info=True)
            return render_template('index.html', error=f'다운로드 중 오류가 발생했습니다: {str(e)}')

    return render_template('index.html', max_file_size_gb=MAX_FILE_SIZE/(1024*1024*1024))

@app.route('/download-waiting/<file_id>')
def download_waiting(file_id):
    if not re.match(r'^[0-9a-f\-]+$', file_id):
        logging.warning(f"유효하지 않은 file_id 접근 시도: {file_id}")
        return redirect(url_for('index'))

    status = download_status.get(file_id, {'status': 'unknown'})

    if status['status'] == 'completed':
        return redirect(url_for('result', file_id=file_id))

    return render_template('download_waiting.html', file_id=file_id, status=status)

@app.route('/check-status/<file_id>')
def check_status(file_id):
    if not re.match(r'^[0-9a-f\-]+$', file_id):
        logging.warning(f"유효하지 않은 file_id 상태 확인 시도: {file_id}")
        return {'status': 'error', 'error': '유효하지 않은 파일 ID'}

    status = download_status.get(file_id, {'status': 'unknown'})

    if status.get('status') == 'completed':
        return {
            'status': 'completed',
            'redirect': url_for('result', file_id=file_id)
        }

    return status

@app.route('/result/<file_id>')
def result(file_id):
    if not re.match(r'^[0-9a-f\-]+$', file_id):
        logging.warning(f"유효하지 않은 file_id 접근 시도: {file_id}")
        return redirect(url_for('index'))

    status = download_status.get(file_id)
    if not status or status.get('status') != 'completed':
        logging.error(f"완료되지 않은 다운로드에 대한 접근: {file_id}")
        return redirect(url_for('index'))

    download_path = safe_path_join(DOWNLOAD_FOLDER, file_id)
    if not os.path.exists(download_path):
        logging.error(f"다운로드 경로를 찾을 수 없음: {download_path}")
        return render_template('index.html', error="다운로드 파일을 찾을 수 없습니다.")

    files = os.listdir(download_path)
    if not files:
        logging.error(f"다운로드 폴더에 파일이 없음: {download_path}")
        return render_template('index.html', error="다운로드된 파일이 없습니다.")

    file_name = files[0]
    file_path = safe_path_join(download_path, file_name)
    file_size = os.path.getsize(file_path) if os.path.isfile(file_path) else 0

    def readable_size(size_bytes):
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    return render_template('download_result.html',
                           title=status.get('title', '알 수 없는 제목'),
                           file_id=file_id,
                           url=status.get('url', ''),
                           file_name=file_name,
                           file_size=readable_size(file_size))

@app.route('/download-file/<file_id>')
def download_file(file_id):
    try:
        logging.info(f"파일 다운로드 시작: {file_id}")

        if not re.match(r'^[0-9a-f\-]+$', file_id):
            logging.warning(f"유효하지 않은 file_id 다운로드 시도: {file_id}")
            return render_template('index.html', error="유효하지 않은 파일 ID입니다.")

        download_path = safe_path_join(DOWNLOAD_FOLDER, file_id)

        if not os.path.exists(download_path):
            logging.error(f"다운로드 경로를 찾을 수 없음: {download_path}")
            return render_template('index.html', error="다운로드 파일을 찾을 수 없습니다.")

        files = os.listdir(download_path)
        if not files:
            logging.error(f"다운로드 폴더에 파일이 없음: {download_path}")
            return render_template('index.html', error="다운로드된 파일이 없습니다.")

        filename = files[0]
        file_path = safe_path_join(download_path, filename)

        if not os.path.isfile(file_path):
            logging.error(f"파일이 아닌 경로: {file_path}")
            return render_template('index.html', error="유효하지 않은 파일입니다.")

        safe_filename = f"download-{file_id}.mp4"

        response = send_file(
            file_path,
            as_attachment=True,
            mimetype='video/mp4'
        )

        encoded_filename = quote(filename)
        response.headers["Content-Disposition"] = f"attachment; filename=\"{safe_filename}\"; filename*=UTF-8''{encoded_filename}"

        return response
    except Exception as e:
        logging.error(f"파일 다운로드 중 오류: {str(e)}", exc_info=True)
        return render_template('index.html', error=f"파일 다운로드 중 오류가 발생했습니다: {str(e)}")

def clean_old_files():
    try:
        now = datetime.now()
        cleaned_count = 0

        for folder_name in os.listdir(DOWNLOAD_FOLDER):
            folder_path = safe_path_join(DOWNLOAD_FOLDER, folder_name)
            if os.path.isdir(folder_path):
                folder_creation_time = datetime.fromtimestamp(os.path.getctime(folder_path))
                days_old = (now - folder_creation_time).days

                if days_old > MAX_FILE_AGE:
                    shutil.rmtree(folder_path)
                    cleaned_count += 1

        logging.info(f"파일 정리 완료: {cleaned_count}개 폴더 삭제됨")
    except Exception as e:
        logging.error(f"파일 정리 실행 중 오류 발생: {str(e)}", exc_info=True)

def clean_status_dict():
    while True:
        try:
            now = datetime.now()
            for file_id in list(download_status.keys()):
                status = download_status[file_id]
                if status['status'] in ['completed', 'error']:
                    timestamp = status.get('timestamp', 0)
                    if (now - datetime.fromtimestamp(timestamp)).total_seconds() > 3600:  # 1시간
                        del download_status[file_id]

            time.sleep(3600)  # 1시간
        except Exception as e:
            logging.error(f"상태 정보 정리 중 오류: {str(e)}")
            time.sleep(600)  # 10분 후 재시도

def schedule_cleaning():
    while True:
        try:
            clean_old_files()
            time.sleep(86400)  # 24시간
        except Exception as e:
            logging.error(f"예약된 파일 정리 중 오류: {str(e)}")
            time.sleep(3600)  # 1시간 후 재시도

def cleanup_on_exit():
    executor.shutdown(wait=True)
    logging.info("애플리케이션 종료: 리소스 정리 완료")

def init_app():
    clean_old_files()

    cleaning_thread = threading.Thread(target=schedule_cleaning)
    cleaning_thread.daemon = True
    cleaning_thread.start()

    status_cleaning_thread = threading.Thread(target=clean_status_dict)
    status_cleaning_thread.daemon = True
    status_cleaning_thread.start()

    atexit.register(cleanup_on_exit)

init_app()

if __name__ == '__main__':
    # host = os.getenv('FLASK_HOST', '127.0.0.1')
    host = os.getenv('FLASK_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_PORT', 5000))
    app.run(host=host, port=port, debug=True)
