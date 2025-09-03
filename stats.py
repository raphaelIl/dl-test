"""
통계 관리 모듈
"""
import os
import json
import logging
import threading
from datetime import datetime
from config import DOWNLOAD_STATS_FILE

download_stats_lock = threading.Lock()


def load_download_stats():
    """파일에서 다운로드 통계를 로드합니다."""
    try:
        if os.path.exists(DOWNLOAD_STATS_FILE):
            with open(DOWNLOAD_STATS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"다운로드 통계 로드 중 오류: {str(e)}")

    return {
        'total': 0,
        'completed': 0,
        'errors': 0,
        'last_updated': datetime.now().isoformat()
    }


def save_download_stats(stats):
    """다운로드 통계를 파일에 저장합니다."""
    try:
        with download_stats_lock:
            stats['last_updated'] = datetime.now().isoformat()
            with open(DOWNLOAD_STATS_FILE, 'w') as f:
                json.dump(stats, f, indent=2)
    except Exception as e:
        logging.error(f"다운로드 통계 저장 중 오류: {str(e)}")


def update_download_stats(status):
    """다운로드 상태 변경 시 통계를 업데이트합니다."""
    stats = load_download_stats()

    if status == 'started':
        stats['total'] += 1
    elif status == 'completed':
        stats['completed'] += 1
    elif status == 'error':
        stats['errors'] += 1

    save_download_stats(stats)
