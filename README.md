# dl-test

# Docker
## Docker build
```bash
docker build -t raphael1021/dl-test .
```

## Run
5000 port는 맥에서 다른걸로 점유하고 있는듯  
8080 port로 변경

```bash
docker-compose build

docker-compose up -d

docker-compose logs -f
```

# Diagram
```mermaid
sequenceDiagram
    actor User as 사용자
    participant Web as 웹 인터페이스
    participant Thread as ThreadPoolExecutor
    participant Status as 상태 관리(download_status)
    participant FS as 파일 시스템

    User->>Web: YouTube URL 입력
    Web->>Web: UUID 생성
    Web->>Thread: 다운로드 작업 제출
    Web->>User: 대기 페이지로 리다이렉트

    Thread->>Status: 상태 업데이트(status_lock)
    Thread->>FS: YouTube 동영상 다운로드(fs_lock)
    Thread->>Status: 완료 상태 업데이트(status_lock)

    loop 상태 확인
        User->>Web: 상태 확인 요청
        Web->>Status: 상태 조회(status_lock)
        Web->>User: 상태 응답(진행 중)
    end

    User->>Web: 상태 확인 요청
    Web->>Status: 상태 조회(status_lock)
    Web->>User: 완료 페이지로 리다이렉트

    User->>Web: 다운로드 요청
    Web->>FS: 파일 존재 확인(fs_lock)
    Web->>FS: 파일 목록 조회(safely_access_files)
    Web->>FS: 파일 확인(fs_lock)
    Web->>User: 파일 전송

    par 백그라운드 작업
        loop STATUS_MAX_AGE(3분)마다
            Thread->>FS: 오래된 파일 정리(fs_lock)
        end

        loop STATUS_CLEANUP_INTERVAL(1분)마다
            Thread->>Status: 오래된 상태 정리(status_lock)
        end
    end
```
