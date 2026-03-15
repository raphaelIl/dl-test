"""
yt-dlp 메타데이터 캐싱 모듈 — Redis 기반, 장애 시 캐시 없이 동작
"""
import hashlib
import json
import logging

_KEY_PREFIX = "dl:meta:"
_DEFAULT_TTL = 1800  # 30분 (스트리밍 URL 유효기간 6시간 대비 안전 마진)

# formats에서 캐싱할 필드만 선별 (전체 저장 시 수 MB)
_FORMAT_FIELDS = ("url", "ext", "height", "vcodec", "acodec", "protocol", "format_id", "filesize")


def _make_key(url: str) -> str:
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    return f"{_KEY_PREFIX}{h}"


def _extract_cacheable(info: dict) -> dict:
    """캐싱에 필요한 필드만 선별하여 용량 절감"""
    if not info:
        return {}

    result = {}
    for field in ("title", "thumbnail", "duration", "uploader", "description",
                   "view_count", "upload_date", "ext", "extractor"):
        val = info.get(field)
        if val is not None:
            result[field] = val

    # url (직접 다운로드 링크용)
    if info.get("url"):
        result["url"] = info["url"]

    # formats — 필요한 필드만 축소 저장
    raw_formats = info.get("formats")
    if raw_formats:
        slim = []
        for fmt in raw_formats:
            entry = {k: fmt[k] for k in _FORMAT_FIELDS if k in fmt}
            if entry.get("url"):
                slim.append(entry)
        result["formats"] = slim

    # entries (플레이리스트) — 첫 항목만
    if "entries" in info and info["entries"]:
        first = info["entries"][0] if isinstance(info["entries"], list) else None
        if first:
            result["entries"] = [_extract_cacheable(first)]

    return result


def get_cached_info(url: str) -> dict | None:
    """Redis에서 캐시 조회, 없거나 Redis 불가 시 None"""
    import redis_client

    if not redis_client.is_available():
        return None

    try:
        r = redis_client.get_redis()
        raw = r.get(_make_key(url))
        if raw:
            logging.info(f"메타데이터 캐시 히트: {url[:60]}")
            return json.loads(raw)
    except Exception as e:
        logging.warning(f"메타데이터 캐시 조회 실패: {e}")
    return None


def set_cached_info(url: str, info: dict, ttl: int = _DEFAULT_TTL):
    """캐싱 가능한 필드만 선별하여 Redis에 저장"""
    import redis_client

    if not redis_client.is_available():
        return

    try:
        data = _extract_cacheable(info)
        if not data:
            return
        r = redis_client.get_redis()
        r.setex(_make_key(url), ttl, json.dumps(data, ensure_ascii=False))
        logging.info(f"메타데이터 캐시 저장: {url[:60]}")
    except Exception as e:
        logging.warning(f"메타데이터 캐시 저장 실패: {e}")
