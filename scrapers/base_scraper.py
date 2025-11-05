"""기본 스크래퍼 클래스"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup
import time
import random
import urllib3

# SSL 경고 무시
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import cloudscraper
    CLOUDSCRAPER_AVAILABLE = True
except ImportError:
    CLOUDSCRAPER_AVAILABLE = False
    print("⚠️  cloudscraper가 설치되지 않았습니다. 'pip install cloudscraper'로 설치하면 더 안정적입니다.")


class BaseScraper(ABC):
    """모든 스크래퍼의 기본 클래스"""
    
    def __init__(self, base_url: str, name: str = "Unknown", use_cloudscraper: bool = False):
        """
        Args:
            base_url: 스크래핑할 사이트의 기본 URL
            name: 스크래퍼 이름
            use_cloudscraper: cloudscraper 사용 여부 (기본값: False)
        """
        self.base_url = base_url
        self.name = name
        self.use_session = False  # Session 사용하지 않음 (연결 문제 해결)
        
        # 다양한 User-Agent 목록
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ]
        
        self.current_headers = self._get_random_headers()
    
    def _get_random_headers(self):
        """랜덤 헤더 생성"""
        return {
            'User-Agent': random.choice(self.user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,ko;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'close',  # Keep-alive 사용 안 함
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
    
    def get_page(self, url: str, params: Optional[Dict] = None, max_retries: int = 3) -> Optional[BeautifulSoup]:
        """페이지 요청 및 파싱 (재시도 로직 포함)
        
        Args:
            url: 요청할 URL
            params: 쿼리 파라미터
            max_retries: 최대 재시도 횟수
            
        Returns:
            BeautifulSoup 객체 또는 None
        """
        for attempt in range(max_retries):
            try:
                # 요청 간 딜레이
                if attempt > 0:
                    delay = random.uniform(0.5, 2.0)
                    print(f"[{self.name}] 재시도 {attempt}/{max_retries} - {delay:.1f}초 대기 중...")
                    time.sleep(delay)
                
                # 매번 새로운 헤더 생성
                headers = self._get_random_headers()
                
                # Session 없이 직접 요청 (ConnectionReset 문제 해결)
                print(f"[{self.name}] 연결 시도 중... (시도 {attempt + 1}/{max_retries})")
                response = requests.get(
                    url, 
                    params=params,
                    headers=headers,
                    timeout=15,
                    verify=False,  # SSL 검증 비활성화
                    allow_redirects=True,
                    stream=False  # 전체 응답 한번에 받기
                )
                
                # 상태 코드 확인
                response.raise_for_status()
                
                # 성공!
                print(f"[{self.name}] OK 연결 성공! (상태: {response.status_code}, 크기: {len(response.content)} bytes)")
                
                # 약간의 딜레이
                time.sleep(random.uniform(0.1, 1.0))  # 0.1-1초
                
                return BeautifulSoup(response.content, 'lxml')
                
            except requests.exceptions.SSLError as e:
                print(f"[{self.name}] SSL 오류 (시도 {attempt + 1}/{max_retries})")
                if attempt == max_retries - 1:
                    return None
                
            except requests.exceptions.ConnectionError as e:
                error_msg = str(e)
                print(f"[{self.name}] 연결 오류 (시도 {attempt + 1}/{max_retries})")
                
                if attempt == max_retries - 1:
                    print(f"[{self.name}] X 최대 재시도 초과")
                    print(f"  브라우저로는 접속되나요? 그렇다면:")
                    print(f"  1. 방화벽/백신 프로그램의 Python 차단 확인")
                    print(f"  2. VPN 사용 시도")
                    print(f"  3. 샘플 데이터로 테스트: python add_sample_data.py")
                    return None
                    
            except requests.exceptions.Timeout as e:
                print(f"[{self.name}] 시간 초과 (시도 {attempt + 1}/{max_retries})")
                if attempt == max_retries - 1:
                    return None
                    
            except requests.RequestException as e:
                print(f"[{self.name}] 요청 실패: {type(e).__name__}")
                if attempt == max_retries - 1:
                    return None
        
        return None
    
    @abstractmethod
    def scrape_page(self, page: int = 1) -> List[Dict]:
        """페이지에서 토렌트 정보 스크래핑
        
        Args:
            page: 페이지 번호
            
        Returns:
            토렌트 정보 딕셔너리 리스트
        """
        pass
    
    @abstractmethod
    def parse_torrent_item(self, item) -> Optional[Dict]:
        """개별 토렌트 아이템 파싱
        
        Args:
            item: HTML 요소
            
        Returns:
            토렌트 정보 딕셔너리 또는 None
        """
        pass
    
    def convert_size_to_bytes(self, size_str: str) -> int:
        """크기 문자열을 바이트로 변환
        
        Args:
            size_str: 크기 문자열 (예: "1.5 GiB", "500 MiB")
            
        Returns:
            바이트 단위 크기
        """
        try:
            size_str = size_str.strip()
            parts = size_str.split()
            if len(parts) != 2:
                return 0
            
            value = float(parts[0])
            unit = parts[1].upper()
            
            multipliers = {
                'B': 1,
                'KIB': 1024,
                'MIB': 1024 ** 2,
                'GIB': 1024 ** 3,
                'TIB': 1024 ** 4,
                'KB': 1000,
                'MB': 1000 ** 2,
                'GB': 1000 ** 3,
                'TB': 1000 ** 4,
            }
            
            return int(value * multipliers.get(unit, 0))
        except (ValueError, IndexError):
            return 0

