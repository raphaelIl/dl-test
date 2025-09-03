"""
웹 관련 유틸리티 - IP 처리, 캐시, 언어 감지 등
"""
from flask import request
from config import LANGUAGES, CACHE_CONFIG


def get_client_ip():
    """클라이언트 IP 주소 가져오기"""
    if request.headers.get('CF-Connecting-IP'):
        return request.headers.get('CF-Connecting-IP')
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr


def get_locale():
    """현재 로케일 감지 - 쿠키 우선, 그 다음 브라우저 설정"""
    # 쿠키에서 언어 설정 확인
    cookie_lang = request.cookies.get('language')
    if cookie_lang and cookie_lang in LANGUAGES:
        return cookie_lang

    # 브라우저 언어 설정 확인 - 기본값을 영어로 설정
    browser_lang = request.accept_languages.best_match(LANGUAGES.keys())
    return browser_lang if browser_lang else 'en'


def get_browser_preferred_language():
    """브라우저의 선호 언어를 감지하되, 기본값은 영어"""
    # Accept-Language 헤더에서 언어 감지
    browser_lang = request.accept_languages.best_match(LANGUAGES.keys())
    return browser_lang if browser_lang else 'en'


def add_cache_headers(response):
    """캐시 헤더 추가"""
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


def inject_languages():
    """템플릿 컨텍스트에 언어 정보 주입"""
    return {
        'languages': LANGUAGES,
        'current_lang': get_locale()
    }
