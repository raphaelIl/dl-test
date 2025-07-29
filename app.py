from flask import Flask, render_template, request, send_file, url_for, redirect, abort
import yt_dlp
import os
import uuid
import re
import time
import logging
import shutil
import gc
import json
from datetime import datetime
from dotenv import load_dotenv
from flask_limiter import Limiter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
import atexit
import threading
from flask_babel import Babel, gettext as _
from ipaddress import ip_network, ip_address
from flask import send_from_directory
from flask_limiter.errors import RateLimitExceeded
import psutil
from werkzeug.middleware.proxy_fix import ProxyFix

# Enviornment Variables
load_dotenv() # 환경 변수 로드
ALLOWED_HEALTH_IPS = os.getenv('ALLOWED_HEALTH_IPS', '127.0.0.1,125.177.83.187,172.31.0.0/16').split(',') # 환경 변수에서 허용할 IP 목록 가져오기 (쉼표로 구분된 IP 또는 CIDR)
MAX_WORKERS = int(os.getenv('MAX_WORKERS', 3)) # 환경변수에서 max_workers 값 가져오기 (코어당 스레드 수 기준으로 설정 가능)
DOWNLOAD_FOLDER = os.getenv('DOWNLOAD_FOLDER', 'downloads')
DOWNLOAD_STATS_FILE = os.getenv('DOWNLOAD_STATS_FILE', 'download_stats.json')
STATUS_MAX_AGE = int(os.getenv('STATUS_MAX_AGE', 120)) # 2mins
STATUS_CLEANUP_INTERVAL = int(os.getenv('STATUS_CLEANUP_INTERVAL', 60)) # 1min
# MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', 1 * 1024 * 1024 * 1024)) # 1GB
# MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE_MB', 400)) * 1024 * 1024
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE_MB', 40000)) * 1024 * 1024
DOWNLOAD_LIMITS = os.getenv('DOWNLOAD_LIMITS', "20 per hour, 100 per minute").split(',')
DOWNLOAD_LIMITS = [limit.strip() for limit in DOWNLOAD_LIMITS]
CACHE_CONFIG = {
    'css_js': os.getenv('CACHE_CSS_JS', '31536000,604800'),      # 브라우저 1년, CDN 1주일
    'media': os.getenv('CACHE_MEDIA', '31536000,31536000'),      # 브라우저/CDN 모두 1년
    'default': os.getenv('CACHE_DEFAULT', '86400,86400')         # 브라우저/CDN 모두 1일
}

app = Flask(__name__)
app.config['BABEL_DEFAULT_LOCALE'] = 'en'
app.config['BABEL_TRANSLATION_DIRECTORIES'] = 'translations'

app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,      # X-Forwarded-For 헤더에서 처음 항목을 클라이언트 IP로 사용
    x_proto=1,    # X-Forwarded-Proto 헤더 처리
    x_host=1,     # X-Forwarded-Host 헤더 처리
    x_port=1      # X-Forwarded-Port 헤더 처리
)

status_lock = threading.Lock() # 전역 변수로 락 추가
download_status = {} # 다운로드 상태를 저장할 딕셔너리
fs_lock = threading.Lock() # 파일 시스템 접근을 위한 락
executor = None  # ThreadPoolExecutor 전역 변수

# 다운로드 통계를 파일에 저장하고 관리하는 기능
download_stats_lock = threading.Lock()

def load_download_stats():
    """파일에서 다운로드 통계를 로드합니다."""
    try:
        if os.path.exists(DOWNLOAD_STATS_FILE):
            with open(DOWNLOAD_STATS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"다운로드 통계 로드 중 오류: {str(e)}")

    return {
        'total': 0,
        'completed': 0,
        'errors': 0,
        'last_updated': datetime.now().isoformat()
    }

def save_download_stats(stats):
    """다운로드 통계를 파일에 저장합니다."""
    try:
        with download_stats_lock:
            stats['last_updated'] = datetime.now().isoformat()
            with open(DOWNLOAD_STATS_FILE, 'w') as f:
                json.dump(stats, f, indent=2)
    except Exception as e:
        logging.error(f"다운로드 통계 저장 중 오류: {str(e)}")

def update_download_stats(status):
    """다운로드 상태 변경 시 통계를 업데이트합니다."""
    stats = load_download_stats()

    if status == 'started':
        stats['total'] += 1
    elif status == 'completed':
        stats['completed'] += 1
    elif status == 'error':
        stats['errors'] += 1

    save_download_stats(stats)

# X-Forwarded-For 헤더가 있으면 첫 번째 IP 사용 (CloudFlare에 의해 설정됨)
def get_client_ip():
    if request.headers.get('CF-Connecting-IP'):
        return request.headers.get('CF-Connecting-IP')
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr

# 요청 제한 설정
limiter = Limiter(
    key_func=get_client_ip,
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
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.setLevel(logging.ERROR)
app.logger.setLevel(logging.ERROR)

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

    # 브라우저 언어 설정 확인 - 기본값을 영어로 설정
    browser_lang = request.accept_languages.best_match(LANGUAGES.keys())
    return browser_lang if browser_lang else 'en'

def get_browser_preferred_language():
    """브라우저의 선호 언어를 감지하되, 기본값은 영어"""
    # Accept-Language 헤더에서 언어 감지
    browser_lang = request.accept_languages.best_match(LANGUAGES.keys())
    return browser_lang if browser_lang else 'en'

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
                    # 파일 크기 제한 체크 추가
                    if d['total_bytes'] > MAX_FILE_SIZE:
                        update_status(file_id, {
                            'status': 'error',
                            'error': "This video is too big.",
                            # 'error': f'파일 크기 제한 초과: {d["total_bytes"]/(1024*1024):.1f}MB (최대 {MAX_FILE_SIZE/(1024*1024)}MB)',
                            'timestamp': datetime.now().timestamp()
                        })
                        return
                    progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
                elif 'total_bytes_estimate' in d and d['total_bytes_estimate'] > 0:
                    # 파일 크기 제한 예상치 체크 추가
                    if d['total_bytes_estimate'] > MAX_FILE_SIZE:
                        update_status(file_id, {
                            'status': 'error',
                            'error': f'파일 크기 제한 초과: {d["total_bytes_estimate"]/(1024*1024):.1f}MB (최대 {MAX_FILE_SIZE/(1024*1024)}MB)',
                            'timestamp': datetime.now().timestamp()
                        })
                        return
                    progress = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
                else:
                    progress = 0
                update_status(file_id, {'status': 'downloading', 'progress': progress})
            elif d['status'] == 'finished':
                update_status(file_id, {'status': 'processing', 'progress': 100})
            elif d['status'] == 'error':
                update_status(file_id, {
                    'status': 'error',
                    'error': d.get('error', '알 수 없는 오류'),
                    'timestamp': datetime.now().timestamp()
                })

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
            'socket_timeout': 30,
            'max_filesize': MAX_FILE_SIZE,
            'noprogress': True,
            'buffersize': 1024,
            'nocheckcertificate': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.7204.158 Safari/537.36',
            },
            # 'downloader': 'ffmpeg',
            # 'hls_use_mpegts': True,
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
            logging.info(f"서버 다운로드 성공: {info.get('title')} ({video_url})")
            update_download_stats('completed')
            return info
    except Exception as e:
        error_msg = str(e)
        error_id = generate_error_id()

        # log details
        logging.error(f"다운로드 오류 (ID: {error_id}, URL: {video_url}): {error_msg}", exc_info=True)

        # friendly error message
        user_friendly_msg = _("An error occurred during video download. Please try again later.")

        # 주요 에러 패턴 인식 및 사용자 친화적인 메시지 설정
        if "File is larger than max-filesize" in error_msg:
            user_friendly_msg = _("This video is too large. Please try a shorter video or lower quality.")
        elif "Video unavailable" in error_msg:
            user_friendly_msg = _("The video could not be downloaded. It may be unavailable.")
        elif "Private video" in error_msg:
            user_friendly_msg = _("Private videos cannot be downloaded.")
        elif "This video is available for premium users only" in error_msg or "paywall" in error_msg.lower():
            user_friendly_msg = _("This video requires a premium account and cannot be downloaded.")
        elif "Sign in to confirm your age" in error_msg or "age" in error_msg.lower():
            user_friendly_msg = _("Age-restricted videos cannot be downloaded.")
        elif "requested format not available" in error_msg.lower():
            user_friendly_msg = _("The requested video format is not available.")
        elif "ffmpeg not found" in error_msg.lower() or "ffmpeg" in error_msg.lower():
            user_friendly_msg = _("Server configuration error. Please contact support.")
            logging.critical(f"FFmpeg 관련 오류 (ID: {error_id}): {error_msg}")
        elif "copyright" in error_msg.lower() or "blocked" in error_msg.lower():
            user_friendly_msg = _("This video cannot be accessed due to copyright restrictions.")
        elif "429" in error_msg or "too many requests" in error_msg.lower():
            user_friendly_msg = _("Service temporarily unavailable due to high traffic. Please try again later.")
        elif "network error" in error_msg.lower() or "connection" in error_msg.lower():
            user_friendly_msg = _("A network error occurred. Please check your internet connection or try again later.")
        elif "timeout" in error_msg.lower():
            user_friendly_msg = _("The download timed out. Please try again later.")
        elif "quota" in error_msg.lower():
            user_friendly_msg = _("Download quota exceeded. Please try again later.")
        elif "not a valid URL" in error_msg:
            user_friendly_msg = _("Please enter a valid video URL.")
        elif "unsupported url" in error_msg.lower():
            user_friendly_msg = _("This URL is not supported for downloading.")

        update_status(file_id, {
            'status': 'error',
            'error': user_friendly_msg,
            'error_id': error_id,
            'timestamp': datetime.now().timestamp()
        })
        update_download_stats('error')

        if os.path.exists(download_path):
            shutil.rmtree(download_path)
        return None
    finally:
        gc.collect()

@app.after_request
def add_cache_headers(response):
    if request.path.startswith('/static/'):
        path = request.path

        if path.endswith(('.css', '.js')):
            cache_key = 'css_js'
        elif path.endswith(('.ico', '.jpg', '.jpeg', '.png', '.gif', '.svg', '.woff', '.woff2')):
            cache_key = 'media'
        else:
            cache_key = 'default'

        try:
            browser_ttl, cdn_ttl = map(int, CACHE_CONFIG[cache_key].split(','))
        except (ValueError, AttributeError):
            browser_ttl, cdn_ttl = 86400, 86400

        response.headers['Cache-Control'] = f'public, max-age={browser_ttl}, s-maxage={cdn_ttl}'

    return response

@app.route('/')
def index_redirect():
    # SEO 최적화: 검색엔진 크롤러는 기본 영어 페이지로 제공
    user_agent = request.headers.get('User-Agent', '').lower()

    # 검색엔진 크롤러 감지
    search_engines = ['googlebot', 'bingbot', 'slurp', 'duckduckbot', 'baiduspider', 'yandexbot', 'facebookexternalhit', 'twitterbot']
    is_crawler = any(bot in user_agent for bot in search_engines)

    if is_crawler:
        # 검색엔진 크롤러에게는 영어 페이지 직접 제공 (리다이렉트 없이)
        return index('en')
    else:
        # 일반 사용자에게는 브라우저 언어에 따라 리다이렉트
        preferred_lang = get_browser_preferred_language()
        return redirect(f'/{preferred_lang}/', code=302)

# 검색엔진을 위한 기본 영어 라우트 추가
@app.route('/en')
def english_redirect():
    return redirect('/en/', code=301)

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
            update_download_stats('started')  # 다운로드 시작 시 통계 업데이트
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

    return render_template('download_waiting.html', file_id=file_id, status=status,
                           current_lang=lang, languages=LANGUAGES)

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
        return redirect(url_for('index', lang=lang))

    download_path = safe_path_join(DOWNLOAD_FOLDER, file_id)
    if not os.path.exists(download_path):
        logging.error(f"다운로드 경로를 찾을 수 없음: {download_path}")
        return render_template('index.html', error="다운로드 파일을 찾을 수 없습니다.", current_lang=lang, languages=LANGUAGES)

    files = safely_access_files(download_path)
    if not files:
        logging.error(f"다운로드 폴더에 파일이 없음: {download_path}")
        return render_template('index.html', error="다운로드된 파일이 없습니다.", current_lang=lang, languages=LANGUAGES)

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
            return render_template('index.html', error=_("유효하지 않은 파일 ID입니다."), current_lang=lang, languages=LANGUAGES)

        download_path = safe_path_join(DOWNLOAD_FOLDER, file_id)

        with fs_lock:
            if not os.path.exists(download_path):
                logging.error(f"다운로드 경로를 찾을 수 없음: {download_path}")
                return render_template('index.html', error="다운로드 파일을 찾을 수 없습니다.", current_lang=lang, languages=LANGUAGES)

        files = safely_access_files(download_path)
        if not files:
            logging.error(f"다운로드 폴더에 파일이 없음: {download_path}")
            return render_template('index.html', error="다운로드된 파일이 없습니다.", current_lang=lang, languages=LANGUAGES)

        filename = files[0]
        file_path = safe_path_join(download_path, filename)

        with fs_lock:
            if not os.path.isfile(file_path):
                logging.error(f"파일이 아닌 경로: {file_path}")
                return render_template('index.html', error="유효하지 않은 파일입니다.", current_lang=lang, languages=LANGUAGES)

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
        return render_template('index.html', error=f"파일 다운로드 중 오류가 발생했습니다: {str(e)}", current_lang=lang, languages=LANGUAGES)

@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.static_folder, 'robots.txt')

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
                        if (now - datetime.fromtimestamp(timestamp)).total_seconds() > STATUS_MAX_AGE:
                            to_delete.append(file_id)

                # 상태 정보 삭제 및 파일 시스템 정리
                for file_id in to_delete:
                    del download_status[file_id]
                    logging.info(f"상태 정보 정리됨: {file_id}")

                    # 파일 시스템에서 폴더 삭제
                    folder_path = safe_path_join(DOWNLOAD_FOLDER, file_id)
                    try:
                        if os.path.exists(folder_path):
                            shutil.rmtree(folder_path)
                            logging.info(f"다운로드 파일 정리됨: {file_id}")
                    except Exception as e:
                        logging.error(f"폴더 삭제 중 오류 발생: {file_id}, {str(e)}", exc_info=True)

            time.sleep(STATUS_CLEANUP_INTERVAL)
        except Exception as e:
            logging.error(f"상태 정보 정리 중 오류: {str(e)}")
            time.sleep(STATUS_CLEANUP_INTERVAL)

def cleanup_on_exit():
    executor.shutdown(wait=True)
    logging.warning("애플리케이션 종료: 리소스 정리 완료")

@app.context_processor
def inject_languages():
    return {
        'languages': LANGUAGES,
        'current_lang': get_locale()
    }

@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory('static', 'sitemap.xml')
#
@app.route('/ads.txt')
def ads_txt():
    return send_from_directory('static', 'ads.txt')

@app.route('/health')
def health_check():
    client_ip = get_client_ip()
    logging.info(f"헬스체크 요청 IP: {client_ip}")
    if not check_ip_allowed(client_ip):
        logging.warning(f"허용되지 않은 IP({client_ip})에서 health 엔드포인트 접근 시도")
        abort(403)

    try:
        # 파일에서 다운로드 통계 로드
        stats = load_download_stats()

        # 현재 진행 중인 다운로드 수 계산
        with status_lock:
            in_progress = sum(1 for s in download_status.values()
                            if s.get('status') in ['downloading', 'processing'])

        health_data = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "version": os.getenv('APP_VERSION', '1.0.0'),
            "downloads": {
                "total": stats.get('total', 0),
                "completed": stats.get('completed', 0),
                "in_progress": in_progress,
                "errors": stats.get('errors', 0)
            }
        }

        return health_data, 200
    except Exception as e:
        logging.error(f"헬스 체크 중 오류: {str(e)}", exc_info=True)
        return {"status": "unhealthy", "error": str(e)}, 500

def get_scaling_recommendation(gunicorn_usage, threadpool_queue, cpu_percent):
    if gunicorn_usage > 80 and threadpool_queue > 0:
        return {
            "action": "increase_both",
            "reason": "Bottlenecks in both HTTP requests and task processing",
            "recommendation": "Increase both Gunicorn threads and MAX_WORKERS"
        }
    elif gunicorn_usage > 80:
        return {
            "action": "increase_threads",
            "reason": "Bottleneck in HTTP request processing",
            "recommendation": "Increase Gunicorn threads or workers"
        }
    elif threadpool_queue > 0:
        return {
            "action": "increase_max_workers",
            "reason": "Bottleneck in download task processing",
            "recommendation": "Increase ThreadPool MAX_WORKERS"
        }
    elif cpu_percent > 80:
        return {
            "action": "increase_workers",
            "reason": "High CPU utilization",
            "recommendation": "Increase Gunicorn workers to improve CPU utilization"
        }
    else:
        return {
            "action": "none",
            "reason": "All metrics within normal range",
            "recommendation": "Maintain current configuration"
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
@app.errorhandler(403)
def forbidden(e):
    error_id = generate_error_id()
    logging.warning(f"403 Forbidden access - ID: {error_id}, IP: {get_client_ip()}, Path: {request.path}, User-Agent: {request.headers.get('User-Agent', 'Unknown')}")
    return render_template('error.html', error="You don't have permission to access this resource.", error_id=error_id), 403

@app.errorhandler(400)
def bad_request(e):
    error_id = generate_error_id()
    logging.warning(f"400 Bad request - ID: {error_id}, IP: {get_client_ip()}, Path: {request.path}, User-Agent: {request.headers.get('User-Agent', 'Unknown')}")
    return render_template('error.html', error="Invalid request.", error_id=error_id), 400

@app.errorhandler(404)
def not_found(e):
    error_id = generate_error_id()
    logging.info(f"404 Not found - ID: {error_id}, IP: {get_client_ip()}, Path: {request.path}, User-Agent: {request.headers.get('User-Agent', 'Unknown')}")
    return render_template('error.html', error="The requested resource could not be found.", error_id=error_id), 404

@app.errorhandler(429)
@app.errorhandler(RateLimitExceeded)
def ratelimit_handler(e):
    error_id = generate_error_id()
    logging.warning(f"429 Rate limit exceeded - ID: {error_id}, IP: {get_client_ip()}, Path: {request.path}, User-Agent: {request.headers.get('User-Agent', 'Unknown')}")
    return render_template('error.html', error="Too many download requests. Please try again later.", error_id=error_id), 429

@app.errorhandler(Exception)
def handle_unexpected_error(e):
    error_id = generate_error_id()

    # 에러 유형에 따라 사용자 메시지 정의
    user_message = "An unexpected error occurred. Please try again later."

    # 실제 에러 정보는 로그에만 기록
    logging.error(f"Unexpected error - ID: {error_id}, Type: {type(e).__name__}, Message: {str(e)}, IP: {get_client_ip()}, Path: {request.path}, Method: {request.method}, User-Agent: {request.headers.get('User-Agent', 'Unknown')}", exc_info=True)

    return render_template('error.html', error=user_message, error_id=error_id), 500

def generate_error_id():
    """고유한 에러 추적 ID를 생성합니다."""
    return f"{int(time.time())}-{uuid.uuid4().hex[:8]}"

def init_app():
    global executor
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    # 다운로드 통계 파일 초기화
    try:
        # 통계 파일이 없으면 초기 파일 생성
        if not os.path.exists(DOWNLOAD_STATS_FILE):
            initial_stats = {
                'total': 0,
                'completed': 0,
                'errors': 0,
                'last_updated': datetime.now().isoformat()
            }
            save_download_stats(initial_stats)
            logging.warning(f"다운로드 통계 파일 초기화: {DOWNLOAD_STATS_FILE}")
        else:
            # 기존 통계 파일 로드 및 검증
            stats = load_download_stats()
            logging.warning(f"기존 다운로드 통계 로드: total={stats.get('total', 0)}, completed={stats.get('completed', 0)}, errors={stats.get('errors', 0)}")
    except Exception as e:
        logging.error(f"다운로드 통계 초기화 중 오류: {str(e)}")

    # 시작 정보 로깅 추가
    try:
        process = psutil.Process()
        cpu_count = psutil.cpu_count(logical=False) or 1  # 물리적 CPU 코어 수
        logical_cpus = psutil.cpu_count(logical=True) or 1  # 논리적 CPU 코어 수
        total_memory = round(psutil.virtual_memory().total / (1024**3), 2)  # GB 단위

        # 설정값 추출
        gunicorn_workers = int(os.environ.get('GUNICORN_WORKERS', 1))
        gunicorn_threads = int(os.environ.get('GUNICORN_THREADS', 4))

        startup_info = {
            "app_version": os.getenv('APP_VERSION', '1.0.0'),
            "system": {
                "physical_cpus": cpu_count,
                "logical_cpus": logical_cpus,
                "total_memory_gb": total_memory,
                "process_id": process.pid,
                "parent_id": process.ppid()
            },
            "config": {
                "max_workers": MAX_WORKERS,
                "gunicorn_workers": gunicorn_workers,
                "gunicorn_threads": gunicorn_threads,
                "max_file_size_mb": round(MAX_FILE_SIZE/(1024*1024), 2),
                "download_folder": DOWNLOAD_FOLDER,
                "status_max_age": STATUS_MAX_AGE,
                "download_limits": DOWNLOAD_LIMITS
            }
        }

        logging.warning(f"애플리케이션 시작 정보:")
        logging.warning(f"CPU: 물리적 {cpu_count}코어, 논리적 {logical_cpus}코어")
        logging.warning(f"메모리: {total_memory}GB")
        logging.warning(f"다운로드 워커: {MAX_WORKERS}")
        logging.warning(f"Gunicorn 워커: {gunicorn_workers}, 스레드: {gunicorn_threads}")
        logging.warning(f"최대 파일 크기: {round(MAX_FILE_SIZE/(1024*1024), 2)}MB")

    except Exception as e:
        logging.error(f"시작 정보 로깅 중 오류 발생: {str(e)}")

    # 기존 코드
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
