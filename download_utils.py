"""
다운로드 관련 유틸리티 함수들 - 향상된 버전
"""
import os
import re
import html
import base64
import requests
import yt_dlp
from yt_dlp import YoutubeDL, DownloadError
from urllib.parse import urlsplit, urljoin, unquote
from config import MAX_FILE_SIZE


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
        "Referer": referer,  # 헤더에도 넣되,
    }
    if ua:
        headers["User-Agent"] = ua
    return headers


def base_ydl_opts(detail_url: str, download_dir="/app/downloads", use_cookies=False):
    """
    YoutubeDL 기본 옵션 - 강제 Referer 적용
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
        # ★ m3u8 요청까지 루트 Referer가 '옵션'으로 강제 적용되도록
        "referer": root,
    }
    if use_cookies:
        opts["cookiesfrombrowser"] = ("chrome",)  # 필요할 때만
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

    # 3) 1단계 iframe 따라가서 재검색
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
    향상된 다운로드 함수 - 다단계 전략 및 m3u8 구출 폴백
    """
    base = base_ydl_opts(detail_url, download_dir, use_cookies)

    # 전역 헤더(루트 Origin/Referer)
    hdr_root = build_headers_for(detail_url, ua=None, referer_mode="root")
    hdr_page = build_headers_for(detail_url, ua=None, referer_mode="page")
    hdr_root_ua = build_headers_for(
        detail_url,
        ua=(ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"),
        referer_mode="root",
    )

    strategies = [
        {"http_headers": hdr_root},
        {"http_headers": hdr_page},
        {"http_headers": hdr_root_ua},
    ]

    last_err = None
    for s in strategies:
        try:
            with YoutubeDL({**base, **s}) as ydl:
                ydl.download([detail_url])  # 먼저 일반 경로 시도
            return True
        except DownloadError as e:
            last_err = e

    # 폴백: 진짜 m3u8을 찾아 직접 다운로드
    try:
        page_html = fetch_text(detail_url)
    except Exception as e:
        raise last_err or e

    m3u8s = find_m3u8_candidates(detail_url, page_html)
    if not m3u8s:
        raise last_err or DownloadError("No m3u8 candidates found")

    # 1차: 루트 헤더 + m3u8 직접 다운로드
    for m3u8 in m3u8s:
        try:
            with YoutubeDL({**base, "http_headers": hdr_root}) as ydl:
                ydl.download([m3u8])
            return True
        except DownloadError:
            continue

    # 2차: 쿠키/UA 확장
    base_ck = base_ydl_opts(detail_url, download_dir, use_cookies=True)
    for m3u8 in m3u8s:
        try:
            with YoutubeDL({**base_ck, "http_headers": hdr_root_ua}) as ydl:
                ydl.download([m3u8])
            return True
        except DownloadError:
            continue

    raise last_err or DownloadError("All strategies failed")


# 기존 함수들 (하위 호환성)
def _ydl_base_enhanced(download_dir: str, use_cookies: bool):
    """기존 앱과 통합된 YoutubeDL 기본 설정 - 하위 호환성"""
    return {
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
        "cookiesfrombrowser": ("chrome",) if use_cookies else None
    }


def get_video_info(url):
    """비디오 정보 가져오기"""
    with yt_dlp.YoutubeDL({'quiet': True, 'simulate': True}) as ydl:
        return ydl.extract_info(url, download=False)
