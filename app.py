# TODO(2025.03.23.Sun): celery를 사용하여 비동기로 다운로드 제공할 수 있게 해야할수도
from flask import Flask, render_template, request, send_file, url_for, redirect
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

# 환경 변수 로드
load_dotenv()

app = Flask(__name__)

# 다운로드 파일 저장 폴더 및 설정
DOWNLOAD_FOLDER = os.getenv('DOWNLOAD_FOLDER', 'downloads')
MAX_FILE_AGE = int(os.getenv('MAX_FILE_AGE', 14))  # 일 단위
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', 2 * 1024 * 1024 * 1024))  # 기본 2GB

# 로깅 설정
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# 요청 제한 설정 - 수정된 버전
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)
limiter.init_app(app)

if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

@app.route('/', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
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

            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'merge_output_format': 'mp4',  # 출력 형식을 mp4로 강제 지정
                'outtmpl': download_path + '/%(title)s.%(ext)s',
                'noplaylist': True,
                'retries': 5,
                'fragment_retries': 5,
                'socket_timeout': 30,
                'max_filesize': MAX_FILE_SIZE,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                },
                'postprocessors': [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mp4',
                }],
            }

            # 비디오 정보 추출
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                video_title = info.get('title', 'video')
                video_ext = info.get('ext', 'mp4')

                # 로그 기록
                logging.info(f"다운로드 성공: {video_title} ({video_url})")

                # 다운로드 결과 페이지로 리다이렉트 (파일 ID만 전달)
                return redirect(url_for('download_result', file_id=file_id, title=video_title))

        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            logging.error(f"다운로드 오류 (URL: {video_url}): {error_msg}")

            if "Connection reset by peer" in error_msg:
                return render_template('index.html', error='접근이 제한된 사이트이거나 서버 접속이 차단되었습니다. 다른 URL을 시도해보세요.')
            elif "unavailable" in error_msg.lower() or "not available" in error_msg.lower():
                return render_template('index.html', error='영상을 찾을 수 없거나 제공자에 의해 제한된 콘텐츠입니다.')
            else:
                return render_template('index.html', error=f'다운로드 중 오류가 발생했습니다: {error_msg}')
        except Exception as e:
            logging.error(f"예상치 못한 오류 (URL: {video_url}): {str(e)}", exc_info=True)
            return render_template('index.html', error=f'다운로드 중 오류가 발생했습니다: {str(e)}')

    return render_template('index.html')

@app.route('/download-result')
def download_result():
    file_id = request.args.get('file_id')
    title = request.args.get('title')

    if not file_id:
        return redirect(url_for('index'))

    # file_id의 유효성 검증 (악의적인 경로 탐색 방지)
    if not re.match(r'^[0-9a-f\-]+$', file_id):
        logging.warning(f"유효하지 않은 file_id 접근 시도: {file_id}")
        return redirect(url_for('index'))

    download_path = os.path.join(DOWNLOAD_FOLDER, file_id)

    if not os.path.exists(download_path):
        return redirect(url_for('index'))

    # 폴더 내 첫 번째 파일을 찾음
    files = os.listdir(download_path)
    if not files:
        return redirect(url_for('index'))

    return render_template('download_result.html', title=title, file_id=file_id)

@app.route('/download-file/<file_id>')
def download_file(file_id):
    # file_id의 유효성 검증
    if not re.match(r'^[0-9a-f\-]+$', file_id):
        logging.warning(f"유효하지 않은 file_id 다운로드 시도: {file_id}")
        return redirect(url_for('index'))

    download_path = os.path.join(DOWNLOAD_FOLDER, file_id)

    if not os.path.exists(download_path):
        return redirect(url_for('index'))

    # 폴더 내 첫 번째 파일을 찾음
    files = os.listdir(download_path)
    if not files:
        return redirect(url_for('index'))

    file_path = os.path.join(download_path, files[0])

    # 파일이 아니라 디렉토리인 경우 방지
    if not os.path.isfile(file_path):
        return redirect(url_for('index'))

    logging.info(f"파일 다운로드: {file_id} - {os.path.basename(file_path)}")
    return send_file(file_path, as_attachment=True)

# 오래된 파일 정리 함수
def clean_old_files():
    try:
        now = datetime.now()
        cleaned_count = 0

        for folder_name in os.listdir(DOWNLOAD_FOLDER):
            folder_path = os.path.join(DOWNLOAD_FOLDER, folder_name)
            if os.path.isdir(folder_path):
                folder_time = datetime.fromtimestamp(os.path.getctime(folder_path))
                if (now - folder_time).days > MAX_FILE_AGE:
                    try:
                        for file in os.listdir(folder_path):
                            os.remove(os.path.join(folder_path, file))
                        os.rmdir(folder_path)
                        cleaned_count += 1
                    except Exception as e:
                        logging.error(f"파일 정리 중 오류: {str(e)}")

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

# 애플리케이션 초기화 함수
def init_app():
    # 서버 시작 시 오래된 파일 정리
    clean_old_files()

    # 백그라운드 스레드로 파일 정리 예약
    cleaning_thread = threading.Thread(target=schedule_cleaning)
    cleaning_thread.daemon = True
    cleaning_thread.start()

# 애플리케이션 초기화 (Gunicorn에서 사용)
init_app()

# 개발 환경에서 직접 실행할 때만 사용
if __name__ == '__main__':
    app.run(debug=True)
    # app.run(host='0.0.0.0', port=5000, debug=False)
