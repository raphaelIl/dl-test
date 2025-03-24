# 현재 프로젝트에서 멀티스레드가 더 적합한 이유

## 1. I/O 바운드 작업의 특성

현재 프로젝트의 주요 작업들:
- YouTube 동영상 다운로드 (네트워크 I/O)
- 파일 시스템 작업 (디스크 I/O)
- 상태 관리 (메모리 접근)

이러한 I/O 바운드 작업들은 **대부분의 시간을 대기**하며 보냅니다:
- 네트워크 응답 대기
- 디스크 읽기/쓰기 대기
- CPU 연산은 매우 적음

```python
# 예: download_video 함수의 주요 작업
def download_video(url):
    # I/O 작업: YouTube 서버와 통신
    yt = YouTube(url)
    
    # I/O 작업: 파일 다운로드
    stream.download()
    
    # I/O 작업: 파일 시스템 접근
    with fs_lock:
        os.rename(...)
```

## 2. 공유 상태 관리의 용이성

현재 코드는 여러 공유 자원을 사용:
```python
download_status = {}  # 전역 상태
fs_lock = threading.Lock()  # 파일 시스템 락
status_lock = threading.Lock()  # 상태 락
```

멀티프로세스를 사용할 경우:
- 프로세스 간 상태 공유를 위해 복잡한 IPC 필요
- `multiprocessing.Manager()` 사용 필요
- 성능 오버헤드 발생
- 코드 복잡도 증가

## 3. 리소스 효율성

멀티스레드:
- 가벼운 메모리 사용
- 빠른 컨텍스트 스위칭
- 공유 메모리로 인한 효율적인 통신

멀티프로세스:
- 각 프로세스마다 메모리 복사
- 무거운 컨텍스트 스위칭
- IPC 오버헤드

## 4. 구체적인 예시

```python
# 현재 코드의 주요 작업
def clean_status_dict():    # I/O 중심
    while True:
        with status_lock:   # 공유 상태 접근
            # ... 상태 정리 ...
        time.sleep(3600)    # I/O 대기

def schedule_cleaning():    # I/O 중심
    while True:
        clean_old_files()   # 파일 시스템 I/O
        time.sleep(86400)   # I/O 대기
```

이러한 작업들은:
1. CPU 연산이 거의 없음
2. 대부분 I/O 대기
3. 상태 공유가 빈번
4. GIL의 영향을 거의 받지 않음

따라서, 현재 프로젝트의 특성상 멀티스레드가 멀티프로세스보다 더 효율적이고 적절한 선택입니다. CPU 바운드 작업이 추가된다면 그때 해당 부분만 멀티프로세스로 분리하는 것을 고려할 수 있습니다.

# I/O 바운드와 CPU 바운드 작업의 차이점

## I/O 바운드 작업

주로 입출력을 기다리는 작업:

```python
# I/O 바운드 작업의 예시
def io_bound_task():
    # 네트워크 I/O
    response = requests.get('https://api.example.com')
    
    # 파일 I/O
    with open('file.txt', 'w') as f:
        f.write(response.text)
    
    # 데이터베이스 I/O
    db.query("SELECT * FROM table")
```

특징:
- 대부분의 시간을 대기하며 보냄
- CPU 사용률이 낮음
- **멀티스레드**가 효과적

## CPU 바운드 작업

복잡한 연산을 수행하는 작업:

```python
# CPU 바운드 작업의 예시
def cpu_bound_task():
    # 복잡한 수학 계산
    result = 0
    for i in range(10**7):
        result += i * i
    
    # 이미지 처리
    image = Image.open('input.jpg')
    processed = image.filter(ImageFilter.BLUR)
```

특징:
- CPU를 집중적으로 사용
- 대기 시간이 거의 없음
- **멀티프로세스**가 효과적

## 현재 프로젝트의 작업 분류

현재 코드에는 대부분 I/O 바운드 작업이 있습니다:
1. YouTube 동영상 다운로드 (네트워크 I/O)
2. 파일 시스템 작업 (디스크 I/O)
3. 상태 확인 및 업데이트 (메모리 I/O)

따라서 멀티스레드가 더 적합한 선택입니다.
