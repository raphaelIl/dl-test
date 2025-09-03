"""
다운로드 매니저 - 다운로드 프로세스 관리 (향상된 버전)
"""
import os
import gc
import logging
import shutil
import yt_dlp
from datetime import datetime
from flask_babel import gettext as _

from config import MAX_FILE_SIZE
from download_utils import try_download_enhanced, build_headers_for, get_video_info
from utils import safely_access_files, generate_error_id
from stats import update_download_stats


def download_video(video_url, file_id, download_path, update_status_callback):
    """메인 다운로드 함수 - 향상된 다운로드 로직 적용"""
    try:
        update_status_callback(file_id, {'status': 'downloading', 'progress': 0})

        def progress_hook(d):
            if d['status'] == 'downloading':
                if 'total_bytes' in d and d['total_bytes'] > 0:
                    if d['total_bytes'] > MAX_FILE_SIZE:
                        update_status_callback(file_id, {
                            'status': 'error',
                            'error': "This video is too big.",
                            'timestamp': datetime.now().timestamp()
                        })
                        return
                    progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
                elif 'total_bytes_estimate' in d and d['total_bytes_estimate'] > 0:
                    if d['total_bytes_estimate'] > MAX_FILE_SIZE:
                        update_status_callback(file_id, {
                            'status': 'error',
                            'error': f'파일 크기 제한 초과: {d["total_bytes_estimate"]/(1024*1024):.1f}MB (최대 {MAX_FILE_SIZE/(1024*1024)}MB)',
                            'timestamp': datetime.now().timestamp()
                        })
                        return
                    progress = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
                else:
                    progress = 0
                update_status_callback(file_id, {'status': 'downloading', 'progress': progress})
            elif d['status'] == 'finished':
                update_status_callback(file_id, {'status': 'processing', 'progress': 100})
            elif d['status'] == 'error':
                update_status_callback(file_id, {
                    'status': 'error',
                    'error': d.get('error', '알 수 없는 오류'),
                    'timestamp': datetime.now().timestamp()
                })

        # 향상된 다운로드 시도 사용
        try:
            default_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"

            # 1차: 향상된 다운로드 함수 사용 (강제 Referer + m3u8 구출 폴백 포함)
            logging.info(f"향상된 다운로드 시도 시작: {video_url}")
            if try_download_enhanced(video_url, download_path, ua=default_ua):
                files = safely_access_files(download_path)
                if files:
                    try:
                        info = get_video_info(video_url)
                        title = info.get('title', '알 수 없는 제목')
                    except:
                        title = files[0].rsplit('.', 1)[0] if '.' in files[0] else files[0]

                    update_status_callback(file_id, {
                        'status': 'completed',
                        'title': title,
                        'url': video_url,
                        'timestamp': datetime.now().timestamp()
                    })
                    logging.info(f"향상된 다운로드 성공: {title} ({video_url})")
                    update_download_stats('completed')
                    return {'title': title}
        except Exception as e:
            logging.warning(f"향상된 다운로드 실패, 기본 방식으로 시도: {str(e)}")

        # 2차 폴백: 기본 yt-dlp 방식 (하위 호환성)
        logging.info(f"기본 다운로드 방식으로 폴백: {video_url}")
        headers = build_headers_for(video_url, referer_mode="root")
        ydl_opts = {
            'format': 'bestvideo[vcodec^=avc]+bestaudio[ext=m4a]/best[vcodec^=avc]/bestvideo+bestaudio/best',
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
            "http_headers": headers,
            # 기본 방식에도 강제 Referer 적용
            "referer": f"{video_url.split('://')[0]}://{video_url.split('/')[2]}/",
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'progress_hooks': [progress_hook],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            update_status_callback(file_id, {
                'status': 'completed',
                'title': info.get('title', '알 수 없는 제목'),
                'url': video_url,
                'timestamp': datetime.now().timestamp()
            })
            logging.info(f"기본 다운로드 성공: {info.get('title')} ({video_url})")
            update_download_stats('completed')
            return info

    except Exception as e:
        error_msg = str(e)
        error_id = generate_error_id()

        # 상세 로그 기록
        logging.error(f"다운로드 오류 (ID: {error_id}, URL: {video_url}): {error_msg}", exc_info=True)

        # 사용자 친화적 메시지
        user_friendly_msg = _("An error occurred during video download. Please try again later.")

        # 에러 패턴별 메시지 설정 (기존과 동일)
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
        # 새로운 에러 패턴 추가
        elif "No m3u8 candidates found" in error_msg:
            user_friendly_msg = _("Could not find video stream. The site may have changed its format.")
        elif "All strategies failed" in error_msg:
            user_friendly_msg = _("Failed to download with all available methods. The video may be protected.")

        update_status_callback(file_id, {
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
