"""Selenium 기반 베이스 스크래퍼"""
from typing import Optional
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time
import random
from abc import ABC, abstractmethod


class SeleniumBaseScraper(ABC):
    """Selenium 기반 스크래퍼의 베이스 클래스"""
    
    def __init__(self, base_url: str, name: str):
        """
        Args:
            base_url: 스크래핑할 사이트의 기본 URL
            name: 스크래퍼 이름
        """
        self.base_url = base_url
        self.name = name
        self.driver = None
        self.driver_initialized = False
        
        # User-Agent 목록
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ]
    
    def _init_driver(self):
        """Chrome WebDriver 초기화"""
        if self.driver_initialized and self.driver:
            return
        
        print(f"[{self.name}] Chrome 브라우저 초기화 중...")
        try:
            chrome_options = Options()
            chrome_options.add_argument("--headless")  # GUI 없이
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument(f"user-agent={random.choice(self.user_agents)}")
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            
            # WebDriver 속성 숨기기 (봇 감지 우회)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            self.driver_initialized = True
            print(f"[{self.name}] OK Chrome 브라우저 준비 완료!")
            
        except Exception as e:
            print(f"[{self.name}] X 브라우저 초기화 실패: {e}")
            self.driver = None
            self.driver_initialized = False
            raise
    
    def get_page_selenium(self, url: str, wait_time: int = 5) -> Optional[BeautifulSoup]:
        """Selenium으로 페이지 가져오기
        
        Args:
            url: 요청할 URL
            wait_time: 페이지 로드 대기 시간 (초)
            
        Returns:
            BeautifulSoup 객체 또는 None
        """
        try:
            # 드라이버 초기화
            if not self.driver:
                self._init_driver()
            
            print(f"[{self.name}] 페이지 로드 중: {url}")
            
            # 페이지 로드
            self.driver.get(url)
            
            # 페이지 로드 대기
            time.sleep(random.uniform(0.5, 1.5))
            
            # body 태그가 로드될 때까지 대기
            try:
                WebDriverWait(self.driver, wait_time).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
            except:
                pass
            
            # 페이지 소스 가져오기
            page_source = self.driver.page_source
            
            print(f"[{self.name}] OK 페이지 로드 완료 ({len(page_source)} bytes)")
            
            # BeautifulSoup으로 파싱
            return BeautifulSoup(page_source, 'lxml')
            
        except Exception as e:
            print(f"[{self.name}] X 페이지 로드 실패: {e}")
            return None
    
    def close(self):
        """WebDriver 종료"""
        if self.driver:
            print(f"[{self.name}] 브라우저 종료")
            try:
                self.driver.quit()
            except:
                pass
            self.driver = None
            self.driver_initialized = False
    
    @abstractmethod
    def scrape_page(self, page: int = 1, **kwargs):
        """페이지 스크래핑 (하위 클래스에서 구현)"""
        pass
    
    def __del__(self):
        """소멸자: 드라이버 정리"""
        self.close()

