from flask import Flask, render_template, request, send_file, url_for, redirect
import yt_dlp
import os
import uuid
import re
from datetime import datetime

app = Flask(__name__)

# 다운로드 파일 저장 폴더
DOWNLOAD_FOLDER = 'downloads'
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        video_url = request.form['video_url']
        if not video_url:
            return render_template('index.html', error='URL을 입력해주세요.')

        try:
            # 고유한 파일 이름 생성
            file_id = str(uuid.uuid4())
            download_path = os.path.join(DOWNLOAD_FOLDER, file_id)

            # yt-dlp 옵션 설정
            ydl_opts = {
                'format': 'best',
                'outtmpl': download_path + '/%(title)s.%(ext)s',
                'noplaylist': True,
            }

            # 비디오 정보 추출
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                video_title = info.get('title', 'video')
                video_ext = info.get('ext', 'mp4')
                downloaded_file = os.path.join(download_path, f"{video_title}.{video_ext}")

                # 다운로드 결과 페이지로 리다이렉트
                return redirect(url_for('download_result', file_path=downloaded_file, title=video_title))

        except Exception as e:
            return render_template('index.html', error=f'다운로드 중 오류가 발생했습니다: {str(e)}')

    return render_template('index.html')

@app.route('/download-result')
def download_result():
    file_path = request.args.get('file_path')
    title = request.args.get('title')

    if not file_path or not os.path.exists(file_path):
        return redirect(url_for('index'))

    return render_template('download_result.html', title=title, file_path=file_path)

@app.route('/download-file')
def download_file():
    file_path = request.args.get('file_path')

    if not file_path or not os.path.exists(file_path):
        return redirect(url_for('index'))

    return send_file(file_path, as_attachment=True)

# 30일 이상 지난 파일 정리 함수
def clean_old_files():
    now = datetime.now()
    for folder_name in os.listdir(DOWNLOAD_FOLDER):
        folder_path = os.path.join(DOWNLOAD_FOLDER, folder_name)
        if os.path.isdir(folder_path):
            folder_time = datetime.fromtimestamp(os.path.getctime(folder_path))
            if (now - folder_time).days > 30:
                for file in os.listdir(folder_path):
                    os.remove(os.path.join(folder_path, file))
                os.rmdir(folder_path)

if __name__ == '__main__':
    # 서버 시작 시 오래된 파일 정리
    clean_old_files()
    app.run(debug=True)
