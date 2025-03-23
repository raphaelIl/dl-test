# dl-test

# Docker
## Docker build
```bash
docker build -t video-downloader .
```

## Run
```bash
docker run -d -p 5000:5000 -v $(pwd)/downloads:/app/downloads -v $(pwd)/logs:/app/logs --name video-downloader video-downloader
```

```bash
# Docker Compose로 빌드 및 실행
docker-compose up -d

# 로그 확인
docker-compose logs -f
```

# Diagram
```mermaid
sequenceDiagram
    participant Client as 클라이언트
    participant Flask as Flask 서버
    participant ThreadPool as 스레드풀(5개)
    participant Storage as 파일시스템
    participant Status as 상태저장소

%% 초기 다운로드 요청
    Client->>Flask: POST / (video_url 전송)
    Flask->>Flask: UUID 생성
    Flask->>Storage: 다운로드 폴더 생성
    Flask->>ThreadPool: download_video 작업 제출
    Flask-->>Client: /download-waiting/{file_id}로 리다이렉트

%% 다운로드 진행 상태 확인
    loop 2초마다
        Client->>Flask: GET /check-status/{file_id}
        Flask->>Status: 상태 조회
        Status-->>Flask: 현재 상태 반환
        Flask-->>Client: 상태 정보 응답
    end

%% 다운로드 완료 시
    ThreadPool->>Storage: 영상 파일 저장
    ThreadPool->>Status: 상태 업데이트 (completed)

    Client->>Flask: GET /result/{file_id}
    Flask->>Status: 완료 상태 확인
    Flask->>Storage: 파일 정보 조회
    Flask-->>Client: 다운로드 결과 페이지

%% 파일 다운로드
    Client->>Flask: GET /download-file/{file_id}
    Flask->>Storage: 파일 읽기
    Flask-->>Client: 파일 전송

%% 백그라운드 정리 작업
    loop 24시간마다
        Flask->>Storage: 오래된 파일 정리
    end

    loop 1시간마다
        Flask->>Status: 오래된 상태 정보 정리
    end
```
