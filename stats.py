"""
통계 관리 모듈 — Redis HINCRBY atomic counter
"""
import logging
from datetime import datetime

import redis_client

_REDIS_KEY = "dl:stats"
_DEFAULT_STATS = {
    "total": 0,
    "completed": 0,
    "errors": 0,
    "last_updated": "",
}


def load_download_stats() -> dict:
    """다운로드 통계 로드"""
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

    return {**_DEFAULT_STATS, "last_updated": datetime.now().isoformat()}


def update_download_stats(status: str):
    """다운로드 상태 변경 시 통계 업데이트 (Redis atomic)"""
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


def save_download_stats(stats: dict):
    """통계를 저장 (초기화용)"""
    try:
        r = redis_client.get_redis()
        stats["last_updated"] = datetime.now().isoformat()
        r.hset(_REDIS_KEY, mapping={
            "total": str(stats.get("total", 0)),
            "completed": str(stats.get("completed", 0)),
            "errors": str(stats.get("errors", 0)),
            "last_updated": stats["last_updated"],
        })
    except Exception as e:
        logging.error(f"Redis 통계 저장 실패: {e}")
