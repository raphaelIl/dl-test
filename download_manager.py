"""
다운로드 매니저 - 다운로드 프로세스 관리 (수정된 버전)
"""
import os
import gc
import logging
import shutil
import yt_dlp
from datetime import datetime
from flask_babel import gettext as _

from download_utils import try_download_enhanced, get_video_info, extract_direct_download_link, validate_direct_download_link
from utils import safely_access_files, generate_error_id, safe_path_join, readable_size
from stats import update_download_stats


def detect_url_type_and_strategy(video_url):
    """URL 타입을 분석하여 최적의 추출 전략을 결정"""
    from urllib.parse import urlparse

    parsed = urlparse(video_url)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()

    # 도메인별 최적 전략 결정
    strategies = {
        'direct_file': False,
        'needs_generic': False,
        'has_cors_issues': False,
        'needs_stealth': False,
        'extractor_preference': None,
        'timeout_settings': 'normal'
    }

    # 직접 파일 링크 감지
    if any(path.endswith(ext) for ext in ['.mp4', '.webm', '.m4v', '.avi', '.mov']):
        strategies['direct_file'] = True
        return strategies

    # 도메인별 특성 분석
    if any(x in domain for x in ['youtube.com', 'youtu.be']):
        strategies['extractor_preference'] = 'youtube'
        strategies['timeout_settings'] = 'short'
    elif any(x in domain for x in ['tiktok.com', 'douyin.com']):
        strategies['extractor_preference'] = 'tiktok'
        strategies['has_cors_issues'] = True
    elif any(x in domain for x in ['instagram.com', 'facebook.com', 'fb.watch']):
        strategies['extractor_preference'] = 'instagram'
        strategies['has_cors_issues'] = True
        strategies['timeout_settings'] = 'long'
    elif any(x in domain for x in ['twitter.com', 'x.com']):
        strategies['extractor_preference'] = 'twitter'
    elif any(x in domain for x in ['vimeo.com']):
        strategies['extractor_preference'] = 'vimeo'
    elif any(x in domain for x in ['dailymotion.com']):
        strategies['extractor_preference'] = 'dailymotion'
    elif any(x in domain for x in ['pornhub.com', 'xhamster.com', 'xvideos.com', 'redtube.com']):
        # 성인 사이트는 특별한 우회 전략 필요
        strategies['extractor_preference'] = 'adult_site'
        strategies['has_cors_issues'] = True
        strategies['needs_stealth'] = True
        strategies['timeout_settings'] = 'long'
    else:
        # 알 수 없는 사이트는 generic extractor 사용
        strategies['needs_generic'] = True
        strategies['timeout_settings'] = 'long'

    return strategies


def extract_streaming_urls(video_url):
    """스트리밍 URL을 추출하는 함수 - 브라우저 직접 재생 우선, 강화된 우회 기능 추가"""
    from download_utils import get_random_user_agent, PROXY_LIST
    import random
    import time

    # 1. URL 타입 분석으로 최적 전략 결정
    strategy = detect_url_type_and_strategy(video_url)

    # 직접 파일 링크인 경우 즉시 반환
    if strategy['direct_file']:
        logging.info(f"🎯 직접 파일 링크 감지: {video_url}")
        return {
            'title': 'Direct Video File',
            'streaming_urls': [{
                'url': video_url,
                'format_id': 'direct',
                'quality': 720,
                'ext': video_url.split('.')[-1].split('?')[0],
                'type': 'direct_file',
                'priority': 1
            }],
            'best_url': video_url,
            'best_quality': 720,
            'best_ext': video_url.split('.')[-1].split('?')[0]
        }

    # 2. 전략에 따른 yt-dlp 옵션 설정
    timeout_map = {
        'short': 15,
        'normal': 30,
        'long': 60
    }

    # 성인 사이트인 경우 특별 처리 (추가 우회 기법)
    is_adult_site = False
    if strategy.get('extractor_preference') == 'adult_site':
        is_adult_site = True
        # 첫 시도를 위한 사용자 에이전트 설정 (랜덤)
        user_agent = get_random_user_agent()
    else:
        # 일반적인 사이트
        user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36'

    # 기본 옵션
    ydl_opts = {
        'quiet': False,
        'no_warnings': True,
        'extract_flat': False,
        'writesubtitles': False,
        'writeautomaticsub': False,
        'writedescription': False,
        'writeinfojson': False,
        'writethumbnail': False,
        'ignoreerrors': True,
        'no_check_certificate': True,
        'socket_timeout': timeout_map[strategy['timeout_settings']],
        'retries': 1,
        'fragment_retries': 1,
        'extractor_retries': 1,
        'file_access_retries': 1,
        # 브라우저 재생 가능한 포맷 우선
        'format': 'best[height<=1080][ext=mp4]/best[height<=1080]/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]',
        'http_headers': {
            'User-Agent': user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-User': '?1',
        }
    }

    # 성인 사이트를 위한 특별한 우회 전략 - 쿠키 완전 제거
    if strategy.get('needs_stealth'):
        ydl_opts.update({
            'socket_timeout': 120,  # 매우 긴 타임아웃
            'geo_bypass': True,
            'sleep_interval': 3,
            'max_sleep_interval': 8,
            'prefer_insecure': False,
            'no_check_certificate': True,
        })

        # 쿠키 로직 완전 제거
        logging.info(f"🔒 성인 사이트 스텔스 모드 (쿠키 없음): {video_url}")
    # CORS 문제가 있는 사이트 처리
    elif strategy['has_cors_issues']:
        ydl_opts.update({
            'http_headers': {
                'User-Agent': get_random_user_agent(),
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'cross-site'
            }
        })
    # 알려진 사이트는 generic extractor 강제 사용으로 시작
    elif strategy['needs_generic']:
        ydl_opts['force_generic_extractor'] = True
        logging.info(f"🔍 알 수 없는 사이트, Generic Extractor 사용: {video_url}")
    elif strategy['extractor_preference']:
        logging.info(f"🎯 {strategy['extractor_preference']} 사이트 감지: {video_url}")

    # 일반 시도
    max_attempts = 1
    if is_adult_site:
        max_attempts = 3  # 성인 사이트는 최대 3번 시도

    for attempt in range(max_attempts):
        try:
            # 추가 시도는 지연과 다른 사용자 에이전트 사용
            if attempt > 0:
                # 시도 사이에 대기
                delay = random.uniform(2.0, 4.0)
                logging.info(f"🕒 {attempt+1}번째 시도를 위해 {delay:.1f}초 대기 중...")
                time.sleep(delay)

                # 새로운 사용자 에이전트 선택
                new_user_agent = get_random_user_agent()
                ydl_opts['http_headers']['User-Agent'] = new_user_agent
                logging.info(f"🔄 새 User-Agent로 재시도: {new_user_agent[:30]}...")

                # 랜덤하게 프록시 사용 (있는 경우)
                if len(PROXY_LIST) > 1 and random.random() < 0.7:  # 70% 확률로 프록시 교체
                    proxy = random.choice(PROXY_LIST[1:]) if len(PROXY_LIST) > 1 else None
                    if proxy:
                        ydl_opts['proxy'] = proxy
                        logging.info(f"🌐 프록시 사용: {proxy}")

            logging.info(f"🎬 스마트 전략으로 비디오 정보 추출 시도 {attempt+1}/{max_attempts}: {video_url}")

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)

                if not info:
                    logging.warning(f"❌ 비디오 정보 추출 실패: {video_url}")
                    continue  # 다음 시도로

                logging.info(f"✅ 비디오 정보 추출 성공: {info.get('title', 'Unknown')}")

                # 포맷 정보 확인
                formats = info.get('formats', [])
                if not formats:
                    logging.warning(f"❌ 포맷 정보 없음: {video_url}")
                    continue  # 다음 시도로

                logging.info(f"📋 {len(formats)}개의 포맷 발견")

                # 브라우저 직접 재생 가능한 URL 수집 (m3u8 제외)
                direct_playable_urls = []

                # 1차: mp4 비디오+오디오 통합 포맷 (최우선) - m3u8 제외
                for i, fmt in enumerate(formats):
                    url = fmt.get('url', '')
                    ext = fmt.get('ext', '')
                    vcodec = fmt.get('vcodec', 'none')
                    acodec = fmt.get('acodec', 'none')
                    height = fmt.get('height', 0)
                    protocol = fmt.get('protocol', '')

                    # m3u8 및 dash 프로토콜 제외
                    if (url and ext == 'mp4' and
                        vcodec and vcodec != 'none' and
                        acodec and acodec != 'none' and
                        height > 0 and height <= 1080 and  # 최대 1080p
                        not any(x in url.lower() for x in ['m3u8', 'dash', '.mpd']) and
                        protocol not in ['m3u8', 'm3u8_native', 'hls']):

                        direct_playable_urls.append({
                            'url': url,
                            'format_id': fmt.get('format_id', f'mp4_{i}'),
                            'quality': height,
                            'ext': ext,
                            'filesize': fmt.get('filesize'),
                            'type': 'video_audio_mp4',
                            'vcodec': vcodec,
                            'acodec': acodec,
                            'priority': 1
                        })

                # 2차: 기타 브라우저 재생 가능한 포맷 (webm 포함) - m3u8 제외
                if not direct_playable_urls:
                    for i, fmt in enumerate(formats):
                        url = fmt.get('url', '')
                        ext = fmt.get('ext', '')
                        vcodec = fmt.get('vcodec', 'none')
                        acodec = fmt.get('acodec', 'none')
                        height = fmt.get('height', 0)
                        protocol = fmt.get('protocol', '')

                        if (url and ext in ['webm', 'mp4'] and
                            vcodec and vcodec != 'none' and
                            acodec and acodec != 'none' and
                            height > 0 and height <= 1080 and  # 최대 1080p
                            not any(x in url.lower() for x in ['m3u8', 'dash', '.mpd']) and
                            protocol not in ['m3u8', 'm3u8_native', 'hls']):

                            direct_playable_urls.append({
                                'url': url,
                                'format_id': fmt.get('format_id', f'web_{i}'),
                                'quality': height,
                                'ext': ext,
                                'filesize': fmt.get('filesize'),
                                'type': 'video_audio_web',
                                'vcodec': vcodec,
                                'acodec': acodec,
                                'priority': 2
                            })

                # 3차: HTTP 직접 URL만 허용 (m3u8 완전 제외)
                if not direct_playable_urls:
                    for i, fmt in enumerate(formats):
                        url = fmt.get('url', '')
                        ext = fmt.get('ext', '')
                        height = fmt.get('height', 0)
                        protocol = fmt.get('protocol', '')

                        if (url and url.startswith('http') and
                            ext in ['mp4', 'webm', 'mkv'] and
                            height > 0 and height <= 1080 and  # 최대 1080p
                            not any(x in url.lower() for x in ['m3u8', 'dash', '.mpd']) and
                            protocol not in ['m3u8', 'm3u8_native', 'hls']):

                            direct_playable_urls.append({
                                'url': url,
                                'format_id': fmt.get('format_id', f'http_{i}'),
                                'quality': height,
                                'ext': ext,
                                'filesize': fmt.get('filesize'),
                                'type': 'http_direct',
                                'priority': 3
                            })

                if not direct_playable_urls:
                    logging.warning(f"❌ 브라우저 직접 재생 가능한 URL이 없음 (m3u8 제외): {video_url}")
                    continue  # 다음 시도로

                # 우선순위와 품질별로 정렬
                direct_playable_urls.sort(key=lambda x: (x.get('priority', 999), -x.get('quality', 0)))

                best_format = direct_playable_urls[0]
                result = {
                    'title': info.get('title', 'Unknown Title'),
                    'thumbnail': info.get('thumbnail'),
                    'duration': info.get('duration'),
                    'uploader': info.get('uploader'),
                    'description': info.get('description'),
                    'view_count': info.get('view_count'),
                    'upload_date': info.get('upload_date'),
                    'streaming_urls': direct_playable_urls,
                    'best_url': best_format['url'],
                    'best_quality': best_format['quality'],
                    'best_ext': best_format['ext']
                }

                logging.info(f"✅ 스마트 전략 성공! (시도 {attempt+1}/{max_attempts})")
                logging.info(f"   📺 제목: {result['title']}")
                logging.info(f"   🎬 최고 품질: {result['best_quality']}p ({result['best_ext']})")
                logging.info(f"   📋 총 {len(direct_playable_urls)}개 포맷 (m3u8 제외)")

                return result

        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e).lower()
            if any(x in error_msg for x in ['404', 'not found', 'unavailable', 'private', 'removed']):
                logging.warning(f"⚠️ 비디오 접근 불가 또는 삭제됨: {video_url}")
                if attempt == max_attempts - 1:  # 마지막 시도에서만 None 반환
                    return None
            else:
                logging.error(f"❌ yt-dlp 다운로드 오류 (시도 {attempt+1}): {video_url} - {str(e)}")
                if "connection reset by peer" in error_msg and attempt < max_attempts - 1:
                    logging.info(f"⚠️ 연결 재설정 오류, 재시도 준비 중...")
                    continue  # 연결 재설정 오류는 다시 시도
                elif attempt == max_attempts - 1:  # 마지막 시도에서만 None 반환
                    return None
        except (ConnectionResetError, ConnectionAbortedError, OSError) as e:
            logging.error(f"❌ 네트워크 연결 오류 (시도 {attempt+1}): {video_url} - {str(e)}")
            if attempt < max_attempts - 1:
                continue  # 네트워크 오류는 다시 시도
        except Exception as e:
            logging.error(f"❌ 스트리밍 URL 추출 중 오류 (시도 {attempt+1}): {video_url} - {str(e)}")
            if attempt < max_attempts - 1:
                continue  # 일반 오류도 다시 시도

    # 모든 시도 실패 시
    logging.error(f"❌ 모든 시도 ({max_attempts}회) 실패: {video_url}")
    return None


def update_status_completed(file_id, update_status_callback, video_url, title, is_direct_link=False, direct_url=None, streaming_info=None, **extra_info):
    """완료 상태 업데이트 로직 통합 - 스트리밍 정보 추가"""
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

    # 스트리밍 정보 추가
    if streaming_info:
        status_data['streaming_info'] = streaming_info

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
    """메인 다운로드 함수 - 스트리밍 우선, 서버 다운로드 fallback"""
    server_download_success = False  # 서버 다운로드 성공 여부 추적

    try:
        update_status_callback(file_id, {'status': 'processing', 'progress': 10})

        # 1. 스트리밍 URL 추출 시도 (주요 방식)
        logging.info(f"🎬 스트리밍 URL 추출 시도: {video_url}")
        streaming_info = extract_streaming_urls(video_url)

        if streaming_info and streaming_info.get('best_url'):
            logging.info(f"✅ 스트리밍 URL 추출 성공, 서버 다운로드 없이 완료")

            # 스트리밍 정보로 완료 처리 (서버 다운로드 없이)
            update_status_completed(
                file_id,
                update_status_callback,
                video_url,
                streaming_info['title'],
                is_direct_link=False,
                streaming_info=streaming_info,
                thumbnail=streaming_info.get('thumbnail'),
                duration=streaming_info.get('duration'),
                uploader=streaming_info.get('uploader')
            )
            return

        # 2. 직접 다운로드 링크 시도 (백업 방식)
        logging.info(f"🔗 스트리밍 실패, 직접 링크 시도: {video_url}")
        try:
            direct_link_info = extract_direct_download_link(video_url)

            if direct_link_info:
                direct_url = direct_link_info['url']
                validation_result = validate_direct_download_link(direct_url)

                if validation_result['valid']:
                    logging.info(f"✅ 직접 다운로드 링크 유효성 검증 성공")

                    update_status_completed(
                        file_id,
                        update_status_callback,
                        video_url,
                        direct_link_info.get('title', 'Direct Link Video'),
                        is_direct_link=True,
                        direct_url=direct_url,
                        thumbnail=direct_link_info.get('thumbnail'),
                        duration=direct_link_info.get('duration'),
                        uploader=direct_link_info.get('uploader')
                    )
                    return
        except Exception as e:
            logging.warning(f"직접 링크 추출 실패: {e}")

        # 3. 서버 다운로드 시도 (fallback 방식)
        logging.info(f"⚠️ 스트리밍/직접링크 실패, 서버 다운로드 시도: {video_url}")
        update_status_callback(file_id, {'status': 'downloading', 'progress': 30})

        try:
            # 향상된 다운로드 시도 (download_utils.py의 함수 사용)
            download_success = try_download_enhanced(video_url, download_path, use_cookies=True)

            if download_success:
                logging.info(f"✅ 서버 다운로드 성공: {video_url}")
                server_download_success = True  # 성공 플래그 설정

                # 다운로드된 파일 확인
                files = safely_access_files(download_path)
                if files:
                    file_name = files[0]
                    file_path = safe_path_join(download_path, file_name)
                    if os.path.isfile(file_path):
                        file_size = readable_size(os.path.getsize(file_path))

                        # 기본 비디오 정보 추출 시도
                        title = "Downloaded Video"
                        thumbnail = None
                        duration = None
                        uploader = None

                        try:
                            video_info = get_video_info(video_url)
                            if video_info:
                                title = video_info.get('title', title)
                                thumbnail = video_info.get('thumbnail')
                                duration = video_info.get('duration')
                                uploader = video_info.get('uploader')
                        except Exception as e:
                            logging.warning(f"서버 다운로드 후 메타데이터 추출 실패: {e}")

                        update_status_completed(
                            file_id,
                            update_status_callback,
                            video_url,
                            title,
                            is_direct_link=False,
                            file_name=file_name,
                            file_size=file_size,
                            thumbnail=thumbnail,
                            duration=duration,
                            uploader=uploader
                        )
                        return

        except Exception as e:
            logging.warning(f"서버 다운로드도 실패: {e}")

        # 4. 모든 방법 실패 - 최소한의 정보로 완료 처리 (서버 다운로드 없음)
        logging.warning(f"⚠️ 모든 다운로드 방법 실패, 원본 URL만 제공: {video_url}")

        # 기본 비디오 정보라도 가져오기 시도
        title = "Video"
        thumbnail = None
        try:
            video_info = get_video_info(video_url)
            if video_info:
                title = video_info.get('title', title)
                thumbnail = video_info.get('thumbnail')
        except Exception as e:
            logging.warning(f"기본 정보 추출도 실패: {e}")

        # 원본 URL만으로 완료 처리 (사용자가 원본 사이트로 이동하게 됨)
        update_status_completed(
            file_id,
            update_status_callback,
            video_url,
            title,
            is_direct_link=False,
            thumbnail=thumbnail,
            # 원본 URL을 저장하여 나중에 리다이렉트에 사용
            original_url=video_url
        )

    except Exception as e:
        # 최상위 예외 처리
        handle_download_error(file_id, update_status_callback, video_url, download_path, e)

    finally:
        # 서버 다운로드가 성공한 경우에는 파일을 보존
        if not server_download_success:
            try:
                if os.path.exists(download_path):
                    shutil.rmtree(download_path, ignore_errors=True)
                    logging.info(f"다운로드 폴더 정리 완료: {download_path}")
            except Exception:
                pass
        else:
            logging.info(f"서버 다운로드 성공으로 파일 보존: {download_path}")

        # 메모리 정리
        gc.collect()
