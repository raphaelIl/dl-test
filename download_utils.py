"""
다운로드 관련 유틸리티 함수들 - 향상된 버전
"""
import os
import re
import html
import base64
import requests
import random
import yt_dlp
import time
from yt_dlp import YoutubeDL, DownloadError
from urllib.parse import urlsplit, urljoin, unquote
from config import MAX_FILE_SIZE
import logging

# 프록시 설정 - 필요시 여기에 실제 프록시 서버 추가
PROXY_LIST = [
    None,  # 프록시 없이 직접 연결 (기본)
    # 아래에 실제 프록시 서버를 추가할 수 있습니다
    # 'socks5://user:pass@host:port',
    # 'http://user:pass@host:port',
]

# 다양한 User-Agent 목록
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/127.0.0.0 Safari/537.36',
    'Mozilla/5.0 (iPad; CPU OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/127.0.0.0 Mobile/15E148 Safari/604.1',
]


def get_random_user_agent():
    """랜덤 User-Agent 반환"""
    return random.choice(USER_AGENTS)


def build_headers_for(url: str, *, ua: str | None = None, referer_mode: str = "root") -> dict:
    """
    HTTP 헤더 빌더 - 향상된 버전
    referer_mode:
      - "root": Referer = scheme://host/  (most compatible, RECOMMENDED)
      - "page": Referer = input URL       (some sites require full page referer)
    """
    u = urlsplit(url)
    origin = f"{u.scheme}://{u.netloc}"
    referer = origin + "/" if referer_mode == "root" else url
    headers = {
        "Accept": "*/*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Origin": origin,
        "Referer": referer,
    }
    if ua:
        headers["User-Agent"] = ua
    return headers


def default_user_agent():
    """기본 User-Agent 반환"""
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"


def base_ydl_opts(detail_url: str, download_dir="/app/downloads", use_cookies=False):
    """
    YoutubeDL 기본 옵션 설정
    """
    os.makedirs(download_dir, exist_ok=True)
    root = f"{urlsplit(detail_url).scheme}://{urlsplit(detail_url).netloc}/"
    opts = {
        "downloader": "m3u8:native",
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 1,
        "paths": {"home": download_dir, "temp": download_dir},
        "outtmpl": {"default": "%(title).200B.%(ext)s"},
        "default_search": "ytsearch",
        "format": "bestvideo[vcodec^=avc]+bestaudio[ext=m4a]/best[vcodec^=avc]/bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "max_filesize": MAX_FILE_SIZE,
        "nocheckcertificate": True,
        "referer": root,
    }
    return opts


def fetch_text(url: str, headers=None, timeout=10) -> str:
    """HTML 텍스트 가져오기 - 헤더 지원"""
    r = requests.get(url, headers=headers or {"Accept":"text/html,*/*;q=0.1"}, timeout=timeout)
    r.raise_for_status()
    return r.text


# 향상된 정규식 패턴들
_abs_m3u8 = re.compile(r'https?://[^\s\'">]+?\.m3u8(?:\?[^\s\'">]+)?', re.I)
_rel_m3u8 = re.compile(r'["\']([^"\']+?\.m3u8(?:\?[^"\']+)?)["\']', re.I)
_iframe   = re.compile(r'<iframe[^>]+src=["\']([^"\']+)["\']', re.I)
_atob     = re.compile(r'atob\(["\']([A-Za-z0-9+/=]{20,})["\']\)', re.I)


def find_m3u8_candidates(detail_url: str, text: str) -> list[str]:
    """
    HTML에서 m3u8 후보 URL들 찾기 - 향상된 버전 (atob 디코딩 포함)
    """
    base = detail_url
    host = urlsplit(detail_url).netloc
    cands = []

    # 0) atob(...) 안의 base64를 먼저 풀어 잠재 URL 확보
    for b64 in _atob.findall(text):
        try:
            dec = base64.b64decode(b64 + "==").decode("utf-8", "ignore")
            text += "\n" + dec
        except Exception:
            pass

    # 1) 절대 m3u8
    for u in _abs_m3u8.findall(text):
        cands.append(html.unescape(u))

    # 2) 상대 m3u8 → 절대화
    for m in _rel_m3u8.findall(text):
        if not m.startswith("http"):
            m = urljoin(base, html.unescape(m))
        cands.append(m)

    # 3) iframe 따라가서 재검색
    m = _iframe.search(text)
    if m:
        iframe_url = urljoin(base, html.unescape(m.group(1)))
        try:
            it = fetch_text(iframe_url)
            # iframe 안에도 atob(...) 있을 수 있음
            for b64 in _atob.findall(it):
                try:
                    dec = base64.b64decode(b64 + "==").decode("utf-8", "ignore")
                    it += "\n" + dec
                except Exception:
                    pass
            for u in _abs_m3u8.findall(it):
                cands.append(html.unescape(u))
            for r in _rel_m3u8.findall(it):
                r = urljoin(iframe_url, html.unescape(r)) if not r.startswith("http") else r
                cands.append(r)
        except Exception:
            pass

    # 정규화/스코어링: 페이지 호스트 ≠ CDN(예: vod.*) 우선
    seen, scored = set(), []
    for u in cands:
        u = unquote(u)
        if u in seen:
            continue
        seen.add(u)
        h = urlsplit(u).netloc.lower()
        score = 0
        score += 10 if h != host else 0
        score += 6 if ("vod." in h or "cdn" in h) else 0
        score += 3 if ("/vod-" in u or "/vod_" in u or "/kor_mov/" in u) else 0
        scored.append((score, u))
    scored.sort(reverse=True)
    return [u for _, u in scored]


def try_download_enhanced(detail_url: str, download_dir: str, *, ua: str | None = None, use_cookies=False) -> bool:
    """
    효율적인 다운로드 함수 - Docker 환경 대응 및 m3u8 실제 변환
    직접 링크 추출 시도 -> 실패 시 영상 다운로드로 fallback
    """
    from urllib.parse import urlparse

    # URL 분석으로 기본 정보 추출
    parsed = urlparse(detail_url)
    domain = parsed.netloc.lower()

    # 기본 옵션 설정
    base = base_ydl_opts(detail_url, download_dir, use_cookies)

    # 모든 사이트에 대해 실제 비디오 파일 변환 설정 적용 (다운로드 필요할 경우)
    # m3u8 파일이 아닌 실제 비디오 파일을 다운로드하도록 포맷 설정 개선
    base.update({
        # 최대 1080p로 제한하고 mp4 우선
        'format': 'best[height<=1080][ext=mp4]/best[height<=1080]/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]',
        'merge_output_format': 'mp4',
        # m3u8를 native로 처리하여 실제 비디오 파일로 변환
        'hls_prefer_native': True,
        'hls_use_mpegts': False,
        # 파일명에서 m3u8 관련 정보 제거
        'outtmpl': {'default': '%(title).150B.%(ext)s'},
        # 스트리밍 프로토콜 처리 개선
        'http_chunk_size': 10485760,  # 10MB chunks
    })

    # 도메인별 최적화된 설정
    if any(x in domain for x in ['youtube.com', 'youtu.be']):
        # YouTube는 빠른 처리 가능
        base.update({
            'socket_timeout': 20,
            'retries': 1,
            'fragment_retries': 1,
        })
    elif any(x in domain for x in ['pornhub.com', 'xhamster.com', 'xvideos.com', 'redtube.com']):
        # 성인 사이트는 특별한 우회 전략 필요 - Docker 환경 대응
        base.update({
            'socket_timeout': 120,  # 매우 긴 타임아웃
            'retries': 1,
            'fragment_retries': 1,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
                'Referer': 'https://www.pornhub.com/',
            },
            'geo_bypass': True,
            'sleep_interval': 3,  # 요청 간 더 긴 대기
            'max_sleep_interval': 8,
            'no_check_certificate': True,
        })
        # Docker 환경에서는 쿠키 사용하지 않음
        if use_cookies:
            logging.warning("Docker 환경에서 쿠키 사용 건너뛰기")
    elif any(x in domain for x in ['tiktok.com', 'instagram.com', 'facebook.com']):
        # 소셜 미디어는 CORS 및 User-Agent 중요
        base.update({
            'socket_timeout': 45,
            'retries': 1,
            'http_headers': {
                'User-Agent': ua or default_user_agent(),
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'cross-site'
            }
        })
    else:
        # 알 수 없는 사이트는 Generic Extractor부터 시작
        base.update({
            'socket_timeout': 60,
            'retries': 1,
            'force_generic_extractor': True,
        })

    # 1차: 최적화된 설정으로 한 번만 시도
    try:
        logging.info(f"스마트 다운로드 시도: {detail_url}")
        with YoutubeDL(base) as ydl:
            ydl.download([detail_url])

        # 다운로드된 파일이 m3u8인지 확인하고 실제 비디오 파일인지 검증
        downloaded_files = [f for f in os.listdir(download_dir) if os.path.isfile(os.path.join(download_dir, f))]
        if downloaded_files:
            for file in downloaded_files:
                if file.endswith('.m3u8'):
                    # m3u8 파일이 다운로드된 경우 삭제하고 실패로 처리
                    os.remove(os.path.join(download_dir, file))
                    logging.warning(f"⚠️ m3u8 파일이 다운로드됨, 삭제 후 폴백 시도")
                    raise DownloadError("Downloaded m3u8 file instead of video")
                elif any(file.endswith(ext) for ext in ['.mp4', '.webm', '.mkv', '.avi', '.mov']):
                    logging.info(f"✅ 실제 비디오 파일 다운로드 성공: {file}")
                    return True

        logging.info(f"✅ 스마트 다운로드 성공")
        return True
    except DownloadError as e:
        error_msg = str(e).lower()
        logging.warning(f"⚠️ 기본 다운로드 실패: {str(e)}")

        # 404나 접근 불가 오류는 바로 포기
        if any(x in error_msg for x in ['404', 'not found', 'unavailable', 'private', 'removed']):
            logging.warning(f"비디오 접근 불가, m3u8 폴백 건너뛰기")
            raise e
    except (ConnectionResetError, ConnectionAbortedError, OSError) as e:
        logging.warning(f"⚠️ 네트워크 연결 오류: {str(e)}")
    except Exception as e:
        logging.warning(f"⚠️ 일반 오류: {str(e)}")

    # 2차: 향상된 m3u8 폴백 - 실제 비디오 파일로 변환 (Docker 환경 대응)
    logging.info("기본 다운로드 실패, 향상된 m3u8 폴백 시도 (Docker 환경)")
    try:
        page_html = fetch_text(detail_url, timeout=30)
        m3u8s = find_m3u8_candidates(detail_url, page_html)

        if not m3u8s:
            logging.warning("m3u8 후보를 찾을 수 없음")
            raise DownloadError("No m3u8 candidates found")

        # 가장 유력한 후보들을 시도
        for i, m3u8_url in enumerate(m3u8s[:3]):  # 최대 3개까지만 시도
            logging.info(f"m3u8 후보 {i+1} 시도: {m3u8_url[:100]}...")

            enhanced_base = {
                **base,
                "socket_timeout": 90,
                "retries": 1,
                # m3u8를 실제 비디오 파일로 변환하는 설정 강화
                'hls_prefer_native': True,
                'hls_use_mpegts': False,
                'format': 'best[height<=1080][ext=mp4]/best[height<=1080]',
                'merge_output_format': 'mp4',
                # 세그먼트를 하나의 파일로 병합
                'concurrent_fragment_downloads': 4,
                'fragment_retries': 3,
                # Docker 환경에서 쿠키 관련 오류 무시
                'no_check_certificate': True,
            }
            enhanced_base.pop('force_generic_extractor', None)  # m3u8는 generic 필요없음
            enhanced_base.pop('cookiesfrombrowser', None)  # Docker에서 쿠키 제거

            try:
                with YoutubeDL(enhanced_base) as ydl:
                    ydl.download([m3u8_url])

                # 다운로드된 파일 검증
                downloaded_files = [f for f in os.listdir(download_dir) if os.path.isfile(os.path.join(download_dir, f))]
                video_files = [f for f in downloaded_files if any(f.endswith(ext) for ext in ['.mp4', '.webm', '.mkv', '.avi', '.mov'])]

                if video_files:
                    logging.info(f"✅ m3u8 폴백으로 실제 비디오 파일 다운로드 성공: {video_files[0]}")
                    # m3u8 파일이 있다면 삭제
                    for f in downloaded_files:
                        if f.endswith('.m3u8'):
                            try:
                                os.remove(os.path.join(download_dir, f))
                                logging.info(f"불필요한 m3u8 파일 삭제: {f}")
                            except:
                                pass
                    return True
                else:
                    logging.warning(f"m3u8 후보 {i+1} 실패: 실제 비디오 파일이 다운로드되지 않음")
                    continue

            except Exception as e:
                logging.warning(f"m3u8 후보 {i+1} 실패: {str(e)}")
                continue

        raise DownloadError("All m3u8 candidates failed to download actual video")

    except Exception as e:
        logging.error(f"m3u8 폴백도 실패: {str(e)}")
        raise DownloadError("Both direct and m3u8 fallback failed")


def get_video_info(url):
    """비디오 정보 가져오기"""
    with yt_dlp.YoutubeDL({'quiet': False, 'simulate': True}) as ydl:
        return ydl.extract_info(url, download=False)


def extract_direct_download_link(url):
    """
    스마트한 직접 다운로드 링크 추출 - 재시도 없이 효율적으로
    """
    from urllib.parse import urlparse

    # URL 사전 검증
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    # 직접 파일 링크인 경우 즉시 반환
    if any(url.lower().endswith(ext) for ext in ['.mp4', '.webm', '.m4v', '.avi', '.mov']):
        return {
            'url': url,
            'title': 'Direct Video File',
            'ext': url.split('.')[-1].split('?')[0],
            'source': 'direct'
        }

    # 도메인별 최적화된 설정
    ydl_opts = {
        'quiet': False,
        'format': 'best',
        'skip_download': True,
        'noplaylist': True,
        'socket_timeout': 30,
        'retries': 1,  # 재시도 최소화
        'ignoreerrors': True,
    }

    # 도메인별 특별 처리
    if any(x in domain for x in ['youtube.com', 'youtu.be']):
        # YouTube는 빠른 처리 가능
        ydl_opts['socket_timeout'] = 15
    elif any(x in domain for x in ['tiktok.com', 'instagram.com', 'facebook.com']):
        # 소셜 미디어는 User-Agent 중요
        ydl_opts.update({
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': '*/*',
            }
        })
    else:
        # 알 수 없는 사이트는 generic extractor 사용
        ydl_opts['force_generic_extractor'] = True
        ydl_opts['socket_timeout'] = 45

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            # 플레이리스트의 경우 첫 번째 항목 사용
            if 'entries' in info:
                info = info['entries'][0]

            if not info:
                return None

            # 직접 다운로드 URL 추출
            direct_url = info.get('url')
            if not direct_url:
                return None

            return {
                'url': direct_url,
                'title': info.get('title', 'video'),
                'ext': info.get('ext', 'mp4'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader'),
                'source': info.get('extractor', '').lower()
            }
    except Exception as e:
        logging.warning(f"직접 다운로드 링크 추출 실패 (재시도 없음): {str(e)}")
        return None


def validate_direct_download_link(url):
    """
    주어진 직접 다운로드 링크가 유효한지 확인합니다.
    헤더 요청으로 URL이 유효한지, 파일 크기가 제한을 초과하지 않는지 검증합니다.
    반환값:
        유효한 경우: {'valid': True, 'size': 파일_크기(바이트)}
        유효하지 않은 경우: {'valid': False, 'reason': '이유'}
    """
    try:
        headers = {
            'User-Agent': default_user_agent(),
            'Range': 'bytes=0-0'  # 첫 바이트만 요청하여 빠른 검증
        }

        # HEAD 요청으로 파일 정보 확인
        response = requests.head(url, headers=headers, timeout=10, allow_redirects=True)

        # 성공적인 응답이 아니면 GET으로 재시도
        if response.status_code != 200:
            response = requests.get(url, headers=headers, timeout=10, stream=True, allow_redirects=True)
            if response.status_code != 200 and response.status_code != 206:
                return {'valid': False, 'reason': f'상태 코드 오류: {response.status_code}'}

        # 파일 크기 확인
        size = None
        if 'Content-Length' in response.headers:
            size = int(response.headers.get('Content-Length', 0))
            if size > MAX_FILE_SIZE:
                return {'valid': False, 'reason': f'파일 크기 제한 초과: {size/(1024*1024):.1f}MB'}

        # 컨텐트 타입 확인 - 비디오 형식인지
        content_type = response.headers.get('Content-Type', '')
        if content_type and not ('video' in content_type.lower() or 'octet-stream' in content_type.lower()):
            # 잘못된 컨텐트 타입이지만, URL이 m3u8이나 mp4로 끝나면 유효하다고 간주
            if not (url.lower().endswith('.mp4') or url.lower().endswith('.m3u8')):
                return {'valid': False, 'reason': f'잘못된 컨텐트 타입: {content_type}'}

        return {'valid': True, 'size': size, 'content_type': content_type}

    except Exception as e:
        return {'valid': False, 'reason': f'유효성 검증 중 오류: {str(e)}'}
