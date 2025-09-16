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
from download_utils import try_download_enhanced, build_headers_for, get_video_info, extract_direct_download_link, validate_direct_download_link, default_user_agent
from utils import safely_access_files, generate_error_id
from stats import update_download_stats


def update_status_completed(file_id, update_status_callback, video_url, title, is_direct_link=False, direct_url=None, **extra_info):
    """완료 상태 업데이트 로직 통합"""
    status_data = {
        'status': 'completed',
        'progress': 100,
        'title': title,
        'url': video_url,
        'is_direct_link': is_direct_link,
        'timestamp': datetime.now().timestamp()
    }

    # 직접 다운로드 링크인 경우 추가 정보
    if is_direct_link and direct_url:
        status_data['direct_url'] = direct_url

    # 추가 정보 통합
    for key, value in extra_info.items():
        if value is not None:
            status_data[key] = value

    update_status_callback(file_id, status_data)
    update_download_stats('completed')


def handle_download_error(file_id, update_status_callback, video_url, download_path, error):
    """에러 처리 로직 통합 - 사용자에게는 친화적인 메시지만 표시"""
    error_id = generate_error_id()

    # 기술적 오류는 로그에만 기록
    logging.error(f"Download Fail (ID: {error_id}, URL: {video_url}): {str(error)}", exc_info=True)

    # 사용자에게는 친화적인 메시지만 표시
    user_friendly_message = _("An unexpected error occurred. Please try again later.")
    error_message = f"{user_friendly_message} (Error ID: {error_id})"

    update_status_callback(file_id, {
        'status': 'error',
        'error': error_message,
        'timestamp': datetime.now().timestamp()
    })

    update_download_stats('errors')

    # 다운로드 폴더 정리
    try:
        shutil.rmtree(download_path, ignore_errors=True)
    except Exception:
        pass


def download_video(video_url, file_id, download_path, update_status_callback):
    """메인 다운로드 함수 - 향상된 다운로드 로직 적용"""
    try:
        update_status_callback(file_id, {'status': 'downloading', 'progress': 0})

        # 1. 먼저 직접 다운로드 링크 추출 시도
        direct_link_info = extract_direct_download_link(video_url)

        # 직접 다운로드 링크가 추출되었으면 유효성 검증
        if direct_link_info:
            direct_url = direct_link_info['url']
            validation_result = validate_direct_download_link(direct_url)

            # 유효한 직접 다운로드 링크인 경우
            if validation_result['valid']:
                logging.info(f"직접 다운로드 링크 발견: {video_url} -> {direct_url}")

                # 상태 업데이트 (직접 다운로드 링크 사용)
                update_status_completed(
                    file_id,
                    update_status_callback,
                    video_url,
                    direct_link_info['title'],
                    is_direct_link=True,
                    direct_url=direct_url,
                    thumbnail=direct_link_info.get('thumbnail'),
                    duration=direct_link_info.get('duration'),
                    uploader=direct_link_info.get('uploader'),
                    source=direct_link_info.get('source')
                )
                return

        # 2. 직접 다운로드 링크가 없거나 유효하지 않으면 기존 방식으로 다운로드
        logging.info(f"직접 다운로드 링크를 사용할 수 없음. 기존 방식으로 다운로드: {video_url}")

        def progress_hook(d):
            if d['status'] == 'downloading':
                if 'total_bytes' in d and d['total_bytes'] > 0:
                    if d['total_bytes'] > MAX_FILE_SIZE:
                        error_id = generate_error_id()
                        logging.warning(f"파일 크기 제한 초과 (ID: {error_id}): {d['total_bytes']/(1024*1024):.1f}MB")
                        update_status_callback(file_id, {
                            'status': 'error',
                            'error': _("파일 크기가 제한을 초과했습니다."),
                            'timestamp': datetime.now().timestamp()
                        })
                        return
                    progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
                elif 'total_bytes_estimate' in d and d['total_bytes_estimate'] > 0:
                    if d['total_bytes_estimate'] > MAX_FILE_SIZE:
                        error_id = generate_error_id()
                        logging.warning(f"파일 크기 제한 초과 (ID: {error_id}): {d['total_bytes_estimate']/(1024*1024):.1f}MB")
                        update_status_callback(file_id, {
                            'status': 'error',
                            'error': _("파일 크기가 제한을 초과했습니다."),
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
                error_id = generate_error_id()
                error_detail = d.get('error', '알 수 없는 오류')
                logging.error(f"다운로드 진행 중 오류 (ID: {error_id}): {error_detail}")
                update_status_callback(file_id, {
                    'status': 'error',
                    'error': _('다운로드 중 오류가 발생했습니다'),
                    'timestamp': datetime.now().timestamp()
                })

        # 다운로드 옵션 설정
        ydl_opts = {
            # 'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'format': 'bestvideo[vcodec^=avc]+bestaudio[ext=m4a]/best[vcodec^=avc]/bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            'outtmpl': os.path.join(download_path, '%(title)s.%(ext)s'),
            'quiet': False,
            'noprogress': True,
            'retries': 10,
            'fragment_retries': 10,
            'progress_hooks': [progress_hook],
            'max_filesize': MAX_FILE_SIZE,
            'http_headers': {
                'User-Agent': default_user_agent()
            }
        }

        # 비디오 정보 가져오기
        video_info = None  # 기본값 설정
        try:
            video_info = get_video_info(video_url)
            title = video_info.get('title', _('Unknown Title'))
        except Exception:
            title = _('Unknown Title')

        try:
            # yt-dlp로 다운로드 시도
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

            # 다운로드된 파일 확인
            files = safely_access_files(download_path)
            if not files:
                raise Exception(_("다운로드된 파일이 없습니다."))

            # 상태 업데이트
            thumbnail = None
            if video_info:
                thumbnail = video_info.get('thumbnail')

            update_status_completed(
                file_id,
                update_status_callback,
                video_url,
                title,
                is_direct_link=False,
                thumbnail=thumbnail
            )

        except Exception as e:
            # 향상된 다운로드 방식 시도
            try:
                try_download_enhanced(video_url, download_path)

                # 다운로드된 파일 확인
                files = safely_access_files(download_path)
                if not files:
                    raise Exception(_("다운로드된 파일이 없습니다."))

                # 상태 업데이트
                thumbnail = None
                if video_info:
                    thumbnail = video_info.get('thumbnail')

                update_status_completed(
                    file_id,
                    update_status_callback,
                    video_url,
                    title,
                    is_direct_link=False,
                    thumbnail=thumbnail
                )

            except Exception as enhanced_error:
                # 모든 다운로드 방식 실패
                handle_download_error(file_id, update_status_callback, video_url, download_path, enhanced_error)

    except Exception as e:
        # 최상위 예외 처리
        handle_download_error(file_id, update_status_callback, video_url, download_path, e)

    finally:
        # 메모리 정리
        gc.collect()
