"""
Flask 애플리케이션 메인 파일 - 단일 URL 구조 버전 (스트리밍 우선)
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


# 공통 유틸리티 함수
def render_error(error_message, status_code=400):
    """에러 응답 통합 함수"""
    error_id = generate_error_id()
    logging.warning(f"{status_code} 에러 - ID: {error_id}, IP: {get_client_ip()}, Path: {request.path}, 메시지: {error_message}")
    return render_template('error.html', error=error_message, error_id=error_id), status_code


def check_valid_file_id(file_id):
    """유효한 파일 ID인지 검사"""
    return re.match(r'^[0-9a-f\-]+$', file_id) is not None


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

    # 리퍼러에서 돌아갈 페이지 결정
    referrer = request.referrer
    next_url = url_for('index')

    # 리퍼러가 있으면 URL 분석
    if referrer:
        host_url = request.host_url.rstrip('/')
        if referrer.startswith(host_url):
            # 호스트 URL 제거하여 경로만 추출
            path = referrer[len(host_url):]

            # 경로 분석
            if '/result/' in path:
                match = re.search(r'/result/([0-9a-f\-]+)', path)
                if match:
                    file_id = match.group(1)
                    next_url = url_for('result', file_id=file_id, _t=datetime.now().timestamp())

            elif '/download-waiting/' in path:
                match = re.search(r'/download-waiting/([0-9a-f\-]+)', path)
                if match:
                    file_id = match.group(1)
                    next_url = url_for('download_waiting', file_id=file_id)

    # 언어 쿠키 설정 및 리디렉션
    response = make_response(redirect(next_url))
    response.set_cookie('language', language, max_age=30*24*60*60)  # 30일
    return response


@app.route('/download-waiting/<file_id>')
def download_waiting(file_id):
    """다운로드 대기 페이지"""
    if not check_valid_file_id(file_id):
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
    if not check_valid_file_id(file_id):
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
    """다운로드 결과 페이지 - 안정성 우선으로 단순화"""
    if not check_valid_file_id(file_id):
        logging.warning(f"유효하지 않은 file_id 접근 시도: {file_id}")
        return redirect(url_for('index'))

    status = get_status(file_id)
    if not status or status.get('status') != 'completed':
        logging.error(f"완료되지 않은 다운로드에 대한 접근: {file_id}")
        return redirect(url_for('index'))

    # 스트리밍 정보가 있는 경우 (우선순위 1)
    if status.get('streaming_info'):
        streaming_info = status.get('streaming_info')
        return render_template('download_result.html',
                              title=status.get('title', '알 수 없는 제목'),
                              file_id=file_id,
                              url=status.get('url', ''),
                              streaming_info=streaming_info,
                              has_streaming=True,
                              is_direct_link=False,
                              thumbnail=status.get('thumbnail', ''),
                              duration=status.get('duration'),
                              uploader=status.get('uploader', ''),
                              current_lang=get_locale(),
                              languages=LANGUAGES)

    # 직접 다운로드 링크가 있는 경우 (우선순위 2)
    if status.get('is_direct_link', False) and status.get('direct_url'):
        return render_template('download_result.html',
                              title=status.get('title', '알 수 없는 제목'),
                              file_id=file_id,
                              url=status.get('url', ''),
                              direct_url=status.get('direct_url', ''),
                              is_direct_link=True,
                              has_streaming=False,
                              thumbnail=status.get('thumbnail', ''),
                              duration=status.get('duration'),
                              uploader=status.get('uploader', ''),
                              source=status.get('source', ''),
                              current_lang=get_locale(),
                              languages=LANGUAGES)

    # 기존 방식: 서버에서 다운로드한 파일 (우선순위 3)
    download_path = safe_path_join(DOWNLOAD_FOLDER, file_id)

    # 파일이 없어도 에러를 표시하지 않고 기본 정보만 표시
    file_name = "video.mp4"
    file_size = "Unknown"

    if os.path.exists(download_path):
        files = safely_access_files(download_path)
        if files:
            file_name = files[0]
            file_path = safe_path_join(download_path, file_name)
            if os.path.isfile(file_path):
                file_size = readable_size(os.path.getsize(file_path))

    return render_template('download_result.html',
                           title=status.get('title', _('다운로드 완료')),
                           file_id=file_id,
                           url=status.get('url', ''),
                           file_name=file_name,
                           file_size=file_size,
                           is_direct_link=False,
                           has_streaming=False,
                           thumbnail=status.get('thumbnail', ''),
                           current_lang=get_locale(),
                           languages=LANGUAGES)


@app.route('/stream/<file_id>')
def stream_video(file_id):
    """비디오 스트리밍 엔드포인트"""
    try:
        if not check_valid_file_id(file_id):
            return render_error(_("유효하지 않은 파일 ID입니다."))

        # 상태 확인
        status = get_status(file_id)
        if not status or status.get('status') != 'completed':
            return render_error(_("다운로드가 완료되지 않았습니다."))

        # 스트리밍 정보 확인
        streaming_info = status.get('streaming_info')
        if not streaming_info or not streaming_info.get('best_url'):
            return render_error(_("스트리밍 URL을 찾을 수 없습니다."))

        # 요청된 품질 파라미터 확인
        quality = request.args.get('quality', 'best')

        # 적절한 스트리밍 URL 선택
        selected_url = streaming_info.get('best_url')

        if quality != 'best':
            try:
                quality_num = int(quality)
                for stream in streaming_info.get('streaming_urls', []):
                    if stream.get('quality') == quality_num:
                        selected_url = stream.get('url')
                        break
            except ValueError:
                pass

        if not selected_url:
            return render_error(_("요청된 품질의 스트리밍 URL을 찾을 수 없습니다."))

        # 스트리밍 URL로 리다이렉트
        logging.info(f"스트리밍 리다이렉트: {file_id} -> {selected_url}")
        return redirect(selected_url)

    except Exception as e:
        logging.error(f"스트리밍 중 오류: {str(e)}", exc_info=True)
        return render_error(_("스트리밍 중 오류가 발생했습니다"), debug_message=str(e))


@app.route('/download-file/<file_id>')
def download_file(file_id):
    """파일 다운로드 - 브라우저 직접 재생 우선, 서버 파일 fallback"""
    try:
        logging.info(f"파일 다운로드 요청: {file_id}")

        if not check_valid_file_id(file_id):
            return render_error(_("유효하지 않은 파일 ID입니다."))

        # 상태 확인
        status = get_status(file_id)
        if not status or status.get('status') != 'completed':
            return render_error(_("다운로드가 완료되지 않았습니다."))

        # 1단계: 저장된 스트리밍 정보 확인 (최우선)
        streaming_info = status.get('streaming_info')
        if streaming_info and streaming_info.get('best_url'):
            logging.info(f"저장된 스트리밍 URL로 리다이렉트: {streaming_info.get('best_url')}")
            return redirect(streaming_info.get('best_url'))

        # 2단계: 직접 다운로드 링크가 있는 경우 리다이렉트
        if status.get('is_direct_link', False) and status.get('direct_url'):
            logging.info(f"직접 다운로드 링크로 리다이렉트: {status.get('direct_url')}")
            return redirect(status.get('direct_url'))

        # 3단계: 서버에 다운로드된 파일 제공 (핵심 fallback)
        download_path = safe_path_join(DOWNLOAD_FOLDER, file_id)
        if os.path.exists(download_path):
            files = safely_access_files(download_path)
            if files:
                filename = files[0]
                file_path = safe_path_join(download_path, filename)

                if os.path.isfile(file_path):
                    logging.info(f"서버 다운로드 파일 제공: {file_path}")
                    safe_filename = f"download-{file_id}.mp4"
                    response = send_file(file_path, as_attachment=True, mimetype='video/mp4')
                    encoded_filename = quote(filename)
                    response.headers["Content-Disposition"] = f"attachment; filename=\"{safe_filename}\"; filename*=UTF-8''{encoded_filename}"
                    return response

        # 4단계: 실시간으로 스트리밍 URL 추출 재시도
        original_url = status.get('url', '')
        if original_url:
            try:
                from download_manager import extract_streaming_urls
                streaming_info = extract_streaming_urls(original_url)

                if streaming_info and streaming_info.get('best_url'):
                    logging.info(f"실시간 스트리밍 URL 추출 성공, 브라우저로 리다이렉트")
                    return redirect(streaming_info.get('best_url'))
            except Exception as e:
                logging.warning(f"실시간 스트리밍 추출 실패: {e}")

        # 5단계: 최후 수단으로 원본 URL 리다이렉트
        if original_url:
            logging.info(f"최후 수단으로 원본 URL 리다이렉트: {original_url}")
            return redirect(original_url)

        # 모든 방법 실패
        return render_error(_("다운로드된 파일이 없습니다."))

    except Exception as e:
        logging.error(f"파일 다운로드 중 오류: {str(e)}", exc_info=True)
        return render_error(_("파일 다운로드 중 오류가 발생했습니다"))


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
    return render_error("You don't have permission to access this resource.", 403)


@app.errorhandler(400)
def bad_request(e):
    return render_error("Invalid request.", 400)


@app.errorhandler(404)
def not_found(e):
    return render_error("The requested resource could not be found.", 404)


@app.errorhandler(429)
@app.errorhandler(RateLimitExceeded)
def ratelimit_handler(e):
    return render_error("Too many download requests. Please try again later.", 429)


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    return render_error("An unexpected error occurred. Please try again later.", 500)


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
