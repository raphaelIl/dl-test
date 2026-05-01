"""
웹 관련 유틸리티 - IP 처리, 캐시 등
"""
from flask import request
from config import CACHE_CONFIG


def get_client_ip():
    """클라이언트 IP 주소 가져오기"""
    if request.headers.get('CF-Connecting-IP'):
        return request.headers.get('CF-Connecting-IP')
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr


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
