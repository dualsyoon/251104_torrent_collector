# 토렌트 수집기 (Torrent Collector)

성인 토렌트 사이트에서 데이터를 수집하고 관리하는 로컬 애플리케이션입니다.

## 주요 기능

- 🌐 **다중 소스 지원**: 3개의 토렌트 사이트에서 데이터 수집
  - **Sukebei (Nyaa)**: 일본 토렌트 사이트 (검열/무검열 혼합)
  - **JAVTorrent**: 일본 AV 전문 (주로 무검열)
  - **TorrentKitty**: DHT 검색 엔진 (다국가)
- 📊 **기간별 정렬**: 1일, 7일, 1달, 전체 기간별 인기도 순 정렬
- 🎯 **필터링**: Censored/Uncensored, 국가, 장르별 필터링
- 💾 **로컬 데이터베이스**: SQLite를 이용한 오프라인 데이터 저장
- 🖼️ **썸네일 뷰**: 웹 링크를 통한 썸네일 및 스냅샷 표시
- 🧲 **Magnet 링크**: 원클릭 토렌트 다운로드
- 🔎 **검색 기능**: 제목 기반 검색
- 🔄 **자동 재시도**: 연결 실패 시 자동 재시도 로직

## 설치 방법

### 1. Python 설치
Python 3.9 이상이 필요합니다. [Python 공식 사이트](https://www.python.org/)에서 다운로드하세요.

### 2. 의존성 패키지 설치
```bash
pip install -r requirements.txt
```

### 3. ⚠️ 중요: 한국에서는 VPN이 거의 필수입니다!

**문제**: Python으로 해당 사이트 접속 시 ISP가 SSL 연결을 차단합니다.
**해결**: VPN 사용 또는 샘플 데이터로 테스트

```bash
# 옵션 1: 샘플 데이터로 먼저 테스트 (VPN 없이 가능)
python add_sample_data.py
python main.py

# 옵션 2: VPN 설치 후 실제 데이터 수집
# ProtonVPN 추천: https://protonvpn.com/download
# VPN 연결 후:
python main.py
```

자세한 내용은 `SOLUTION.md` 파일을 참조하세요.

## 실행 방법

### 일반 실행
```bash
python main.py
```

### 🧪 테스트 모드 (연결 문제 시)
사이트 접속이 안 되는 경우, 샘플 데이터로 먼저 테스트해보세요:

```bash
# 1. 샘플 데이터 추가
python add_sample_data.py

# 2. 애플리케이션 실행
python main.py
```

샘플 데이터로 UI와 기능을 미리 확인할 수 있습니다!

## 사용 방법

### 1. 토렌트 수집
- **소스 선택**: 드롭다운에서 수집할 소스를 선택합니다.
  - "모든 소스": 3개 사이트 모두에서 수집
  - 개별 소스: 특정 사이트만 선택
- 상단의 **"📥 새 토렌트 수집"** 버튼을 클릭하거나 메뉴에서 `데이터 > 새 토렌트 수집`을 선택합니다.
- 각 소스당 3페이지씩 자동으로 수집하여 데이터베이스에 저장합니다.
- 진행 상황은 프로그레스 바에 표시됩니다.

### 2. 필터링
좌측 필터 패널에서 다양한 조건으로 필터링할 수 있습니다:

- **기간**: 전체, 1일, 7일, 1개월
- **검열**: 전체, 검열, 무검열
- **국가**: JP(일본), CN(중국), KR(한국), US(미국), EU(유럽), TH(태국), OTHER(기타)
- **장르**: 다중 선택 가능 (Blowjob, Handjob, Threesome, Creampie, Anal, BDSM 등)
- **검색**: 제목으로 검색

### 3. Magnet 링크 사용
- 토렌트 행을 **더블 클릭**하거나 우측의 **🧲 버튼**을 클릭하면 Magnet 링크가 실행됩니다.
- 토렌트 클라이언트(qBittorrent, uTorrent 등)가 설치되어 있어야 합니다.

## 프로젝트 구조

```
251104_torrent_collector/
├── main.py                 # 애플리케이션 진입점
├── requirements.txt        # 의존성 패키지 목록
├── torrents.db            # SQLite 데이터베이스 (자동 생성)
├── database/              # 데이터베이스 모듈
│   ├── __init__.py
│   ├── models.py          # 데이터 모델 (Torrent, Genre, Country)
│   └── database.py        # 데이터베이스 연결 및 쿼리
├── scrapers/              # 웹 스크래퍼 모듈
│   ├── __init__.py
│   ├── base_scraper.py    # 기본 스크래퍼 클래스
│   └── sukebei_scraper.py # Sukebei.nyaa.si 전용 스크래퍼
└── gui/                   # GUI 모듈
    ├── __init__.py
    ├── main_window.py     # 메인 윈도우
    ├── filter_panel.py    # 필터 패널
    └── torrent_list.py    # 토렌트 리스트 테이블
```

## 기술 스택

- **GUI**: PySide6 (Qt6 for Python)
- **데이터베이스**: SQLite + SQLAlchemy ORM
- **웹 스크래핑**: BeautifulSoup4 + Requests
- **언어**: Python 3.9+

## 데이터베이스 스키마

### Torrent 테이블
- 제목, 카테고리, 크기, Magnet 링크
- 국가, 검열 여부, 장르 (다대다 관계)
- 시더, 리처, 다운로드 수, 인기도 점수
- 업로드 날짜, 생성/수정 날짜

### Genre 테이블
- 장르 이름 (영어/한국어)
- 22개 기본 장르 제공

### Country 테이블
- 국가 코드 및 이름
- 8개 기본 국가 제공

## 인기도 계산 공식

```
인기도 점수 = (Seeders × 3) + (Leechers × 1) + (Downloads × 0.1) + (Comments × 2)
```

토렌트 목록은 인기도 점수와 업로드 날짜를 기준으로 정렬됩니다.

## 문제 해결

### Magnet 링크가 실행되지 않을 때
- 토렌트 클라이언트(qBittorrent, uTorrent 등)가 설치되어 있는지 확인하세요.
- Magnet 링크를 기본 앱에 연결했는지 확인하세요.

### 스크래핑이 실패할 때 (ConnectionResetError)

**⚠️ 중요: 한국에서는 대부분의 성인 토렌트 사이트가 ISP에 의해 차단됩니다!**

#### 즉시 해결 방법:

1. **VPN 사용 (필수!)** 🔐
   ```bash
   # 무료 VPN 추천:
   # - ProtonVPN (https://protonvpn.com/)
   # - Windscribe (https://windscribe.com/)
   # 설치 후 일본이나 미국 서버로 연결
   ```

2. **연결 테스트 실행**
   ```bash
   python test_connection.py
   ```
   어떤 사이트가 접속 가능한지 확인

3. **CloudScraper 설치** (차단 우회)
   ```bash
   pip install cloudscraper
   ```

4. **다른 네트워크 시도**
   - 모바일 핫스팟 사용
   - 다른 와이파이 네트워크

자세한 내용은 `TROUBLESHOOTING.md` 파일을 참조하세요.

### 데이터베이스 오류
- `torrents.db` 파일을 삭제하고 애플리케이션을 재시작하세요.

## 변경 사항 (v2.1.0)

### ✨ 새로운 기능
- ✅ **Selenium 브라우저 자동화**: ISP 차단 완전 우회! ⭐
- ✅ **다중 소스 지원**: 4개 사이트 (Sukebei, JAVTorrent, TorrentKitty, Selenium)
- ✅ **소스 선택 UI**: 드롭다운에서 수집할 소스 선택 가능
- ✅ **차단 방지 시스템**: 자동 딜레이, User-Agent 랜덤화
- ✅ **연결 안정성 개선**: 자동 재시도 로직 (최대 3회)
- ✅ **스크래퍼 매니저**: 중앙 집중식 소스 관리

### 🛡️ 차단 방지
- 페이지 간 3-8초 자동 대기
- User-Agent 랜덤 로테이션
- Selenium 봇 감지 우회
- 자세한 내용: `RATE_LIMITING.md`

## 향후 개선 사항

- [ ] 더 많은 토렌트 사이트 지원 추가
- [ ] 썸네일 자동 다운로드 및 캐싱
- [ ] 토렌트 상세 정보 보기
- [ ] 즐겨찾기/북마크 기능
- [ ] 다운로드 히스토리 추적
- [ ] 다크 모드 지원
- [ ] 다국어 지원
- [ ] 프록시/VPN 설정

## 주의사항

⚠️ **이 애플리케이션은 교육 목적으로만 사용하시기 바랍니다.**

- 성인 콘텐츠를 다루므로 만 19세 이상만 사용해야 합니다.
- 저작권 및 관련 법률을 준수하여 사용하세요.
- 불법 콘텐츠 다운로드는 법적 책임이 따를 수 있습니다.

## 라이선스

이 프로젝트는 개인 사용 목적으로 제작되었습니다.
