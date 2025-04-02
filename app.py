from flask import Flask, render_template, request, send_file, url_for, redirect, abort
import yt_dlp
import os
import uuid
import re
import time
import logging
import shutil
import gc
from datetime import datetime
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
import atexit
import threading
from flask_babel import Babel, gettext as _
from ipaddress import ip_network, ip_address
from flask import send_from_directory
from flask_limiter.errors import RateLimitExceeded
import psutil

load_dotenv() # 환경 변수 로드
# Env
ALLOWED_HEALTH_IPS = os.getenv('ALLOWED_HEALTH_IPS', '127.0.0.1,125.177.83.187,172.31.0.0/16').split(',') # 환경 변수에서 허용할 IP 목록 가져오기 (쉼표로 구분된 IP 또는 CIDR)
MAX_WORKERS = int(os.getenv('MAX_WORKERS', 3)) # 환경변수에서 max_workers 값 가져오기 (코어당 스레드 수 기준으로 설정 가능)
DOWNLOAD_FOLDER = os.getenv('DOWNLOAD_FOLDER', 'downloads')
MAX_FILE_AGE = int(os.getenv('MAX_FILE_AGE', 14))  # 일 단위
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', 1 * 1024 * 1024 * 1024))
DOWNLOAD_LIMITS = os.getenv('DOWNLOAD_LIMITS', "300 per hour, 20 per minute").split(',')
# DOWNLOAD_LIMITS = os.getenv('DOWNLOAD_LIMITS', "20 per hour, 1 per minute").split(',')
DOWNLOAD_LIMITS = [limit.strip() for limit in DOWNLOAD_LIMITS]

app = Flask(__name__)
app.config['BABEL_DEFAULT_LOCALE'] = 'en'
app.config['BABEL_TRANSLATION_DIRECTORIES'] = 'translations'

status_lock = threading.Lock() # 전역 변수로 락 추가
download_status = {} # 다운로드 상태를 저장할 딕셔너리
fs_lock = threading.Lock() # 파일 시스템 접근을 위한 락
executor = None  # ThreadPoolExecutor 전역 변수

# 요청 제한 설정
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=None,
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

# 언어 설정
LANGUAGES = {
    'en': 'English',                # 영어
    'id': 'Bahasa Indonesia',       # 인도네시아어
    'pt_BR': 'Português (Brasil)',  # 브라질 포르투갈어 - 브라질에서 사용되는 포르투갈어 방언
    'es_MX': 'Español (México)',    # 멕시코 스페인어 - 멕시코에서 사용되는 스페인어 방언
    'vi': 'Tiếng Việt',             # 베트남어
    'fil': 'Filipino',              # 필리핀어(타갈로그어)
    'th': 'ไทย',                    # 태국어
    'fr': 'Français',               # 프랑스어
    'ur': 'اردو',                   # 우르두어(파키스탄)
    'ko': '한국어',                   # 한국어
    'ja': '日本語',                   # 일본어
    'zh': '中文',                    # 중국어
}

# 1. Babel 인스턴스 생성
babel = Babel(app)

# 2. get_locale 함수 정의
def get_locale():
    # URL 경로에서 언어 코드 확인 (예: /ko/, /en/ 등)
    path_parts = request.path.split('/')
    if len(path_parts) > 1 and path_parts[1] in LANGUAGES:
        return path_parts[1]

    # 브라우저 언어 설정 확인
    return request.accept_languages.best_match(LANGUAGES.keys(), default='en')

# 3. Babel 초기화 (get_locale 함수 정의 후에)
babel.init_app(app, locale_selector=get_locale)

def update_status(file_id, status_data):
    with status_lock:
        download_status[file_id] = status_data

def safe_path_join(*paths):
    """안전한 경로 결합"""
    base = os.path.abspath(paths[0])
    for path in paths[1:]:
        joined = os.path.abspath(os.path.join(base, path))
        if not joined.startswith(base):
            raise ValueError("Invalid path")
        base = joined
    return base

def safely_access_files(directory_path):
    with fs_lock:
        if os.path.exists(directory_path):
            files = os.listdir(directory_path)
            return files
        return []

def get_video_info(url):
    with yt_dlp.YoutubeDL({'quiet': True, 'simulate': True}) as ydl:
        return ydl.extract_info(url, download=False)

def download_video(video_url, file_id, download_path):
    try:
        update_status(file_id, {'status': 'downloading', 'progress': 0})

        def progress_hook(d):
            if d['status'] == 'downloading':
                if 'total_bytes' in d and d['total_bytes'] > 0:
                    progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
                elif 'total_bytes_estimate' in d and d['total_bytes_estimate'] > 0:
                    progress = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
                else:
                    progress = 0
                update_status(file_id, {'status': 'downloading', 'progress': progress})
            elif d['status'] == 'finished':
                update_status(file_id, {'status': 'processing', 'progress': 100})

        ydl_opts = {
            # 'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'format': 'bestvideo[vcodec^=avc]+bestaudio[ext=m4a]/best[vcodec^=avc]/bestvideo+bestaudio/best',
            # 'format': 'bestvideo[vcodec^=avc][height<=1080]+bestaudio[ext=m4a]/best[vcodec^=avc][height<=1080]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best',
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
            update_status(file_id, {
                'status': 'completed',
                'title': info.get('title', '알 수 없는 제목'),
                'url': video_url,
                'timestamp': datetime.now().timestamp()
            })
            logging.info(f"다운로드 성공: {info.get('title')} ({video_url})")
            return info
    except Exception as e:
        update_status(file_id, {
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().timestamp()
        })
        if os.path.exists(download_path):
            shutil.rmtree(download_path)
        logging.error(f"다운로드 오류 (URL: {video_url}): {str(e)}")
        return None
    finally:
        gc.collect()

@app.route('/')
def index_redirect():
    # 브라우저 언어에 따라 적절한 언어 URL로 리다이렉트
    lang = get_locale()
    return redirect(f'/{lang}/')

@app.route('/<lang>/', methods=['GET', 'POST'])
def index(lang):
    if lang not in LANGUAGES:
        return redirect('/')

    if request.method == 'POST':
        for limit in DOWNLOAD_LIMITS:
            limiter.limit(limit)(lambda: None)()
        video_url = request.form['video_url']

        if not video_url:
            return render_template('index.html', error=_('URL을 입력해주세요.'))

        try:
            file_id = str(uuid.uuid4())
            download_path = safe_path_join(DOWNLOAD_FOLDER, file_id)

            if not os.path.exists(download_path):
                os.makedirs(download_path)

            executor.submit(download_video, video_url, file_id, download_path)
            return redirect(url_for('download_waiting', lang=lang, file_id=file_id))

        except Exception as e:
            logging.error(f"예상치 못한 오류 (URL: {video_url}): {str(e)}", exc_info=True)
            return render_template('index.html', error=f'{_("다운로드 중 오류가 발생했습니다")}: {str(e)}')

    return render_template('index.html', max_file_size_gb=MAX_FILE_SIZE/(1024*1024*1024))

@app.route('/<lang>/download-waiting/<file_id>')
def download_waiting(lang, file_id):
    if lang not in LANGUAGES:
        return redirect('/')

    if not re.match(r'^[0-9a-f\-]+$', file_id):
        logging.warning(f"유효하지 않은 file_id 접근 시도: {file_id}")
        return redirect(url_for('index', lang=lang))

    with status_lock:
        status = download_status.get(file_id, {'status': 'unknown'})

    if status['status'] == 'completed':
        return redirect(url_for('result', lang=lang, file_id=file_id))

    return render_template('download_waiting.html', file_id=file_id, status=status)

@app.route('/<lang>/check-status/<file_id>')
def check_status(lang, file_id):
    if lang not in LANGUAGES:
        return {'status': 'error', 'error': '지원하지 않는 언어입니다'}

    if not re.match(r'^[0-9a-f\-]+$', file_id):
        logging.warning(f"유효하지 않은 file_id 상태 확인 시도: {file_id}")
        return {'status': 'error', 'error': '유효하지 않은 파일 ID'}

    with status_lock:
        status = download_status.get(file_id, {'status': 'unknown'})

    if status.get('status') == 'completed':
        return {
            'status': 'completed',
            'redirect': url_for('result', lang=lang, file_id=file_id)
        }

    return status

@app.route('/<lang>/result/<file_id>')
def result(lang, file_id):
    if lang not in LANGUAGES:
        return redirect('/')

    if not re.match(r'^[0-9a-f\-]+$', file_id):
        logging.warning(f"유효하지 않은 file_id 접근 시도: {file_id}")
        return redirect(url_for('index', lang=lang))

    with status_lock:
        status = download_status.get(file_id)

    if not status or status.get('status') != 'completed':
        logging.error(f"완료되지 않은 다운로드에 대한 접근: {file_id}")
        return redirect(url_for('index'))

    download_path = safe_path_join(DOWNLOAD_FOLDER, file_id)
    if not os.path.exists(download_path):
        logging.error(f"다운로드 경로를 찾을 수 없음: {download_path}")
        return render_template('index.html', error="다운로드 파일을 찾을 수 없습니다.")

    files = safely_access_files(download_path)
    if not files:
        logging.error(f"다운로드 폴더에 파일이 없음: {download_path}")
        return render_template('index.html', error="다운로드된 파일이 없습니다.")

    file_name = files[0]
    with fs_lock:
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

@app.route('/<lang>/download-file/<file_id>')
def download_file(lang, file_id):
    if lang not in LANGUAGES:
        return redirect('/')

    try:
        logging.info(f"파일 다운로드 시작: {file_id}")

        if not re.match(r'^[0-9a-f\-]+$', file_id):
            logging.warning(f"유효하지 않은 file_id 다운로드 시도: {file_id}")
            return render_template('index.html', error=_("유효하지 않은 파일 ID입니다."))

        download_path = safe_path_join(DOWNLOAD_FOLDER, file_id)

        with fs_lock:
            if not os.path.exists(download_path):
                logging.error(f"다운로드 경로를 찾을 수 없음: {download_path}")
                return render_template('index.html', error="다운로드 파일을 찾을 수 없습니다.")

        files = safely_access_files(download_path)
        if not files:
            logging.error(f"다운로드 폴더에 파일이 없음: {download_path}")
            return render_template('index.html', error="다운로드된 파일이 없습니다.")

        filename = files[0]
        file_path = safe_path_join(download_path, filename)

        with fs_lock:
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

@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.static_folder, 'robots.txt')

def clean_old_files():
    try:
        now = datetime.now()
        cleaned_count = 0

        # 디렉토리 목록 가져올 때 락 사용
        with fs_lock:
            folder_names = os.listdir(DOWNLOAD_FOLDER)

        for folder_name in folder_names:
            # 각 폴더 작업할 때마다 락 사용
            with fs_lock:
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
            to_delete = []

            with status_lock:
                for file_id in list(download_status.keys()):
                    status = download_status[file_id]
                    if status['status'] in ['completed', 'error']:
                        timestamp = status.get('timestamp', 0)
                        if (now - datetime.fromtimestamp(timestamp)).total_seconds() > 3600:  # 1시간
                            to_delete.append(file_id)

                for file_id in to_delete:
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

@app.context_processor
def inject_languages():
    return {
        'languages': LANGUAGES,
        'current_lang': get_locale()
    }

# health check endpoint
@app.route('/health')
def health_check():
    client_ip = get_remote_address()
    logging.info(f"헬스체크 요청 IP: {client_ip}")
    if not check_ip_allowed(client_ip):
        logging.warning(f"허용되지 않은 IP({client_ip})에서 health 엔드포인트 접근 시도")
        abort(403)

    try:
        health_data = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "version": os.getenv('APP_VERSION', '1.0.0'),
            "components": {}
        }

        # 파일 시스템 상태 확인
        try:
            with fs_lock:
                fs_writeable = os.access(DOWNLOAD_FOLDER, os.W_OK)
                available_space = shutil.disk_usage(DOWNLOAD_FOLDER).free

            health_data["components"]["filesystem"] = {
                "status": "healthy" if fs_writeable else "degraded",
                "available_space_gb": round(available_space / (1024**3), 2),
                "writable": fs_writeable
            }
        except Exception as e:
            health_data["components"]["filesystem"] = {
                "status": "unhealthy",
                "error": str(e)
            }
            health_data["status"] = "degraded"

        # 스레드 풀 상태 확인
        try:
            queue_size = executor._work_queue.qsize() # 현재 대기 중인 작업 수
            total_tasks = sum(1 for s in download_status.values() if s.get('status') == 'downloading' or s.get('status') == 'processing') # 전체 작업 추적을 위한 변수들 추가
            active_workers = min(total_tasks - queue_size, executor._max_workers) if total_tasks > queue_size else 0 # 실제 실행 중인 작업자 수 (전체 작업 - 대기 중인 작업)
            available_workers = executor._max_workers - active_workers # 사용 가능한 작업자 수
            health_data["components"]["thread_pool"] = {
                "status": "healthy",
                "max_workers": executor._max_workers,
                "available_workers": available_workers,
                "active_workers": active_workers,
                "waiting_tasks": queue_size,
                "total_tasks": total_tasks,
                "utilization_percent": round((active_workers / executor._max_workers) * 100, 2) if active_workers > 0 else 0
            }
        except Exception as e:
            health_data["components"]["thread_pool"] = {
                "status": "unknown",
                "error": str(e)
            }

        # 다운로드 상태 통계
        try:
            with status_lock:
                total = len(download_status)
                completed = sum(1 for s in download_status.values() if s.get('status') == 'completed')
                downloading = sum(1 for s in download_status.values() if s.get('status') == 'downloading')
                errors = sum(1 for s in download_status.values() if s.get('status') == 'error')

            health_data["components"]["downloads"] = {
                "status": "healthy",
                "total": total,
                "completed": completed,
                "in_progress": downloading,
                "errors": errors
            }
        except Exception as e:
            health_data["components"]["downloads"] = {
                "status": "unknown",
                "error": str(e)
            }

        # Gunicorn 워커/스레드 상태 추정
        try:
            process = psutil.Process()
            connections = len(process.connections(kind='inet'))
            cpu_percent = process.cpu_percent(interval=0.1)
            system_load = os.getloadavg()[0]  # 1분 평균 로드

            # 실시간 연결 수 vs. 최대 처리 가능 연결 수
            workers = int(os.environ.get('GUNICORN_WORKERS', 1))
            threads = int(os.environ.get('GUNICORN_THREADS', 4))

            # 최대 동시 처리 가능 요청 수 계산
            gunicorn_capacity = workers * threads
            connections = len(process.connections(kind='inet'))

            gunicorn_usage = min(100, (connections / gunicorn_capacity) * 100)
            gunicorn_queue_estimate = max(0, connections - gunicorn_capacity)

            # 스레드풀 대기열 크기 가져오기
            threadpool_queue = health_data["components"]["thread_pool"]["waiting_tasks"]

            health_data["components"]["gunicorn_stats"] = {
                "status": "healthy" if gunicorn_usage < 80 else "warning",
                "capacity": {
                    "workers": workers,
                    "threads_per_worker": threads,
                    "max_concurrent_requests": gunicorn_capacity
                },
                "usage": {
                    "active_connections": connections,
                    "usage_percent": round(gunicorn_usage, 1),
                    "estimated_queue": gunicorn_queue_estimate
                },
                "system": {
                    "cpu_percent": round(cpu_percent, 1),
                    "system_load": round(system_load, 2)
                }
            }

            # 전체 병목 상태 평가
            health_data["components"]["bottleneck_analysis"] = {
                "http_layer_pressure": "high" if gunicorn_usage > 80 else "moderate" if gunicorn_usage > 60 else "low",
                "worker_pool_pressure": "high" if threadpool_queue > 0 else "low",
                "primary_bottleneck": "gunicorn_threads" if gunicorn_usage > 80 and (threadpool_queue == 0) else
                "worker_pool" if threadpool_queue > 0 and gunicorn_usage < 80 else
                "both" if gunicorn_usage > 80 and threadpool_queue > 0 else "none",
                "scaling_recommendation": get_scaling_recommendation(gunicorn_usage, threadpool_queue, cpu_percent)
            }
        except Exception as e:
            health_data["components"]["gunicorn_stats"] = {
                "status": "unknown",
                "error": str(e)
            }

        return health_data, 200
    except Exception as e:
        logging.error(f"헬스 체크 중 오류: {str(e)}", exc_info=True)
        return {"status": "unhealthy", "error": str(e)}, 500

def get_scaling_recommendation(gunicorn_usage, threadpool_queue, cpu_percent):
    if gunicorn_usage > 80 and threadpool_queue > 0:
        return {
            "action": "increase_both",
            "reason": "HTTP 요청과 작업 처리 모두 병목 발생",
            "recommendation": "Gunicorn threads와 MAX_WORKERS 모두 증가 필요"
        }
    elif gunicorn_usage > 80:
        return {
            "action": "increase_threads",
            "reason": "HTTP 요청 처리 병목 발생",
            "recommendation": "Gunicorn threads 증가 또는 workers 증가 필요"
        }
    elif threadpool_queue > 0:
        return {
            "action": "increase_max_workers",
            "reason": "다운로드 작업 처리 병목 발생",
            "recommendation": "ThreadPool MAX_WORKERS 증가 필요"
        }
    elif cpu_percent > 80:
        return {
            "action": "increase_workers",
            "reason": "CPU 사용률 높음",
            "recommendation": "Gunicorn workers 증가로 CPU 활용도 향상 필요"
        }
    else:
        return {
            "action": "none",
            "reason": "현재 모든 지표가 정상 범위",
            "recommendation": "현재 설정 유지"
        }

def check_ip_allowed(ip_str):
    try:
        client_ip = ip_address(ip_str)
        for allowed in ALLOWED_HEALTH_IPS:
            # CIDR 표기법 (예: 10.0.0.0/8) 또는 단일 IP 처리
            if '/' in allowed:
                if client_ip in ip_network(allowed):
                    return True
            elif client_ip == ip_address(allowed):
                return True
        return False
    except ValueError:
        return False

# error handler
@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', error="The page you requested was not found."), 404

@app.errorhandler(500)
def server_error(e):
    logging.error(f"Server error occurred: {str(e)}", exc_info=True)
    return render_template('error.html', error="An internal server error occurred. Please try again later."), 500

@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', error="You don't have permission to access this resource."), 403

@app.errorhandler(400)
def bad_request(e):
    return render_template('error.html', error="Invalid request."), 400

@app.errorhandler(429)
@app.errorhandler(RateLimitExceeded)
def ratelimit_handler(e):
    logging.warning(f"Rate limit exceeded: {get_remote_address()}")
    return render_template('error.html', error="Too many download requests. Please try again later."), 429

@app.errorhandler(Exception)
def handle_unexpected_error(e):
    logging.error(f"Unexpected error: {str(e)}", exc_info=True)
    return render_template('error.html', error="An unexpected error occurred. Please try again later."), 500

def init_app():
    global executor
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS) # 스레드 풀 초기화

    clean_old_files()

    cleaning_thread = threading.Thread(target=schedule_cleaning)
    cleaning_thread.daemon = True
    cleaning_thread.start()

    status_cleaning_thread = threading.Thread(target=clean_status_dict)
    status_cleaning_thread.daemon = True
    status_cleaning_thread.start()

    atexit.register(cleanup_on_exit)

init_app()

if __name__ == '__main__': # local
    host = os.getenv('FLASK_HOST', '127.0.0.1')
    port = int(os.getenv('FLASK_PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'true').lower() == 'true'
    app.run(host=host, port=port, debug=debug)
