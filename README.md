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

# 프로젝트 아키텍처

## 전체 시스템 구조
```mermaid
graph TB
    subgraph "Frontend Layer"
        UI[웹 인터페이스]
        WAIT[대기 페이지]
        RESULT[결과 페이지]
    end
    
    subgraph "Flask Application (app.py)"
        ROUTE[라우트 핸들러]
        LANG[다국어 지원]
        LIMIT[Rate Limiting]
    end
    
    subgraph "Core Processing"
        EXECUTOR[ThreadPoolExecutor]
        MANAGER[download_manager.py]
        STATUS[status_manager.py]
        STATS[stats.py]
    end
    
    subgraph "Download Strategy"
        STREAM[스트리밍 URL 추출]
        DIRECT[직접 링크 추출]
        SERVER[서버 다운로드]
    end
    
    subgraph "Utilities"
        UTILS[download_utils.py]
        WEB_UTILS[web_utils.py]
        CONFIG[config.py]
    end
    
    subgraph "External Services"
        YT_DLP[yt-dlp]
        PROXY[프록시 서버들]
    end
    
    subgraph "Storage"
        FS[파일 시스템]
        MEMORY[메모리 상태]
        LOGS[로그 파일]
    end
    
    UI --> ROUTE
    WAIT --> ROUTE
    RESULT --> ROUTE
    ROUTE --> EXECUTOR
    ROUTE --> STATUS
    EXECUTOR --> MANAGER
    MANAGER --> STREAM
    MANAGER --> DIRECT
    MANAGER --> SERVER
    STREAM --> UTILS
    DIRECT --> UTILS
    SERVER --> UTILS
    UTILS --> YT_DLP
    UTILS --> PROXY
    STATUS --> MEMORY
    MANAGER --> FS
    MANAGER --> LOGS
    ROUTE --> LANG
    ROUTE --> LIMIT
    MANAGER --> STATS
```

## 스마트 다운로드 프로세스 (3단계 폴백 시스템)
```mermaid
sequenceDiagram
    actor User as 사용자
    participant Web as 웹 인터페이스
    participant Thread as ThreadPoolExecutor
    participant Manager as download_manager
    participant Strategy as URL 전략 분석
    participant Stream as 스트리밍 추출
    participant Direct as 직접 링크 추출
    participant Server as 서버 다운로드
    participant Status as 상태 관리
    participant FS as 파일 시스템
    participant YT as yt-dlp

    User->>Web: 비디오 URL 입력
    Web->>Web: UUID 생성 (file_id)
    Web->>Thread: 다운로드 작업 제출
    Web->>User: 대기 페이지로 리다이렉트

    Thread->>Manager: download_video() 호출
    Manager->>Status: 처리 중 상태 업데이트 (10%)
    
    %% 1단계: 스트리밍 URL 추출 시도
    Manager->>Strategy: detect_url_type_and_strategy()
    Strategy-->>Manager: 도메인별 최적화 전략
    Manager->>Stream: extract_streaming_urls()
    
    alt YouTube/일반 사이트
        Stream->>YT: yt-dlp로 정보 추출
        YT-->>Stream: 비디오 메타데이터 + 포맷 목록
        Stream->>Stream: 브라우저 재생 가능한 MP4 필터링
        Stream-->>Manager: 스트리밍 정보 반환
        Manager->>Status: 즉시 완료 처리 (100%)
        Status-->>Web: 스트리밍 URL 제공
    else 성인 사이트/제한 사이트
        Stream->>Stream: 스텔스 모드 + 랜덤 User-Agent
        Stream->>YT: 특별 헤더로 최대 3회 시도
        alt 성공
            YT-->>Stream: 스트리밍 URL
            Stream-->>Manager: 스트리밍 정보 반환
            Manager->>Status: 즉시 완료 처리 (100%)
        else 실패
            Stream-->>Manager: null 반환
        end
    end

    %% 2단계: 직접 다운로드 링크 시도 (1단계 실패 시)
    alt 스트리밍 추출 실패
        Manager->>Direct: extract_direct_download_link()
        Direct->>YT: 직접 URL 추출 시도
        YT-->>Direct: 직접 다운로드 URL
        Direct->>Direct: validate_direct_download_link()
        Direct->>Direct: 파일 크기/타입 검증
        alt 유효한 직접 링크
            Direct-->>Manager: 직접 링크 정보
            Manager->>Status: 즉시 완료 처리 (100%)
            Status-->>Web: 직접 링크 제공
        else 직접 링크 실패
            Direct-->>Manager: null 반환
        end
    end

    %% 3단계: 서버 다운로드 (1,2단계 모두 실패 시)
    alt 모든 추출 방식 실패
        Manager->>Status: 다운로드 중 상태 (30%)
        Manager->>Server: try_download_enhanced()
        
        %% 3-1단계: 최적화된 직접 다운로드
        Server->>YT: 도메인별 최적화 설정으로 다운로드
        alt 직접 다운로드 성공
            YT->>FS: 실제 비디오 파일 저장
            FS-->>Server: 다운로드 완료
            Server-->>Manager: 성공 반환
            Manager->>Status: 파일 정보와 함께 완료 (100%)
        else 직접 다운로드 실패
            %% 3-2단계: m3u8 폴백
            Server->>Server: find_m3u8_candidates()
            Server->>Server: HTML 파싱 + atob 디코딩
            loop 최대 3개 m3u8 후보
                Server->>YT: m3u8 → 실제 비디오 변환
                alt m3u8 변환 성공
                    YT->>FS: 병합된 MP4 파일 저장
                    FS-->>Server: 변환 완료
                    Server-->>Manager: 성공 반환
                    Manager->>Status: 파일 정보와 함께 완료 (100%)
                else m3u8 변환 실패
                    Server->>Server: 다음 후보 시도
                end
            end
        end
    end

    %% 모든 방법 실패 시
    alt 모든 다운로드 방법 실패
        Manager->>Manager: get_video_info() 기본 정보만
        Manager->>Status: 원본 URL만으로 완료 처리
        Status-->>Web: 원본 사이트 리다이렉트
    end

    %% 사용자 상태 확인 루프
    loop 상태 확인
        User->>Web: AJAX 상태 확인
        Web->>Status: get_status()
        Status-->>Web: 현재 상태
        Web-->>User: 진행률 표시
    end

    %% 완료 후 결과 표시
    User->>Web: 최종 상태 확인
    Web->>Status: get_status()
    Status-->>Web: 완료 상태
    Web->>User: 결과 페이지로 리다이렉트

    %% 결과 페이지에서 처리
    alt 스트리밍 정보 있음
        Web-->>User: 브라우저 직접 재생
    else 직접 링크 있음
        Web-->>User: 직접 다운로드 링크 제공
    else 서버 파일 있음
        User->>Web: 파일 다운로드 요청
        Web->>FS: send_file()
        FS-->>User: 파일 전송
    else 원본 URL만 있음
        Web-->>User: 원본 사이트로 리다이렉트
    end

    %% 백그라운드 정리 작업
    par 백그라운드 정리
        loop 1분마다
            Thread->>Status: 오래된 상태 정리
            Thread->>FS: 오래된 파일 삭제
        end
    end
```

## 도메인별 처리 전략
```mermaid
flowchart TD
    URL[입력 URL] --> DETECT[도메인 감지]
    
    DETECT --> YT[YouTube/YoutuBe]
    DETECT --> SOCIAL[TikTok/Instagram/Facebook]
    DETECT --> ADULT[성인 사이트]
    DETECT --> UNKNOWN[알 수 없는 사이트]
    
    YT --> YT_STRATEGY[빠른 처리<br/>15초 타임아웃<br/>1회 시도]
    
    SOCIAL --> SOCIAL_STRATEGY[CORS 대응<br/>특별 User-Agent<br/>45초 타임아웃]
    
    ADULT --> ADULT_STRATEGY[스텔스 모드<br/>쿠키 제거<br/>랜덤 User-Agent<br/>120초 타임아웃<br/>최대 3회 시도<br/>프록시 지원]
    
    UNKNOWN --> UNKNOWN_STRATEGY[Generic Extractor<br/>60초 타임아웃<br/>안전한 헤더]
    
    YT_STRATEGY --> EXTRACT[URL 추출]
    SOCIAL_STRATEGY --> EXTRACT
    ADULT_STRATEGY --> EXTRACT
    UNKNOWN_STRATEGY --> EXTRACT
    
    EXTRACT --> SUCCESS[성공: 스트리밍 URL]
    EXTRACT --> FAIL[실패: 다음 단계로]
```

## 파일 구조 및 모듈 관계
```mermaid
graph LR
    subgraph "Core Files"
        APP[app.py<br/>Flask 앱 메인]
        CONFIG[config.py<br/>설정 관리]
    end
    
    subgraph "Download Engine"
        DM[download_manager.py<br/>다운로드 로직]
        DU[download_utils.py<br/>유틸리티 함수]
    end
    
    subgraph "System Management"
        SM[status_manager.py<br/>상태 추적]
        STATS[stats.py<br/>통계 관리]
        UTILS[utils.py<br/>공통 유틸]
        WU[web_utils.py<br/>웹 유틸]
    end
    
    subgraph "External Dependencies"
        YTDLP[yt-dlp<br/>비디오 추출]
        FLASK[Flask<br/>웹 프레임워크]
        BABEL[Flask-Babel<br/>다국어]
    end
    
    APP --> DM
    APP --> SM
    APP --> STATS
    APP --> WU
    DM --> DU
    DM --> UTILS
    DU --> YTDLP
    APP --> FLASK
    APP --> BABEL
    CONFIG --> APP
```
