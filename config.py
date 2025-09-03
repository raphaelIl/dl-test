"""
애플리케이션 설정 관리
"""
import os
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()

# 환경 변수 설정
ALLOWED_HEALTH_IPS = os.getenv('ALLOWED_HEALTH_IPS', '127.0.0.1,125.177.83.187,172.31.0.0/16').split(',')
MAX_WORKERS = int(os.getenv('MAX_WORKERS', 3))
DOWNLOAD_FOLDER = os.getenv('DOWNLOAD_FOLDER', 'downloads')
DOWNLOAD_STATS_FILE = os.getenv('DOWNLOAD_STATS_FILE', 'download_stats.json')
STATUS_MAX_AGE = int(os.getenv('STATUS_MAX_AGE', 120))  # 2mins
STATUS_CLEANUP_INTERVAL = int(os.getenv('STATUS_CLEANUP_INTERVAL', 60))  # 1min
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE_MB', 40000)) * 1024 * 1024
DOWNLOAD_LIMITS = os.getenv('DOWNLOAD_LIMITS', "20 per hour, 100 per minute").split(',')
DOWNLOAD_LIMITS = [limit.strip() for limit in DOWNLOAD_LIMITS]

CACHE_CONFIG = {
    'css_js': os.getenv('CACHE_CSS_JS', '31536000,604800'),      # 브라우저 1년, CDN 1주일
    'media': os.getenv('CACHE_MEDIA', '31536000,31536000'),      # 브라우저/CDN 모두 1년
    'default': os.getenv('CACHE_DEFAULT', '86400,86400')         # 브라우저/CDN 모두 1일
}

# 언어 설정
LANGUAGES = {
    'en': 'English',                # 영어
    'id': 'Bahasa Indonesia',       # 인도네시아어
    'pt_BR': 'Português (Brasil)',  # 브라질 포르투갈어
    'es_MX': 'Español (México)',    # 멕시코 스페인어
    'vi': 'Tiếng Việt',             # 베트남어
    'fil': 'Filipino',              # 필리핀어(타갈로그어)
    'th': 'ไทย',                    # 태국어
    'fr': 'Français',               # 프랑스어
    'ur': 'اردو',                   # 우르두어(파키스탄)
    'ko': '한국어',                   # 한국어
    'ja': '日本語',                   # 일본어
    'zh': '中文',                    # 중국어
}

# 디렉토리 생성
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

if not os.path.exists('logs'):
    os.makedirs('logs')
