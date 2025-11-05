# 차단 방지 (Rate Limiting) 가이드

## 🛡️ 현재 구현된 차단 방지 기능

### 1. **시간 지연 (Delay)**

#### Selenium 스크래퍼
```python
# 페이지 로드 후
time.sleep(random.uniform(0.5, 1.5))  # 0.5-1.5초

# 페이지 간 전환
time.sleep(random.uniform(0.5, 1.5))  # 0.5-1.5초
```

#### 일반 스크래퍼 (requests)
```python
# 성공적인 요청 후
time.sleep(random.uniform(0.1, 1.0))  # 0.1-1초

# 페이지 간 전환
time.sleep(random.uniform(0.5, 1.5))  # 0.5-1.5초
```

### 2. **User-Agent 랜덤화**
```python
user_agents = [
    'Chrome/120.0.0.0 Safari/537.36',
    'Firefox/121.0',
    'Chrome on Mac',
    'Chrome on Linux'
]
# 매 요청마다 랜덤 선택
```

### 3. **요청 간격 패턴**
- 첫 페이지: 즉시 요청
- 2페이지 이후: 0.5-1.5초 랜덤 대기
- 재시도 시: 0.5-2초 대기 후 재시도

### 4. **Selenium의 봇 감지 우회**
```python
# webdriver 속성 숨기기
driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

# 자동화 플래그 제거
options.add_experimental_option('excludeSwitches', ['enable-automation'])
```

## 📊 권장 사용 패턴

### ✅ 안전한 사용
```python
# 옵션 1: 적은 페이지 수집 (1-3페이지)
# - 차단 위험: 낮음
# - 소요 시간: 1-2분
pages = 3

# 옵션 2: Selenium 사용 (브라우저와 동일)
# - 차단 위험: 매우 낮음
# - 소요 시간: 더 느림 (3-5분)
source = 'selenium_sukebei'
```

### ⚠️ 주의가 필요한 사용
```python
# 많은 페이지 한번에 수집
pages = 10+  # 10페이지 이상

# 짧은 시간에 여러 번 실행
# 연속 실행 시 10-15분 간격 권장
```

### ❌ 피해야 할 패턴
```python
# 1. 딜레이 없이 연속 요청
# 2. 동일한 User-Agent 반복
# 3. 짧은 시간에 대량 수집
# 4. 여러 스레드로 동시 요청
```

## 🔧 차단 방지 설정 조정

만약 차단되었다면:

### 1. 더 긴 딜레이 설정
```python
# scrapers/selenium_scraper.py
time.sleep(random.uniform(5, 10))  # 더 길게

# scrapers/base_scraper.py
time.sleep(random.uniform(4, 7))  # 더 길게
```

### 2. 페이지 수 줄이기
```python
# GUI에서 또는 코드에서
pages = 2  # 3에서 2로 감소
```

### 3. 수집 간격 늘리기
- 수집 후 10-15분 대기
- 자주 사용하지 않기

## 📈 현재 타이밍 요약

| 동작 | 딜레이 | 목적 |
|------|--------|------|
| 페이지 로드 (Selenium) | 0.5-1.5초 | 빠른 수집 |
| 페이지 간 이동 | 0.5-1.5초 | 연속 요청 |
| 일반 요청 성공 후 | 0.1-1.0초 | 최소 딜레이 |
| 요청 재시도 간 | 0.5-2.0초 | 빠른 재시도 |

## 💡 팁

### Selenium 사용 (가장 안전)
```bash
# GUI에서 'Sukebei (Selenium)' 선택
# - 실제 브라우저처럼 작동
# - 차단 위험 최소
```

### 샘플 데이터 활용
```bash
# 차단 걱정 없이 테스트
python add_sample_data.py
python main.py
```

### 적당한 사용
```
하루 권장 수집 횟수: 3-5회
한 번에 수집 페이지: 2-3페이지
수집 간격: 10-15분
```

## ⏱️ 예상 소요 시간

| 페이지 수 | Selenium | 일반 스크래퍼 |
|----------|----------|--------------|
| 1페이지 | ~3초 | ~2초 |
| 3페이지 | ~10초 | ~6초 |
| 5페이지 | ~20초 | ~12초 |

**참고**: 빠른 수집으로 최적화되었습니다!

