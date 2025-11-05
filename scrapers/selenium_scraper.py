"""Selenium 기반 브라우저 자동화 스크래퍼"""
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from datetime import datetime
import time
import random
from config import PROXY_URL

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    print("⚠️ Selenium이 설치되지 않았습니다.")
    print("설치: pip install selenium webdriver-manager")


class SeleniumBaseScraper:
    """Selenium 기반 스크래퍼 - ISP 차단 우회"""
    
    def __init__(self, base_url: str = "", name: str = "Selenium"):
        """
        Args:
            base_url: 스크래핑할 사이트의 기본 URL
            name: 스크래퍼 이름
        """
        self.base_url = base_url
        self.name = name
        self.driver = None
        
        if not SELENIUM_AVAILABLE:
            raise ImportError("Selenium이 설치되지 않았습니다")
    
    def _init_driver(self):
        """Chrome 드라이버 초기화"""
        if self.driver:
            return
        
        print(f"[{self.name}] Chrome 브라우저 초기화 중...")
        
        options = Options()
        
        # 헤드리스 모드 (백그라운드 실행)
        options.add_argument('--headless=new')
        
        # 성능 최적화
        options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--disable-quic')
        
        # 봇 감지 우회
        options.add_experimental_option('excludeSwitches', ['enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)
        
        # User-Agent 설정
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        # SSL 인증서 오류 무시
        options.add_argument('--ignore-certificate-errors')
        options.add_argument('--ignore-ssl-errors')
        
        # 프록시 (옵션)
        if PROXY_URL:
            options.add_argument(f'--proxy-server={PROXY_URL}')
            # 일부 환경에서 직접 연결 우회
            options.add_argument('--proxy-bypass-list=*')
        
        # 로그 최소화
        options.add_argument('--log-level=3')
        options.add_experimental_option('excludeSwitches', ['enable-logging'])
        
        try:
            # ChromeDriver 자동 설치 및 설정
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
            
            # JavaScript로 webdriver 속성 제거 (봇 감지 우회)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            print(f"[{self.name}] OK Chrome 브라우저 준비 완료!")
            
        except Exception as e:
            print(f"[{self.name}] X 브라우저 초기화 실패: {e}")
            raise
    
    def get_page_selenium(self, url: str, wait_time: int = 5) -> Optional[BeautifulSoup]:
        """Selenium으로 페이지 가져오기
        
        Args:
            url: 요청할 URL
            wait_time: 페이지 로드 대기 시간 (초)
            
        Returns:
            BeautifulSoup 객체 또는 None
        """
        # 재시도 로직 (네트워크 리셋 대비)
        last_exc = None
        for attempt in range(3):
            try:
                # 드라이버 초기화
                if not self.driver:
                    self._init_driver()
                
                print(f"[{self.name}] 페이지 로드 중: {url}")
                self.driver.get(url)
                time.sleep(random.uniform(0.6, 1.8))
                try:
                    WebDriverWait(self.driver, wait_time).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body"))
                    )
                except:
                    pass
                page_source = self.driver.page_source
                print(f"[{self.name}] OK 페이지 로드 완료 ({len(page_source)} bytes)")
                return BeautifulSoup(page_source, 'lxml')
            except Exception as e:
                last_exc = e
                print(f"[{self.name}] X 페이지 로드 실패(재시도 {attempt+1}/3): {e}")
                time.sleep(1.2 * (attempt + 1))
                continue
        print(f"[{self.name}] X 최종 실패: {last_exc}")
        return None
    
    def close(self):
        """브라우저 종료"""
        if hasattr(self, 'driver') and self.driver:
            try:
                self.driver.quit()
                print(f"[{self.name}] 브라우저 종료")
            except:
                pass
            self.driver = None
    
    def __del__(self):
        """소멸자 - 브라우저 자동 종료"""
        self.close()


class SeleniumSukebeiScraper(SeleniumBaseScraper):
    """Selenium 기반 Sukebei 스크래퍼"""
    
    def __init__(self):
        super().__init__('https://sukebei.nyaa.si', 'Selenium-Sukebei')
        
        # AV만 수집하기 위한 카테고리 필터
        # 1_1: JAV (무검열), 1_2: JAV (검열), 1_3: JAV (Raw)
        self.av_categories = ['1_1', '1_2', '1_3']
    
    def scrape_page(self, page: int = 1, sort_by: str = 'seeders', order: str = 'desc', category: Optional[str] = None) -> List[Dict]:
        """페이지에서 토렌트 정보 스크래핑
        
        Args:
            page: 페이지 번호
            sort_by: 정렬 기준
            order: 정렬 순서
            category: 카테고리 (AV만: 1_1, 1_2, 1_3)
            
        Returns:
            토렌트 정보 딕셔너리 리스트
        """
        # 페이지 간 딜레이
        if page > 1:
            delay = random.uniform(0.5, 1.5)
            print(f"[{self.name}] 다음 페이지 요청 전 {delay:.1f}초 대기 중...")
            time.sleep(delay)
        
        # URL 구성 (AV 카테고리만)
        # Sukebei 카테고리 코드 확인 필요 - Live Action 카테고리 코드가 다를 수 있음
        # 일단 전체에서 수집하고 카테고리로 필터링하는 방식으로 변경
        if category:
            url = f"{self.base_url}/?f=0&c={category}&p={page}&s={sort_by}&o={order}"
        else:
            # 전체에서 수집 (카테고리 필터는 나중에)
            url = f"{self.base_url}/?f=0&c=0_0&p={page}&s={sort_by}&o={order}"
        
        # Selenium으로 페이지 가져오기
        soup = self.get_page_selenium(url)
        
        if not soup:
            return []
        
        torrents = []
        
        # 테이블 찾기 (여러 방법 시도)
        table = soup.find('table', class_='torrent-list')
        if not table:
            # 다른 클래스명으로 시도
            table = soup.find('table', class_=lambda x: x and 'torrent' in x.lower())
        if not table:
            # class 없는 table도 시도
            table = soup.find('table')
        if not table:
            print(f"[{self.name}] X 토렌트 테이블을 찾을 수 없습니다")
            # 디버깅: 페이지 구조 확인
            print(f"[{self.name}] 디버그 - 페이지 크기: {len(str(soup))} bytes")
            # body 태그 확인
            body = soup.find('body')
            if body:
                print(f"[{self.name}] 디버그 - body 내용 일부: {str(body)[:500]}...")
            return []
        
        # tbody 찾기 (없으면 table에서 직접)
        tbody = table.find('tbody')
        if tbody:
            rows = tbody.find_all('tr')
        else:
            # tbody가 없으면 table에서 직접 tr 찾기
            rows = table.find_all('tr')
        
        print(f"[{self.name}] OK {len(rows)}개 토렌트 발견")
        
        # 첫 번째 행이 헤더일 수 있으므로 확인
        if rows and len(rows) > 0:
            first_row_cols = rows[0].find_all('td')
            if len(first_row_cols) == 0:
                # 첫 번째 행이 헤더(th)인 경우 제외
                rows = rows[1:]
                print(f"[{self.name}] 헤더 행 제외, 실제 데이터: {len(rows)}개")
        
        processed_count = 0
        filtered_count = 0
        no_magnet_count = 0
        category_stats = {}  # 카테고리별 통계
        
        for row in rows:
            try:
                columns = row.find_all('td')
                if len(columns) < 7:
                    continue
                
                # 카테고리
                category_col = columns[0]
                category_link = category_col.find('a')
                category = category_link.get('title', '') if category_link else ''
                
                # 카테고리 통계 수집
                if category:
                    category_stats[category] = category_stats.get(category, 0) + 1
                
                # AV만 수집 (Real Life - Videos 카테고리만)
                category_lower = category.lower() if category else ''
                
                # Real Life - Videos 카테고리만 수집
                if not category_lower or 'real life' not in category_lower or 'videos' not in category_lower:
                    # Real Life - Videos가 아니면 스킵
                    filtered_count += 1
                    continue
                
                # 제목 및 링크
                name_col = columns[1]
                title_links = name_col.find_all('a')
                
                if not title_links:
                    continue
                
                title_link = title_links[-1]
                title = title_link.get_text(strip=True)
                view_url = title_link.get('href', '')
                source_id = view_url.split('/')[-1] if view_url else ''
                
                # 제목 필터 제거 - 카테고리 필터만 사용 (URL에서 c=3_0으로 이미 필터링됨)
                
                # Magnet 링크 찾기 (여러 방법 시도)
                # magnet:?xt=urn:btih:... 형식으로 시작
                magnet_link = ''
                
                # 방법 1: name_col 내의 모든 링크에서 magnet 찾기 (magnet: 또는 magnet:?로 시작)
                for link in name_col.find_all('a'):
                    href = link.get('href', '')
                    if href and (href.startswith('magnet:') or href.startswith('magnet:?')):
                        magnet_link = href
                        break
                
                # 방법 2: data-clipboard-text 속성에서 찾기 (Sukebei에서 자주 사용)
                if not magnet_link:
                    clipboard_elem = name_col.find(attrs={'data-clipboard-text': True})
                    if clipboard_elem:
                        clipboard_text = clipboard_elem.get('data-clipboard-text', '')
                        if clipboard_text and (clipboard_text.startswith('magnet:') or clipboard_text.startswith('magnet:?')):
                            magnet_link = clipboard_text
                
                # 방법 3: name_col 전체에서 data 속성 찾기
                if not magnet_link:
                    for elem in name_col.find_all(attrs={'data-clipboard-text': True}):
                        clipboard_text = elem.get('data-clipboard-text', '')
                        if clipboard_text and (clipboard_text.startswith('magnet:') or clipboard_text.startswith('magnet:?')):
                            magnet_link = clipboard_text
                            break
                
                # 방법 4: 다운로드 아이콘 버튼에서 찾기
                if not magnet_link:
                    download_icons = name_col.find_all('a', class_=lambda x: x and ('download' in x.lower() or 'magnet' in x.lower()))
                    for icon in download_icons:
                        href = icon.get('href', '')
                        if href and (href.startswith('magnet:') or href.startswith('magnet:?')):
                            magnet_link = href
                            break
                
                # 방법 5: 행 전체의 모든 링크에서 magnet URI 찾기
                if not magnet_link:
                    all_links = row.find_all('a')
                    for link in all_links:
                        href = link.get('href', '')
                        if href and (href.startswith('magnet:') or href.startswith('magnet:?')):
                            magnet_link = href
                            break
                
                # 방법 6: 행 전체에서 data-clipboard-text 속성 찾기
                if not magnet_link:
                    all_clipboard = row.find_all(attrs={'data-clipboard-text': True})
                    for elem in all_clipboard:
                        clipboard_text = elem.get('data-clipboard-text', '')
                        if clipboard_text and (clipboard_text.startswith('magnet:') or clipboard_text.startswith('magnet:?')):
                            magnet_link = clipboard_text
                            break
                
                # 방법 7: onclick 이벤트나 다른 속성에서 찾기
                if not magnet_link:
                    for elem in name_col.find_all(attrs={'onclick': True}):
                        onclick = elem.get('onclick', '')
                        if 'magnet:' in onclick:
                            # onclick에서 magnet 링크 추출
                            import re
                            match = re.search(r'magnet:\?[^\'"\s]+', onclick)
                            if match:
                                magnet_link = match.group(0)
                                break
                
                # 마그넷 링크가 없으면 스킵 (필수 데이터)
                if not magnet_link:
                    no_magnet_count += 1
                    # 디버깅: 첫 번째 실패만 상세 로그 출력
                    if no_magnet_count == 1:
                        print(f"[{self.name}] 마그넷 링크 없음 (첫 번째 예시): {title[:50]}... (view_url: {view_url})")
                        # 디버깅: name_col의 HTML 일부 출력
                        name_col_html = str(name_col)[:300] if name_col else ""
                        print(f"[{self.name}] 디버그 - name_col HTML: {name_col_html}...")
                    continue
                
                processed_count += 1
                
                # 크기
                size_col = columns[3]
                size = size_col.get_text(strip=True)
                size_bytes = self._convert_size_to_bytes(size)
                
                # 날짜
                date_col = columns[4]
                date_str = date_col.get('data-timestamp', '')
                upload_date = datetime.fromtimestamp(int(date_str)) if date_str else datetime.utcnow()
                
                # Seeders, Leechers, Downloads
                seeders = int(columns[5].get_text(strip=True) or 0)
                leechers = int(columns[6].get_text(strip=True) or 0)
                downloads = int(columns[7].get_text(strip=True) or 0)
                
                # 국가 및 검열 여부
                country, censored = self._detect_country_and_censorship(title)
                
                # 장르
                genres = self._detect_genres(title)
                
                torrents.append({
                    'title': title,
                    'source_id': source_id,
                    'source_site': 'sukebei.nyaa.si',
                    'magnet_link': magnet_link,
                    'torrent_link': '',
                    'size': size,
                    'size_bytes': size_bytes,
                    'category': category,
                    'censored': censored,
                    'country': country,
                    'seeders': seeders,
                    'leechers': leechers,
                    'downloads': downloads,
                    'comments': 0,
                    'upload_date': upload_date,
                    'thumbnail_url': '',
                    'snapshot_urls': '',
                    'genres': genres
                })
                
            except Exception as e:
                print(f"[{self.name}] 토렌트 파싱 오류: {e}")
                continue
        
        print(f"[{self.name}] 처리 완료: 총 {len(rows)}개 중 {processed_count}개 수집, {filtered_count}개 필터링, {no_magnet_count}개 마그넷 링크 없음")
        
        # 카테고리 통계 출력
        if category_stats:
            print(f"[{self.name}] 카테고리 통계:")
            for cat, count in sorted(category_stats.items(), key=lambda x: x[1], reverse=True):
                print(f"  - {cat}: {count}개")
        
        return torrents
    
    def _convert_size_to_bytes(self, size_str: str) -> int:
        """크기 문자열을 바이트로 변환"""
        try:
            parts = size_str.strip().split()
            if len(parts) != 2:
                return 0
            
            value = float(parts[0])
            unit = parts[1].upper()
            
            multipliers = {
                'B': 1, 'KIB': 1024, 'MIB': 1024**2,
                'GIB': 1024**3, 'TIB': 1024**4,
            }
            
            return int(value * multipliers.get(unit, 0))
        except:
            return 0
    
    def _detect_country_and_censorship(self, title: str) -> tuple:
        """국가 및 검열 여부 감지"""
        title_upper = title.upper()
        
        country = 'OTHER'
        if any(x in title for x in ['中文', '国产', '麻豆', '91']):
            country = 'CN'
        elif any(x in title for x in ['FC2', 'HEYZO', 'CARIB', 'SSNI', 'IPX']):
            country = 'JP'
        elif any(x in title for x in ['한국', 'KOREAN', 'BJ']):
            country = 'KR'
        
        censored = True
        if country == 'JP':
            if any(x in title.lower() for x in ['uncensored', '無修正', '无码', 'fc2', 'heyzo']):
                censored = False
        else:
            censored = False
        
        return country, censored
    
    def _detect_genres(self, title: str) -> List[str]:
        """장르 감지"""
        genres = []
        title_lower = title.lower()
        
        genre_keywords = {
            'Blowjob': ['blowjob', 'bj', 'oral', 'フェラ'],
            'Creampie': ['creampie', 'nakadashi', '中出し', '中出'],
            'Anal': ['anal', 'アナル'],
            'Threesome': ['threesome', '3p'],
            'Cosplay': ['cosplay', 'cos', 'コスプレ'],
            'Schoolgirl': ['schoolgirl', 'student', '制服', 'uniform'],
            'MILF': ['milf', 'mature', '熟女'],
            'Amateur': ['amateur', '素人', 'fc2'],
        }
        
        for genre, keywords in genre_keywords.items():
            if any(kw in title_lower for kw in keywords):
                genres.append(genre)
        
        return genres if genres else ['Amateur']

