"""
Redis 연결 관리 모듈 — 싱글톤 연결 풀 + fallback 플래그
"""
import logging
import threading

import redis

from config import REDIS_URL

_pool = None
_redis = None
_lock = threading.Lock()
_available = True


def _init_pool():
    """ConnectionPool 초기화 (lazy singleton)"""
    global _pool, _redis
    if _pool is not None:
        return
    _pool = redis.ConnectionPool.from_url(
        REDIS_URL,
        decode_responses=True,
        max_connections=10,
        socket_timeout=1,
        socket_connect_timeout=1,
        retry_on_timeout=False,
    )
    _redis = redis.Redis(connection_pool=_pool)


def get_redis() -> redis.Redis:
    """Redis 인스턴스 반환 (연결 풀 공유)"""
    with _lock:
        _init_pool()
    return _redis


def is_available() -> bool:
    return _available


def mark_unavailable():
    global _available
    _available = False
    logging.warning("Redis 사용 불가 상태로 전환 — fallback 모드")


def mark_available():
    global _available
    if not _available:
        _available = True
        logging.warning("Redis 복구 감지 — Redis 모드로 전환")


def check_health() -> bool:
    """PING 으로 Redis 연결 확인, 결과에 따라 플래그 갱신"""
    try:
        r = get_redis()
        r.ping()
        mark_available()
        return True
    except Exception as e:
        logging.warning(f"Redis 헬스체크 실패: {e}")
        mark_unavailable()
        return False
