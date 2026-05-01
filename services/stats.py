"""
통계 관리 모듈 — Redis HINCRBY atomic counter
"""
import logging
from datetime import datetime

from infrastructure import redis_client

_REDIS_KEY = "dl:stats"
_DEFAULT_STATS = {
    "total": 0,
    "completed": 0,
    "errors": 0,
    "last_updated": "",
}


def load_download_stats() -> dict:
    """다운로드 통계 로드"""
    if not redis_client.is_available():
        return {**_DEFAULT_STATS, "last_updated": datetime.now().isoformat()}

    try:
        r = redis_client.get_redis()
        data = r.hgetall(_REDIS_KEY)
        if data:
            return {
                "total": int(data.get("total", 0)),
                "completed": int(data.get("completed", 0)),
                "errors": int(data.get("errors", 0)),
                "last_updated": data.get("last_updated", ""),
            }
    except Exception as e:
        logging.error(f"Redis 통계 로드 실패: {e}")
        redis_client.mark_unavailable()

    return {**_DEFAULT_STATS, "last_updated": datetime.now().isoformat()}


def update_download_stats(status: str):
    """다운로드 상태 변경 시 통계 업데이트 (Redis atomic)"""
    if not redis_client.is_available():
        return

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
    except Exception as e:
        logging.error(f"Redis 통계 업데이트 실패: {e}")
        redis_client.mark_unavailable()
