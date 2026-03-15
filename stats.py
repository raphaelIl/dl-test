"""
통계 관리 모듈 — Redis HINCRBY atomic counter, 파일 기반 fallback
"""
import os
import json
import logging
import threading
from datetime import datetime
from config import DOWNLOAD_STATS_FILE

_REDIS_KEY = "dl:stats"
_file_lock = threading.Lock()


def load_download_stats() -> dict:
    """다운로드 통계 로드 (Redis 우선, fallback: 파일)"""
    import redis_client

    if redis_client.is_available():
        try:
            r = redis_client.get_redis()
            data = r.hgetall(_REDIS_KEY)
            if data:
                return {
                    "total": int(data.get("total", 0)),
                    "completed": int(data.get("completed", 0)),
                    "errors": int(data.get("errors", 0)),
                    "last_updated": data.get("last_updated", datetime.now().isoformat()),
                }
        except Exception as e:
            logging.error(f"Redis 통계 로드 실패, 파일 fallback: {e}")
            redis_client.mark_unavailable()

    return _load_from_file()


def update_download_stats(status: str):
    """다운로드 상태 변경 시 통계 업데이트 (Redis atomic, fallback: 파일)"""
    import redis_client

    if redis_client.is_available():
        try:
            r = redis_client.get_redis()
            pipe = r.pipeline()
            if status == "started":
                pipe.hincrby(_REDIS_KEY, "total", 1)
            elif status == "completed":
                pipe.hincrby(_REDIS_KEY, "completed", 1)
            elif status == "error":
                pipe.hincrby(_REDIS_KEY, "errors", 1)
            pipe.hset(_REDIS_KEY, "last_updated", datetime.now().isoformat())
            pipe.execute()
            return
        except Exception as e:
            logging.error(f"Redis 통계 업데이트 실패, 파일 fallback: {e}")
            redis_client.mark_unavailable()

    _update_file(status)


def save_download_stats(stats: dict):
    """통계를 저장 (초기화/마이그레이션용)"""
    import redis_client

    if redis_client.is_available():
        try:
            r = redis_client.get_redis()
            stats["last_updated"] = datetime.now().isoformat()
            r.hset(_REDIS_KEY, mapping={
                "total": str(stats.get("total", 0)),
                "completed": str(stats.get("completed", 0)),
                "errors": str(stats.get("errors", 0)),
                "last_updated": stats["last_updated"],
            })
            return
        except Exception as e:
            logging.error(f"Redis 통계 저장 실패, 파일 fallback: {e}")
            redis_client.mark_unavailable()

    _save_to_file(stats)


# ── File-based fallback ──────────────────────────────────────────

def _load_from_file() -> dict:
    try:
        if os.path.exists(DOWNLOAD_STATS_FILE):
            with open(DOWNLOAD_STATS_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"다운로드 통계 파일 로드 중 오류: {e}")

    return {
        "total": 0,
        "completed": 0,
        "errors": 0,
        "last_updated": datetime.now().isoformat(),
    }


def _save_to_file(stats: dict):
    try:
        with _file_lock:
            stats["last_updated"] = datetime.now().isoformat()
            with open(DOWNLOAD_STATS_FILE, "w") as f:
                json.dump(stats, f, indent=2)
    except Exception as e:
        logging.error(f"다운로드 통계 파일 저장 중 오류: {e}")


def _update_file(status: str):
    stats = _load_from_file()
    if status == "started":
        stats["total"] += 1
    elif status == "completed":
        stats["completed"] += 1
    elif status == "error":
        stats["errors"] += 1
    _save_to_file(stats)
