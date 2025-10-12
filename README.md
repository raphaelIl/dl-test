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
        WAIT[대기 페이지<br/>실시간 상태 확인]
        RESULT[결과 페이지<br/>스트리밍/다운로드]
    end
    
    subgraph "Flask Application (app.py)"
        ROUTE[라우트 핸들러]
        LANG[다국어 지원<br/>11개 언어]
        LIMIT[Rate Limiting<br/>IP별 제한]
        PROXY_STREAM[프록시 스트리밍<br/>IP 숨김 기능]
    end
    
    subgraph "Core Processing"
        EXECUTOR[ThreadPoolExecutor<br/>비동기 다운로드]
        MANAGER[download_manager.py<br/>스마트 다운로드 로직]
        STATUS[status_manager.py<br/>실시간 상태 추적]
        STATS[stats.py<br/>다운로드 통계]
    end
    
    subgraph "3단계 Download Strategy"
        STREAM[1단계: 스트리밍 URL 추출<br/>브라우저 직접 재생]
        DIRECT[2단계: 직접 링크 추출<br/>서버 우회 다운로드]
        SERVER[3단계: 서버 다운로드<br/>m3u8 변환 포함]
    end
    
    subgraph "Smart URL Processing"
        DOMAIN_DETECT[도메인별 전략 분석]
        URL_FILTER[스마트 URL 필터링<br/>m3u8 제외, MP4 우선]
        RETRY_LOGIC[재시도 로직<br/>성인사이트 3회 시도]
    end
    
    subgraph "Utilities"
        UTILS[download_utils.py<br/>고급 다운로드 기능]
        WEB_UTILS[web_utils.py<br/>웹 관련 유틸]
        CONFIG[config.py<br/>환경 설정]
    end
    
    subgraph "External Services"
        YT_DLP[yt-dlp<br/>비디오 추출 엔진]
        PROXY_SERVERS[프록시 서버들<br/>우회 접속]
    end
    
    subgraph "Storage & State"
        FS[파일 시스템<br/>임시 다운로드]
        MEMORY[메모리 상태<br/>실시간 추적]
        LOGS[로그 파일<br/>상세 기록]
    end
    
    UI --> ROUTE
    WAIT --> ROUTE
    RESULT --> ROUTE
    ROUTE --> PROXY_STREAM
    ROUTE --> EXECUTOR
    ROUTE --> STATUS
    EXECUTOR --> MANAGER
    MANAGER --> DOMAIN_DETECT
    DOMAIN_DETECT --> STREAM
    DOMAIN_DETECT --> DIRECT
    DOMAIN_DETECT --> SERVER
    STREAM --> URL_FILTER
    DIRECT --> URL_FILTER
    SERVER --> URL_FILTER
    URL_FILTER --> RETRY_LOGIC
    RETRY_LOGIC --> UTILS
    UTILS --> YT_DLP
    UTILS --> PROXY_SERVERS
    STATUS --> MEMORY
    MANAGER --> FS
    MANAGER --> LOGS
    ROUTE --> LANG
    ROUTE --> LIMIT
    MANAGER --> STATS
```

## 스마트 다운로드 프로세스 (향상된 3단계 폴백 시스템)
```mermaid
sequenceDiagram
    actor User as 사용자
    participant Web as 웹인터페이스
    participant Thread as ThreadPoolExecutor
    participant Manager as 다운로드매니저
    participant Strategy as 도메인별전략분석
    participant Stream as 스트리밍URL추출
    participant Direct as 직접링크추출
    participant Server as 서버다운로드
    participant Proxy as IP숨김프록시
    participant Status as 상태관리
    participant FS as 파일시스템
    participant YT as yt-dlp

    User->>Web: 비디오 URL 입력
    Web->>Web: UUID 생성 (file_id)
    Web->>Thread: 다운로드 작업 제출
    Web->>User: 대기 페이지로 리다이렉트

    Thread->>Manager: download_video() 호출
    Manager->>Status: 처리 중 상태 업데이트 (10%)
    
    Note over Strategy: 도메인별 전략 분석
    Manager->>Strategy: detect_url_type_and_strategy()
    Strategy->>Strategy: 도메인 분석 및 최적화 전략 결정
    Note over Strategy: YouTube: 15초 타임아웃<br/>성인사이트: 스텔스모드+3회시도<br/>SNS: CORS 대응<br/>일반: Generic Extractor
    Strategy-->>Manager: 최적화된 추출 전략
    
    Note over Stream: 1단계: 스트리밍 URL 추출 시도
    Manager->>Stream: extract_streaming_urls()
    
    alt YouTube/일반 사이트
        Stream->>YT: yt-dlp로 정보 추출 (최적화된 설정)
        YT-->>Stream: 비디오 메타데이터 + 포맷 목록
        Stream->>Stream: 스마트 URL 필터링
        Note over Stream: 1차: MP4 비디오+오디오 통합<br/>2차: WebM 포맷<br/>3차: HTTP 직접 URL<br/>m3u8/HLS 완전 제외
        Stream-->>Manager: 브라우저 재생 가능한 스트리밍 정보
        Manager->>Status: 즉시 완료 처리 (100%)
        Status-->>Web: 스트리밍 URL 제공
    else 성인 사이트/제한 사이트
        loop 최대 3회 시도
            Stream->>Stream: 랜덤 User-Agent + 스텔스 모드
            Stream->>YT: 특별 헤더로 시도 (쿠키 제거)
            alt 성공
                YT-->>Stream: 스트리밍 URL
                Stream->>Stream: URL 유효성 검증 + 필터링
                Stream-->>Manager: 스트리밍 정보 반환
                Manager->>Status: 즉시 완료 처리 (100%)
            else 실패
                Stream->>Stream: 지연 후 다음 시도 (다른 User-Agent)
            end
        end
    end

    Note over Direct: 2단계: 직접 다운로드 링크 시도 (1단계 실패 시)
    alt 스트리밍 추출 실패
        Manager->>Status: 진행률 업데이트 (20%)
        Manager->>Direct: extract_direct_download_link()
        Direct->>YT: 직접 URL 추출 시도 (우회 설정)
        YT-->>Direct: 직접 다운로드 URL
        Direct->>Direct: validate_direct_download_link()
        Direct->>Direct: 파일 크기/타입/접근성 검증
        alt 유효한 직접 링크
            Direct-->>Manager: 직접 링크 정보
            Manager->>Status: 즉시 완료 처리 (100%)
            Status-->>Web: 직접 링크 제공
        else 직접 링크 실패
            Direct-->>Manager: null 반환
        end
    end

    Note over Server: 3단계: 서버 다운로드 (1,2단계 모두 실패 시)
    alt 모든 추출 방식 실패
        Manager->>Status: 다운로드 중 상태 (30%)
        Manager->>Server: try_download_enhanced()
        
        Note over Server: 3-1단계: 최적화된 직접 다운로드
        Server->>YT: 도메인별 최적화 설정으로 다운로드
        alt 직접 다운로드 성공
            YT->>FS: 실제 비디오 파일 저장
            Server->>Status: 진행률 업데이트 (70% to 100%)
            FS-->>Server: 다운로드 완료
            Server-->>Manager: 성공 반환
            Manager->>Status: 파일 정보와 함께 완료 (100%)
        else 직접 다운로드 실패
            Note over Server: 3-2단계: m3u8 폴백 시스템
            Server->>Server: find_m3u8_candidates()
            Server->>Server: HTML 파싱 + JavaScript atob 디코딩
            loop 최대 3개 m3u8 후보
                Server->>Status: 진행률 업데이트 (50% + i*15%)
                Server->>YT: m3u8 to MP4 변환 다운로드
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

    Note over Manager: 모든 방법 실패 시 폴백
    alt 모든 다운로드 방법 실패
        Manager->>Manager: get_video_info() 기본 정보만
        Manager->>Status: 원본 URL만으로 완료 처리
        Status-->>Web: 원본 사이트 리다이렉트 정보
    end

    Note over User,Web: 사용자 상태 확인 루프 (실시간)
    loop 상태 확인 (2초마다)
        User->>Web: AJAX 상태 확인
        Web->>Status: get_status()
        Status-->>Web: 현재 상태 + 진행률
        Web-->>User: 실시간 진행률 표시
    end

    Note over User,Web: 완료 후 결과 표시
    User->>Web: 최종 상태 확인
    Web->>Status: get_status()
    Status-->>Web: 완료 상태 + 결과 정보
    Web->>User: 결과 페이지로 리다이렉트

    Note over Proxy: 결과 페이지에서 처리 (IP 숨김 기능 포함)
    alt 스트리밍 정보 있음
        User->>Web: 비디오 플레이어 요청
        Web->>Web: IP 파라미터 검사
        alt STREAM_MODE=true and IP 파라미터 있음
            Web->>Proxy: 프록시 스트리밍 제공
            Proxy->>Proxy: 원본 서버에서 데이터 중계
            Proxy-->>User: 서버 IP 숨김으로 스트리밍
        else STREAM_MODE=false or IP 파라미터 없음
            Web-->>User: 원본 URL로 직접 리다이렉트
        end
    else 직접 링크 있음
        Web->>Web: IP 파라미터 검사 + 프록시 적용
        Web-->>User: 직접 다운로드 (IP 숨김 적용)
    else 서버 파일 있음
        User->>Web: 파일 다운로드 요청
        Web->>FS: send_file()
        FS-->>User: 서버에서 파일 전송
    else 원본 URL만 있음
        Web-->>User: 원본 사이트로 리다이렉트
    end

    Note over Status: 백그라운드 정리 작업
    par 백그라운드 정리
        loop 1분마다
            Thread->>Status: 오래된 상태 정리 (2분 이상)
            Thread->>FS: 오래된 파일 삭제
            Status->>Status: 메모리 최적화
        end
    end
```

## 도메인별 스마트 처리 전략
```mermaid
flowchart TD
    URL[입력 URL] --> DETECT[도메인 감지 및 전략 분석]
    
    DETECT --> YT[YouTube/YoutuBe<br/>youtube.com, youtu.be]
    DETECT --> SOCIAL[SNS 플랫폼<br/>TikTok, Instagram, Facebook]
    DETECT --> ADULT[성인 사이트<br/>Pornhub, Xvideos 등]
    DETECT --> STREAM[스트리밍 사이트<br/>Vimeo, Dailymotion]
    DETECT --> UNKNOWN[알 수 없는 사이트]
    
    YT --> YT_STRATEGY[빠른 처리 전략<br/>15초 타임아웃<br/>1회 시도<br/>YouTube Extractor 우선<br/>표준 헤더]
    
    SOCIAL --> SOCIAL_STRATEGY[SNS 최적화 전략<br/>CORS 대응 헤더<br/>특별 User-Agent<br/>45초 타임아웃<br/>cross-site 모드]
    
    ADULT --> ADULT_STRATEGY[스텔스 모드 전략<br/>쿠키 완전 제거<br/>랜덤 User-Agent 7종<br/>120초 타임아웃<br/>최대 3회 시도<br/>프록시 로테이션<br/>지연 시간 랜덤화]
    
    STREAM --> STREAM_STRATEGY[스트리밍 최적화<br/>전용 Extractor<br/>60초 타임아웃<br/>고품질 우선]
    
    UNKNOWN --> UNKNOWN_STRATEGY[Generic 전략<br/>Generic Extractor 강제<br/>60초 타임아웃<br/>안전한 헤더<br/>보수적 접근]
    
    YT_STRATEGY --> EXTRACT[URL 추출 및 필터링]
    SOCIAL_STRATEGY --> EXTRACT
    ADULT_STRATEGY --> EXTRACT
    STREAM_STRATEGY --> EXTRACT
    UNKNOWN_STRATEGY --> EXTRACT
    
    EXTRACT --> FILTER[스마트 URL 필터링]
    
    FILTER --> PRIORITY1[1차 우선순위<br/>MP4 비디오+오디오 통합<br/>1080p 이하, m3u8 제외]
    FILTER --> PRIORITY2[2차 우선순위<br/>WebM 포맷<br/>브라우저 호환성 확인]
    FILTER --> PRIORITY3[3차 우선순위<br/>HTTP 직접 URL<br/>기본 포맷 지원]
    
    PRIORITY1 --> SUCCESS[성공: 최적화된 스트리밍 URL]
    PRIORITY2 --> SUCCESS
    PRIORITY3 --> SUCCESS
    FILTER --> FAIL[실패: 다음 단계로]
```

## IP 숨김 프록시 시스템 (신규 추가)
```mermaid
flowchart TD
    USER[사용자 요청] --> CHECK_MODE{STREAM_MODE<br/>활성화?}
    
    CHECK_MODE -->|false| DIRECT[직접 리다이렉트<br/>기존 방식]
    CHECK_MODE -->|true| CHECK_IP{URL에 IP<br/>파라미터 있음?}
    
    CHECK_IP -->|없음| DIRECT
    CHECK_IP -->|있음| PROXY[프록시 스트리밍]
    
    PROXY --> FETCH[서버에서 원본 영상 요청]
    FETCH --> STREAM[사용자에게 스트리밍 중계]
    
    STREAM --> HEADERS[Range 헤더 지원<br/>탐색 기능 유지]
    HEADERS --> HIDE_IP[서버 IP 완전 숨김<br/>사용자 브라우저에 노출 안됨]
    
    DIRECT --> EXPOSE_IP[원본 URL 노출<br/>IP 파라미터 보임]
    HIDE_IP --> SECURE[✅ 보안 강화]
    EXPOSE_IP --> VISIBLE[⚠️ IP 정보 노출]
```

## 파일 구조 및 모듈 관계 (업데이트)
```mermaid
graph LR
    subgraph "Core Application"
        APP[app.py<br/>Flask 메인 앱<br/>+ IP 숨김 프록시]
        CONFIG[config.py<br/>환경 설정<br/>+ STREAM_MODE]
    end
    
    subgraph "Download Engine"
        DM[download_manager.py<br/>스마트 다운로드 로직<br/>+ 도메인별 전략]
        DU[download_utils.py<br/>고급 유틸리티<br/>+ m3u8 처리]
    end
    
    subgraph "System Management"
        SM[status_manager.py<br/>실시간 상태 추적<br/>+ 자동 정리]
        STATS[stats.py<br/>다운로드 통계<br/>+ 성능 모니터링]
        UTILS[utils.py<br/>공통 유틸리티]
        WU[web_utils.py<br/>웹 관련 기능]
    end
    
    subgraph "External Dependencies"
        YTDLP[yt-dlp<br/>비디오 추출 엔진]
        FLASK[Flask + Extensions<br/>웹 프레임워크]
        BABEL[Flask-Babel<br/>11개 언어 지원]
        REQUESTS[Requests<br/>프록시 스트리밍]
    end
    
    APP --> DM
    APP --> SM
    APP --> STATS
    APP --> WU
    APP --> REQUESTS
    DM --> DU
    DM --> UTILS
    DU --> YTDLP
    APP --> FLASK
    APP --> BABEL
    CONFIG --> APP
    
    style APP fill:#e1f5fe
    style DM fill:#f3e5f5
    style YTDLP fill:#fff3e0
```

## 주요 특징 및 개선사항

### 🚀 성능 최적화
- **도메인별 맞춤 전략**: 각 사이트 특성에 최적화된 추출 방법
- **3단계 폴백 시스템**: 단계별 실패 시 자동 대안 제공
- **스마트 URL 필터링**: m3u8 제외, 브라우저 호환성 우선
- **실시간 상태 추적**: 사용자 경험 향상

### 🔒 보안 및 우회 기능
- **성인사이트 스텔스 모드**: 3회 재시도 + 랜덤 User-Agent
- **IP 숨김 프록시**: 서버 IP 노출 방지 (환경변수 토글)
- **쿠키 제거**: 추적 방지
- **프록시 로테이션**: 차단 우회

### 🌐 다국어 및 사용성
- **11개 언어 지원**: 글로벌 사용자 대응
- **반응형 UI**: 모바일/데스크톱 최적화
- **실시간 진행률**: 2초마다 상태 업데이트
- **자동 정리**: 메모리 및 디스크 최적화

### 📊 모니터링 및 관리
- **상세 로깅**: 단계별 처리 과정 기록
- **통계 수집**: 성공/실패율 추적
- **헬스체크**: 시스템 상태 모니터링
- **Rate Limiting**: 서버 부하 방지
