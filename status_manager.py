"""
상태 관리 모듈 — Redis JSON + Lua atomic merge, in-memory fallback
"""
import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime

import redis

import redis_client
from config import STATUS_MAX_AGE, STATUS_CLEANUP_INTERVAL, DOWNLOAD_FOLDER
from utils import safe_path_join

# ── Redis Lua Script (atomic merge + SETEX) ──────────────────────
_LUA_MERGE = """
local current = redis.call('GET', KEYS[1])
local data = current and cjson.decode(current) or {}
local updates = cjson.decode(ARGV[1])
for k, v in pairs(updates) do data[k] = v end
redis.call('SETEX', KEYS[1], tonumber(ARGV[2]), cjson.encode(data))
return 'OK'
"""

_KEY_PREFIX = "dl:status:"
_merge_sha = None  # EVALSHA 용 캐시

# ── Fallback (in-memory) ─────────────────────────────────────────
_fallback_lock = threading.Lock()
_fallback_store: dict[str, dict] = {}


def _ttl_for(status_data: dict) -> int:
    """상태에 따라 TTL 결정"""
    s = status_data.get("status", "")
    if s in ("processing", "downloading"):
        return 1800  # 30분 — 대용량 파일 대응
    return STATUS_MAX_AGE  # completed/error → 환경변수 (기본 1800초)


def _eval_merge(r, key: str, payload: str, ttl: str):
    """Lua Script 실행 — NOSCRIPT 시 자동 재로드"""
    global _merge_sha
    if _merge_sha is None:
        _merge_sha = r.script_load(_LUA_MERGE)
    try:
        return r.evalsha(_merge_sha, 1, key, payload, ttl)
    except redis.exceptions.NoScriptError:
        _merge_sha = r.script_load(_LUA_MERGE)
        return r.evalsha(_merge_sha, 1, key, payload, ttl)


# ── Public API (인터페이스 100% 유지) ────────────────────────────

def update_status(file_id: str, status_data: dict):
    """다운로드 상태 업데이트 (기존 상태와 병합)"""
    if redis_client.is_available():
        try:
            r = redis_client.get_redis()
            key = f"{_KEY_PREFIX}{file_id}"
            ttl = _ttl_for(status_data)
            payload = json.dumps(status_data, ensure_ascii=False)
            _eval_merge(r, key, payload, str(ttl))
            return
        except Exception as e:
            logging.error(f"Redis update_status 실패, fallback 전환: {e}")
            redis_client.mark_unavailable()

    # fallback: in-memory
    _fallback_update(file_id, status_data)


def get_status(file_id: str) -> dict:
    """다운로드 상태 조회"""
    if redis_client.is_available():
        try:
            r = redis_client.get_redis()
            raw = r.get(f"{_KEY_PREFIX}{file_id}")
            if raw:
                return json.loads(raw)
            # Redis에 없으면 fallback store도 확인 (전환 직후)
            with _fallback_lock:
                if file_id in _fallback_store:
                    return _fallback_store[file_id].copy()
            return {"status": "unknown"}
        except Exception as e:
            logging.error(f"Redis get_status 실패, fallback 전환: {e}")
            redis_client.mark_unavailable()

    # fallback: in-memory
    return _fallback_get(file_id)


def start_cleanup_thread():
    """상태 정리 스레드 시작"""
    t = threading.Thread(target=_cleanup_loop, daemon=True)
    t.start()
    logging.info("상태 정리 스레드 시작됨")


# ── Fallback helpers ─────────────────────────────────────────────

def _fallback_update(file_id: str, status_data: dict):
    with _fallback_lock:
        if file_id in _fallback_store:
            _fallback_store[file_id].update(status_data)
        else:
            _fallback_store[file_id] = status_data


def _fallback_get(file_id: str) -> dict:
    with _fallback_lock:
        return _fallback_store.get(file_id, {"status": "unknown"}).copy()


# ── Cleanup loop ─────────────────────────────────────────────────

_CLEANUP_LOCK_KEY = "dl:cleanup_lock"


def _acquire_cleanup_lock() -> bool:
    """Redis SET NX로 cleanup 리더 선출 — 한 워커만 정리 수행"""
    if not redis_client.is_available():
        return True  # fallback 모드에서는 모든 워커가 자기 in-memory 정리
    try:
        r = redis_client.get_redis()
        # TTL을 정리 주기의 2배로 설정 — 워커 죽어도 자동 해제
        return r.set(_CLEANUP_LOCK_KEY, os.getpid(), nx=True, ex=STATUS_CLEANUP_INTERVAL * 2)
    except Exception:
        return True  # Redis 에러 시 안전하게 정리 허용


def _cleanup_loop():
    """백그라운드 정리 스레드
    - Redis 모드: TTL이 자동 만료 담당 → 파일시스템 고아 폴더 정리만 (리더 워커만)
    - Fallback 모드: 기존 in-memory 정리 + 파일시스템 정리
    """
    while True:
        try:
            if redis_client.is_available():
                redis_client.check_health()
                # 멀티워커 환경에서 한 워커만 폴더 정리 수행
                if _acquire_cleanup_lock():
                    _cleanup_orphan_folders()
            else:
                _cleanup_fallback_store()
                _cleanup_orphan_folders()
                redis_client.check_health()
        except Exception as e:
            logging.error(f"상태 정보 정리 중 오류: {e}")

        time.sleep(STATUS_CLEANUP_INTERVAL)


def _cleanup_fallback_store():
    """fallback in-memory store에서 만료된 상태 제거"""
    now = datetime.now()
    to_delete = []

    with _fallback_lock:
        for file_id, status in _fallback_store.items():
            if not isinstance(status, dict) or "status" not in status:
                to_delete.append(file_id)
                continue
            if status["status"] in ("completed", "error"):
                ts = status.get("timestamp", 0)
                if (now - datetime.fromtimestamp(ts)).total_seconds() > STATUS_MAX_AGE:
                    to_delete.append(file_id)

        for file_id in to_delete:
            del _fallback_store[file_id]
            logging.info(f"[fallback] 상태 정보 정리됨: {file_id}")


def _get_active_file_ids() -> set[str]:
    """Redis SCAN으로 활성 상태의 file_id 집합을 한 번에 조회"""
    active = set()
    if redis_client.is_available():
        try:
            r = redis_client.get_redis()
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match=f"{_KEY_PREFIX}*", count=200)
                for key in keys:
                    # "dl:status:xxxx-xxxx" → "xxxx-xxxx"
                    active.add(key[len(_KEY_PREFIX):])
                if cursor == 0:
                    break
        except Exception as e:
            logging.warning(f"Redis SCAN 실패, 폴더 정리 건너뜀: {e}")
            return None  # SCAN 실패 시 정리하지 않음 (안전)
    # fallback store도 포함
    with _fallback_lock:
        active.update(_fallback_store.keys())
    return active


def _cleanup_orphan_folders():
    """downloads/ 디렉토리에서 상태가 없는 고아 폴더 삭제
    Redis SCAN 1회로 활성 키를 가져와서 비교 (폴더별 개별 조회 제거)
    """
    if not os.path.exists(DOWNLOAD_FOLDER):
        return

    active_ids = _get_active_file_ids()
    if active_ids is None:
        return  # Redis 에러 시 안전하게 건너뜀

    try:
        now = time.time()
        for name in os.listdir(DOWNLOAD_FOLDER):
            folder = safe_path_join(DOWNLOAD_FOLDER, name)
            if not os.path.isdir(folder):
                continue

            # 폴더 수정 시간이 STATUS_MAX_AGE보다 오래된 것만 대상
            try:
                if now - os.path.getmtime(folder) < STATUS_MAX_AGE:
                    continue
            except OSError:
                continue

            # 활성 상태가 있으면 건드리지 않음
            if name in active_ids:
                continue

            try:
                shutil.rmtree(folder)
                logging.info(f"고아 폴더 정리됨: {name}")
            except Exception as e:
                logging.error(f"폴더 삭제 중 오류: {name}, {e}")
    except Exception as e:
        logging.error(f"고아 폴더 정리 중 오류: {e}")
