"""
공통 유틸리티 함수들
"""
import os
import uuid
import time
import threading
from ipaddress import ip_network, ip_address

fs_lock = threading.Lock()


def safe_path_join(*paths):
    """안전한 경로 결합"""
    base = os.path.abspath(paths[0])
    for path in paths[1:]:
        joined = os.path.abspath(os.path.join(base, path))
        if not joined.startswith(base):
            raise ValueError("Invalid path")
        base = joined
    return base


def safely_access_files(directory_path):
    """스레드 안전한 파일 목록 가져오기"""
    with fs_lock:
        if os.path.exists(directory_path):
            files = os.listdir(directory_path)
            return files
        return []


def generate_error_id():
    """고유한 에러 추적 ID를 생성합니다."""
    return f"{int(time.time())}-{uuid.uuid4().hex[:8]}"


def check_ip_allowed(ip_str, allowed_ips):
    """IP 허용 여부 확인"""
    try:
        client_ip = ip_address(ip_str)
        for allowed in allowed_ips:
            # CIDR 표기법 (예: 10.0.0.0/8) 또는 단일 IP 처리
            if '/' in allowed:
                if client_ip in ip_network(allowed):
                    return True
            elif client_ip == ip_address(allowed):
                return True
        return False
    except ValueError:
        return False


def readable_size(size_bytes):
    """파일 크기를 읽기 쉬운 형태로 변환"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
