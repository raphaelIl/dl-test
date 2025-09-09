"""
Flask 애플리케이션 메인 파일 - 단일 URL 구조 버전
"""
import os
import re
import uuid
import logging
import atexit
import psutil
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from datetime import datetime

from flask import Flask, render_template, request, send_file, url_for, redirect, abort, send_from_directory, make_response
from flask_babel import Babel, gettext as _
from flask_limiter import Limiter
from flask_limiter.errors import RateLimitExceeded
from werkzeug.middleware.proxy_fix import ProxyFix

# 분리된 모듈들 import
from config import *
from web_utils import get_client_ip, get_locale, get_browser_preferred_language, add_cache_headers, inject_languages
from utils import safe_path_join, safely_access_files, generate_error_id, check_ip_allowed, readable_size
from download_manager import download_video
from status_manager import update_status, get_status, start_cleanup_thread
from stats import load_download_stats, save_download_stats, update_download_stats

# Flask 앱 초기화
app = Flask(__name__)
app.config['BABEL_DEFAULT_LOCALE'] = 'en'
app.config['BABEL_TRANSLATION_DIRECTORIES'] = 'translations'

app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1, x_proto=1, x_host=1, x_port=1
)

# 전역 변수
executor = None

# 로깅 설정
logging.basicConfig(
    filename='logs/app.log',
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.setLevel(logging.ERROR)
app.logger.setLevel(logging.ERROR)

# Babel 초기화
babel = Babel(app)
babel.init_app(app, locale_selector=get_locale)

# 요청 제한 설정
limiter = Limiter(
    key_func=get_client_ip,
    default_limits=None,
)
limiter.init_app(app)


# Flask 라우트들
@app.after_request
def after_request(response):
    return add_cache_headers(response)


@app.route('/')
def index():
    """메인 페이지"""
    if request.method == 'GET':
        # 쿠키에 언어 설정이 없으면 브라우저 언어로 설정
        if not request.cookies.get('language'):
            preferred_lang = get_browser_preferred_language()
            response = make_response(render_template('index.html', max_file_size_gb=MAX_FILE_SIZE/(1024*1024*1024)))
            response.set_cookie('language', preferred_lang, max_age=30*24*60*60)  # 30일
            return response

    return render_template('index.html', max_file_size_gb=MAX_FILE_SIZE/(1024*1024*1024))


@app.route('/download', methods=['POST'])
def download():
    """다운로드 요청 처리"""
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

        executor.submit(download_video, video_url, file_id, download_path, update_status)
        update_download_stats('started')
        return redirect(url_for('download_waiting', file_id=file_id))

    except Exception as e:
        logging.error(f"예상치 못한 오류 (URL: {video_url}): {str(e)}", exc_info=True)
        return render_template('index.html', error=f'{_("다운로드 중 오류가 발생했습니다")}: {str(e)}')


@app.route('/set-language/<language>')
def set_language(language):
    """언어 설정"""
    if language not in LANGUAGES:
        return redirect(url_for('index'))

    response = make_response(redirect(request.referrer or url_for('index')))
    response.set_cookie('language', language, max_age=30*24*60*60)  # 30일
    return response


@app.route('/download-waiting/<file_id>')
def download_waiting(file_id):
    """다운로드 대기 페이지"""
    if not re.match(r'^[0-9a-f\-]+$', file_id):
        logging.warning(f"유효하지 않은 file_id 접근 시도: {file_id}")
        return redirect(url_for('index'))

    status = get_status(file_id)
    if status['status'] == 'completed':
        return redirect(url_for('result', file_id=file_id))

    return render_template('download_waiting.html', file_id=file_id, status=status,
                           current_lang=get_locale(), languages=LANGUAGES)


@app.route('/check-status/<file_id>')
def check_status(file_id):
    """다운로드 상태 확인"""
    if not re.match(r'^[0-9a-f\-]+$', file_id):
        logging.warning(f"유효하지 않은 file_id 상태 확인 시도: {file_id}")
        return {'status': 'error', 'error': '유효하지 않은 파일 ID'}

    status = get_status(file_id)
    if status.get('status') == 'completed':
        return {
            'status': 'completed',
            'redirect': url_for('result', file_id=file_id)
        }

    return status


@app.route('/result/<file_id>')
def result(file_id):
    """다운로드 결과 페이지"""
    if not re.match(r'^[0-9a-f\-]+$', file_id):
        logging.warning(f"유효하지 않은 file_id 접근 시도: {file_id}")
        return redirect(url_for('index'))

    status = get_status(file_id)
    if not status or status.get('status') != 'completed':
        logging.error(f"완료되지 않은 다운로드에 대한 접근: {file_id}")
        return redirect(url_for('index'))

    download_path = safe_path_join(DOWNLOAD_FOLDER, file_id)
    if not os.path.exists(download_path):
        logging.error(f"다운로드 경로를 찾을 수 없음: {download_path}")
        return render_template('index.html', error="다운로드 파일을 찾을 수 없습니다.", current_lang=get_locale(), languages=LANGUAGES)

    files = safely_access_files(download_path)
    if not files:
        logging.error(f"다운로드 폴더에 파일이 없음: {download_path}")
        return render_template('index.html', error="다운로드된 파일이 없습니다.", current_lang=get_locale(), languages=LANGUAGES)

    file_name = files[0]
    file_path = safe_path_join(download_path, file_name)
    file_size = os.path.getsize(file_path) if os.path.isfile(file_path) else 0

    return render_template('download_result.html',
                           title=status.get('title', '알 수 없는 제목'),
                           file_id=file_id,
                           url=status.get('url', ''),
                           file_name=file_name,
                           file_size=readable_size(file_size))


@app.route('/download-file/<file_id>')
def download_file(file_id):
    """파일 다운로드"""
    try:
        logging.info(f"파일 다운로드 시작: {file_id}")

        if not re.match(r'^[0-9a-f\-]+$', file_id):
            logging.warning(f"유효하지 않은 file_id 다운로드 시도: {file_id}")
            return render_template('index.html', error=_("유효하지 않은 파일 ID입니다."), current_lang=get_locale(), languages=LANGUAGES)

        download_path = safe_path_join(DOWNLOAD_FOLDER, file_id)
        if not os.path.exists(download_path):
            logging.error(f"다운로드 경로를 찾을 수 없음: {download_path}")
            return render_template('index.html', error="다운로드 파일을 찾을 수 없습니다.", current_lang=get_locale(), languages=LANGUAGES)

        files = safely_access_files(download_path)
        if not files:
            logging.error(f"다운로드 폴더에 파일이 없음: {download_path}")
            return render_template('index.html', error="다운로드된 파일이 없습니다.", current_lang=get_locale(), languages=LANGUAGES)

        filename = files[0]
        file_path = safe_path_join(download_path, filename)

        if not os.path.isfile(file_path):
            logging.error(f"파일이 아닌 경로: {file_path}")
            return render_template('index.html', error="유효하지 않은 파일입니다.", current_lang=get_locale(), languages=LANGUAGES)

        safe_filename = f"download-{file_id}.mp4"
        response = send_file(file_path, as_attachment=True, mimetype='video/mp4')
        encoded_filename = quote(filename)
        response.headers["Content-Disposition"] = f"attachment; filename=\"{safe_filename}\"; filename*=UTF-8''{encoded_filename}"
        return response

    except Exception as e:
        logging.error(f"파일 다운로드 중 오류: {str(e)}", exc_info=True)
        return render_template('index.html', error=f"파일 다운로드 중 오류가 발생했습니다: {str(e)}", current_lang=get_locale(), languages=LANGUAGES)


@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.static_folder, 'robots.txt')


@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory('static', 'sitemap.xml')

@app.route('/ads.txt')
def ads_txt():
    return send_from_directory('static', 'ads.txt')


@app.route('/health')
def health_check():
    """헬스 체크"""
    client_ip = get_client_ip()
    logging.info(f"헬스체크 요청 IP: {client_ip}")

    if not check_ip_allowed(client_ip, ALLOWED_HEALTH_IPS):
        logging.warning(f"허용되지 않은 IP({client_ip})에서 health 엔드포인트 접근 시도")
        abort(403)

    try:
        stats = load_download_stats()

        # 현재 진행 중인 다운로드 수 계산 (상태 관리자에서 가져와야 함)
        # 여기서는 간단히 0으로 설정
        in_progress = 0

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


# 에러 핸들러들
@app.errorhandler(403)
def forbidden(e):
    error_id = generate_error_id()
    logging.warning(f"403 Forbidden access - ID: {error_id}, IP: {get_client_ip()}, Path: {request.path}")
    return render_template('error.html', error="You don't have permission to access this resource.", error_id=error_id), 403


@app.errorhandler(400)
def bad_request(e):
    error_id = generate_error_id()
    logging.warning(f"400 Bad request - ID: {error_id}, IP: {get_client_ip()}, Path: {request.path}")
    return render_template('error.html', error="Invalid request.", error_id=error_id), 400


@app.errorhandler(404)
def not_found(e):
    error_id = generate_error_id()
    logging.info(f"404 Not found - ID: {error_id}, IP: {get_client_ip()}, Path: {request.path}")
    return render_template('error.html', error="The requested resource could not be found.", error_id=error_id), 404


@app.errorhandler(429)
@app.errorhandler(RateLimitExceeded)
def ratelimit_handler(e):
    error_id = generate_error_id()
    logging.warning(f"429 Rate limit exceeded - ID: {error_id}, IP: {get_client_ip()}, Path: {request.path}")
    return render_template('error.html', error="Too many download requests. Please try again later.", error_id=error_id), 429


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    error_id = generate_error_id()
    user_message = "An unexpected error occurred. Please try again later."
    logging.error(f"Unexpected error - ID: {error_id}, Type: {type(e).__name__}, Message: {str(e)}, IP: {get_client_ip()}", exc_info=True)
    return render_template('error.html', error=user_message, error_id=error_id), 500


# 컨텍스트 프로세서
@app.context_processor
def context_processor():
    return inject_languages()


# 정리 함수
def cleanup_on_exit():
    """애플리케이션 종료 시 정리"""
    if executor:
        executor.shutdown(wait=True)
    logging.warning("애플리케이션 종료: 리소스 정리 완료")


def init_app():
    """애플리케이션 초기화"""
    global executor
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    # 다운로드 통계 파일 초기화
    try:
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
            stats = load_download_stats()
            logging.warning(f"기존 다운로드 통계 로드: total={stats.get('total', 0)}, completed={stats.get('completed', 0)}, errors={stats.get('errors', 0)}")
    except Exception as e:
        logging.error(f"다운로드 통계 초기화 중 오류: {str(e)}")

    # 시작 정보 로깅
    try:
        process = psutil.Process()
        cpu_count = psutil.cpu_count(logical=False) or 1
        logical_cpus = psutil.cpu_count(logical=True) or 1
        total_memory = round(psutil.virtual_memory().total / (1024**3), 2)

        gunicorn_workers = int(os.environ.get('GUNICORN_WORKERS', 1))
        gunicorn_threads = int(os.environ.get('GUNICORN_THREADS', 4))

        logging.warning(f"애플리케이션 시작 정보:")
        logging.warning(f"CPU: 물리적 {cpu_count}코어, 논리적 {logical_cpus}코어")
        logging.warning(f"메모리: {total_memory}GB")
        logging.warning(f"다운로드 워커: {MAX_WORKERS}")
        logging.warning(f"Gunicorn 워커: {gunicorn_workers}, 스레드: {gunicorn_threads}")
        logging.warning(f"최대 파일 크기: {round(MAX_FILE_SIZE/(1024*1024), 2)}MB")

    except Exception as e:
        logging.error(f"시작 정보 로깅 중 오류 발생: {str(e)}")

    # 상태 정리 스레드 시작
    start_cleanup_thread()

    # 종료 시 정리 등록
    atexit.register(cleanup_on_exit)


# 앱 초기화
init_app()

if __name__ == '__main__':
    host = os.getenv('FLASK_HOST', '127.0.0.1')
    port = int(os.getenv('FLASK_PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'true').lower() == 'true'
    app.run(host=host, port=port, debug=debug)
