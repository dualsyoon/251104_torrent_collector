# 빠른 시작 가이드 🚀

## ⚠️ 중요: 한국에서는 반드시 Selenium 사용!

일반 Python 연결은 **ISP 차단**으로 안 됩니다. 반드시 다음 중 하나를 선택하세요:

## 방법 1: Selenium 사용 (권장 ⭐)

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 애플리케이션 실행
python main.py

# 3. GUI에서 선택
소스: "⭐ Sukebei (Selenium) (권장)" 선택
↓
[토렌트 수집] 버튼 클릭
```

**왜 Selenium인가?**
- ✅ 실제 브라우저로 작동 → ISP 차단 우회
- ✅ 한국에서도 VPN 없이 작동
- ✅ 자동으로 75개씩 수집

## 방법 2: 샘플 데이터로 테스트

```bash
# VPN 없이 UI만 테스트하고 싶다면
python add_sample_data.py
python main.py
```

## 방법 3: VPN 사용

```bash
# ProtonVPN 등 VPN 연결 후
python main.py
# 아무 소스나 선택 가능
```

## ❌ 작동 안 하는 것들

### 1. 일반 스크래퍼
```
X "Sukebei (Nyaa)" → ISP 차단 (0개 수집)
X "JAVTorrent" → ISP 차단 (0개 수집)
△ "TorrentKitty" → 일부 작동 (느림)
```

### 2. 오류 메시지
```
[Sukebei] 연결 오류 (시도 1/3)
[Sukebei] X 최대 재시도 초과
OK 0개 수집
```
→ 이건 ISP 차단입니다. Selenium으로 바꾸세요!

## ✅ 제대로 작동하는 것

### Selenium 스크래퍼
```
[Selenium-Sukebei] Chrome 브라우저 초기화 중...
[Selenium-Sukebei] OK Chrome 브라우저 준비 완료!
[Selenium-Sukebei] 페이지 로드 중...
[Selenium-Sukebei] OK 페이지 로드 완료!
[Selenium-Sukebei] OK 75개 토렌트 추가

✓ 성공!
```

## 📊 GUI 사용법

1. **소스 선택**
   ```
   ⭐ Sukebei (Selenium) (권장) ← 이거 선택!
   ```

2. **수집 시작**
   ```
   [토렌트 수집] 버튼 클릭
   → 3페이지 (75개) 자동 수집
   ```

3. **필터링**
   ```
   - 기간: 1일/7일/1개월/전체
   - 검열: 검열됨/무검열/전체
   - 국가: 일본/한국/미국 등
   - 장르: 다양한 카테고리
   ```

4. **정렬**
   ```
   - 인기도: 시더 많은 순
   - 크기: 파일 크기
   - 날짜: 최신순
   ```

## 🔧 문제 해결

### Q1: Selenium이 작동 안 함
```bash
# Chrome 드라이버 자동 설치 확인
pip install --upgrade selenium webdriver-manager

# 다시 실행
python main.py
```

### Q2: 브라우저 창이 뜨나요?
```
아니요! Headless 모드로 백그라운드에서 작동합니다.
보이지 않지만 정상 작동 중입니다.
```

### Q3: 너무 느린데요?
```bash
# 현재 설정 (3페이지, ~10초)
pages = 3  # gui/main_window.py

# 더 빠르게 (1페이지만)
pages = 1  # ~3초

# 더 많이 (5페이지)
pages = 5  # ~20초
```

### Q4: 중복 데이터가 쌓이나요?
```
아니요! 자동으로 중복 제거됩니다.
- 새로운 것: 추가
- 기존 것: 시더/리처만 업데이트
```

## 🎯 첫 실행 체크리스트

- [ ] 1. `pip install -r requirements.txt` 실행
- [ ] 2. `python main.py` 실행
- [ ] 3. GUI에서 "⭐ Sukebei (Selenium)" 선택
- [ ] 4. [토렌트 수집] 버튼 클릭
- [ ] 5. 10초 대기
- [ ] 6. 75개 토렌트 확인! 🎉

## 📞 여전히 안 되나요?

1. **로그 확인**
   - 터미널에 뭐가 출력되나요?
   - "연결 오류" → ISP 차단, Selenium 사용
   - "Chrome 초기화 실패" → `pip install selenium webdriver-manager`

2. **샘플 데이터 테스트**
   ```bash
   python add_sample_data.py
   python main.py
   # UI는 작동하는지 확인
   ```

3. **자세한 문서**
   - `TROUBLESHOOTING.md` - 상세 문제 해결
   - `README.md` - 전체 기능 설명
   - `RATE_LIMITING.md` - 차단 방지 설정

## 🚀 성공 시나리오

```
$ python main.py

[애플리케이션 창 열림]
↓
[소스: ⭐ Sukebei (Selenium) 선택]
↓
[토렌트 수집 클릭]
↓
[10초 대기...]
↓
[수집 완료: 신규 75개, 업데이트 0개]
↓
[테이블에 75개 토렌트 표시됨!]
✓ 성공! 🎉
```

이제 시작하세요! 🚀

