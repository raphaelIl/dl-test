"""
상태 관리 모듈 - 다운로드 상태 추적 및 정리
"""
import os
import time
import shutil
import logging
import threading
from datetime import datetime
from config import STATUS_MAX_AGE, STATUS_CLEANUP_INTERVAL, DOWNLOAD_FOLDER
from utils import safe_path_join

# 전역 상태 관리
status_lock = threading.Lock()
download_status = {}


def update_status(file_id, status_data):
    """다운로드 상태 업데이트 (기존 상태와 병합)"""
    with status_lock:
        if file_id in download_status:
            download_status[file_id].update(status_data)
        else:
            download_status[file_id] = status_data


def get_status(file_id):
    """다운로드 상태 조회"""
    with status_lock:
        return download_status.get(file_id, {'status': 'unknown'})


def clean_status_dict():
    """오래된 상태 정보 정리 (백그라운드 스레드)"""
    while True:
        try:
            now = datetime.now()
            to_delete = []

            with status_lock:
                for file_id in list(download_status.keys()):
                    status = download_status[file_id]
                    # 잘못된 형태의 status 데이터 방어
                    if not isinstance(status, dict) or 'status' not in status:
                        to_delete.append(file_id)
                        continue
                    if status['status'] in ['completed', 'error']:
                        timestamp = status.get('timestamp', 0)
                        if (now - datetime.fromtimestamp(timestamp)).total_seconds() > STATUS_MAX_AGE:
                            to_delete.append(file_id)

                # 상태 정보 삭제 및 파일 시스템 정리
                for file_id in to_delete:
                    del download_status[file_id]
                    logging.info(f"상태 정보 정리됨: {file_id}")

                    # 파일 시스템에서 폴더 삭제
                    folder_path = safe_path_join(DOWNLOAD_FOLDER, file_id)
                    try:
                        if os.path.exists(folder_path):
                            shutil.rmtree(folder_path)
                            logging.info(f"다운로드 파일 정리됨: {file_id}")
                    except Exception as e:
                        logging.error(f"폴더 삭제 중 오류 발생: {file_id}, {str(e)}", exc_info=True)

            time.sleep(STATUS_CLEANUP_INTERVAL)
        except Exception as e:
            logging.error(f"상태 정보 정리 중 오류: {str(e)}")
            time.sleep(STATUS_CLEANUP_INTERVAL)


def start_cleanup_thread():
    """상태 정리 스레드 시작"""
    status_cleaning_thread = threading.Thread(target=clean_status_dict)
    status_cleaning_thread.daemon = True
    status_cleaning_thread.start()
    logging.info("상태 정리 스레드 시작됨")
