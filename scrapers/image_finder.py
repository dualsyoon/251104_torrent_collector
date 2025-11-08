"""썸네일 및 스냅샷 이미지 검색"""
import requests
from bs4 import BeautifulSoup
import re
from typing import List, Optional
import time
import random
import json
import os
import pathlib
from urllib.parse import quote, urljoin
from requests.exceptions import RequestException, ConnectionError, Timeout
from config import ENABLE_JAVDB_FALLBACK, ENABLE_SELENIUM_FOR_IMAGES, IMAGE_HTTP_TIMEOUT, IMAGE_HTTP_RETRIES, PROXY_URL
from PySide6.QtCore import QSettings

# Selenium 사용 가능 여부 확인
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


class ImageFinder:
    """토렌트 제목으로 썸네일 이미지 검색"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        # JAVDB 연결 실패 시 자동 비활성화 플래그
        qs = QSettings()
        self.enable_javdb = qs.value('images/enable_javdb_fallback', ENABLE_JAVDB_FALLBACK, type=bool)
        self.javdb_available = self.enable_javdb
        self.javdb_fail_count = 0
        
        # JAVLibrary HTTP 차단 감지 플래그
        self.javlibrary_blocked = False  # HTTP 403이 발생하면 True로 설정
        self.http_timeout = max(3, int(qs.value('images/image_http_timeout', IMAGE_HTTP_TIMEOUT)))
        self.http_retries = max(0, int(qs.value('images/image_http_retries', IMAGE_HTTP_RETRIES)))
        # 공통 플레이스홀더/썸네일 차단 리스트
        self.blocked_thumbnails = {
            'pics.pornfhd.com/storage80000/file/389/38821405/1686033062.79.jpg',
            'trim-fanza.jav-clips.com/images/ogp/javclips1200_630.png',
            'i2.hdslb.com/bfs/archive',  # 부분 매치
            'ytimg.com/vi/',  # YouTube 썸네일
            'urlimage.cc/attachments',  # 포럼 이미지
            'pbs.twimg.com/media',  # Twitter 이미지
        }


        # Selenium 드라이버 재사용 (성능 최적화)
        self.selenium_driver = None
        self.selenium_use_count = 0
        self.selenium_max_reuse = 20  # 20회 사용 후 재시작



    def _safe_get(self, url: str, headers: Optional[dict] = None, params: Optional[dict] = None, timeout: Optional[int] = None):
        """간단한 재시도 포함 GET 요청"""
        last_exc = None
        tries = min(self.http_retries + 1, 2)  # 최대 2회 시도로 제한하여 속도 개선
        for _ in range(tries):
            try:
                resp = self.session.get(url, headers=headers, params=params, timeout=timeout or self.http_timeout)
                return resp
            except (ConnectionError, Timeout, RequestException) as e:
                last_exc = e
                time.sleep(0.1)  # 대기 시간 단축
                continue
        if last_exc:
            raise last_exc
        return None
    
    def search_images(self, title: str, max_images: int = 5, exclude_hosts: List[str] = None) -> dict:
        """제목으로 이미지 검색 (MissAV → JAVLibrary → JAVDB → FC2PPV.stream)
        exclude_hosts: 해당 호스트를 포함하는 URL은 제외 (교체 기능용)
        """
        codes = self._extract_codes(title)
        image_urls: List[str] = []

        def _filter_urls(urls: List[str]) -> List[str]:
            filtered: List[str] = []
            for u in urls:
                if not u:
                    continue
                if self._is_blocked_thumbnail(u):
                    continue
                if exclude_hosts:
                    try:
                        from urllib.parse import urlparse
                        host = urlparse(u).netloc.lower()
                    except Exception:
                        host = ''
                    skip = any((ex or '').lower() in host for ex in exclude_hosts)
                    if skip:
                        continue
                if u not in filtered:
                    filtered.append(u)
            return filtered
        
        # FC2 코드 추출 (백업용)
        fc2_codes = []
        pattern_fc2 = r'FC2[-\s]?PPV[-\s]?(\d{6,8})'
        matches = re.findall(pattern_fc2, title.upper())
        if matches:
            fc2_codes = matches[:1]  # 첫 번째만 사용
        
        # 1단계: 작품번호로 검색
        if codes:
            print(f"[ImageFinder] 1단계: 작품번호로 검색 ({', '.join(codes)})")

            # 1) MissAV 최우선 시도 (Selenium 사용)
            if ENABLE_SELENIUM_FOR_IMAGES:
                for code in codes:
                    urls = self._search_missav_selenium(code)
                    image_urls.extend(urls)
                    if len(image_urls) >= max_images:
                        break
                
                # MissAV 결과가 있으면 바로 리턴
                if image_urls:
                    image_urls = _filter_urls(image_urls)
                    if image_urls:
                        print(f"[ImageFinder] MissAV 성공!")
                        return {
                            'thumbnail': image_urls[0],
                            'snapshots': []
                        }

            # 2) JAVLibrary 시도
            for code in codes:
                urls = self._search_javdatabase(code)
                for u in urls:
                    if u not in image_urls:
                        image_urls.append(u)
                        if len(image_urls) >= max_images:
                            break
                if len(image_urls) >= max_images:
                    break

            # 결과가 있으면 바로 리턴 (빠른 응답)
            if image_urls:
                image_urls = _filter_urls(image_urls)
                if image_urls:
                    print(f"[ImageFinder] JAVLibrary 성공!")
                    return {
                        'thumbnail': image_urls[0],
                        'snapshots': []
                    }
            
            # 3) JAVDB 시도 (Selenium 활성화 시 Selenium만, 아니면 HTTP만)
            for code in codes:
                if ENABLE_SELENIUM_FOR_IMAGES:
                    # Selenium만 사용 (HTTP는 대부분 차단됨)
                    urls = self._search_javdb_selenium(code)
                    image_urls.extend(urls)
                else:
                    # HTTP만 사용
                    urls_http = self._search_javdb(code)
                    image_urls.extend(urls_http)
                
                if len(image_urls) >= max_images:
                    break
            
            # 4) 결과가 있으면 바로 리턴 (빠른 응답)
            if image_urls:
                image_urls = _filter_urls(image_urls)
                if image_urls:
                    print(f"[ImageFinder] JAVDB 성공!")
                    return {
                        'thumbnail': image_urls[0],
                        'snapshots': []
                    }
            
            # 5) 최후의 수단: FC2PPV.stream (FC2 코드가 있을 때만)
            if fc2_codes:
                for fc2_code in fc2_codes:
                    urls = self._search_fc2ppv_stream(fc2_code)
                    image_urls.extend(urls)
                    if len(image_urls) >= max_images:
                        break
            
            # 6) FC2PPV.stream 결과가 있으면 리턴
            if image_urls:
                image_urls = _filter_urls(image_urls)
                if image_urls:
                    # 성공 메시지는 제거 (출력이 너무 많음)
                    return {
                        'thumbnail': image_urls[0],
                        'snapshots': []
                    }
        
        # 2단계: 작품번호로 실패 시 전체 제목으로 재시도
        print(f"[ImageFinder] 2단계: 전체 제목으로 재검색...")
        
        # MissAV 먼저 시도 (Selenium 사용 시)
        if ENABLE_SELENIUM_FOR_IMAGES:
            urls = self._search_missav_selenium(title)
            image_urls.extend(urls)
        
        # MissAV 실패 시 JAVLibrary 시도
        if not image_urls:
            urls = self._search_javdatabase(title)
            image_urls.extend(urls)
        
        # JAVLibrary 실패 시 JAVDB 시도
        if not image_urls:
            if ENABLE_SELENIUM_FOR_IMAGES:
                urls = self._search_javdb_selenium(title)
                image_urls.extend(urls)
            else:
                urls_http = self._search_javdb(title)
                image_urls.extend(urls_http)
        
        # JAVDB 실패 시 JAVBee 시도
        if not image_urls:
            if codes:
                for code in codes:
                    urls = self._search_javbee(code, title=title)
                    image_urls.extend(urls)
                    if image_urls:
                        break
            elif fc2_codes:
                for fc2_code in fc2_codes:
                    urls = self._search_javbee(f"FC2-PPV-{fc2_code}", title=title)
                    image_urls.extend(urls)
                    if image_urls:
                        break
        
        # JAVBee 실패 시 FC2PPV.stream 시도 (FC2 코드가 있을 때만)
        if not image_urls and fc2_codes:
            for fc2_code in fc2_codes:
                urls = self._search_fc2ppv_stream(fc2_code)
                image_urls.extend(urls)
                if image_urls:
                    break
        
        # 필터링
        image_urls = _filter_urls(image_urls)
        
        if image_urls:
            print(f"[ImageFinder] 전체 제목 검색 성공!")
        else:
            print(f"[ImageFinder] 썸네일 검색 실패")
        
        return {
            'thumbnail': image_urls[0] if image_urls else '',
            'snapshots': []  # 스냅샷 비활성화
        }
    
    def _is_blocked_thumbnail(self, url: str) -> bool:
        """플레이스홀더/공통 썸네일 차단"""
        if not url:
            return True
        url_lower = url.lower()
        
        # 아이콘 파일 차단 (favicon.ico, .ico 확장자 등)
        if 'favicon' in url_lower or url_lower.endswith('.ico'):
            return True
        
        # 기타 아이콘 관련 파일명 차단
        icon_patterns = ['/icon.', '/icons/', '/favicon', 'apple-touch-icon', 'android-chrome']
        if any(pattern in url_lower for pattern in icon_patterns):
            return True
        
        # javbee.vip/storage/ 경로의 특정 이미지 차단 (썸네일이 아닌 이미지)
        if 'javbee.vip/storage/' in url_lower:
            return True
        
        # javbee.vip/images/loading.gif 차단 (로딩 이미지)
        if 'javbee.vip/images/loading' in url_lower:
            return True
        
        # 차단 리스트 확인
        for blocked in self.blocked_thumbnails:
            if blocked in url_lower:
                return True
        return False
    
    def _extract_codes(self, title: str) -> List[str]:
        """제목에서 작품 코드 추출
        
        예: "IPX-123", "FC2-1234567", "SSNI-456"
        """
        codes = []
        title_upper = title.upper()
        
        # FC2 패턴 우선 (예: FC2-PPV-1234567 또는 FC2-1234567)
        pattern_fc2 = r'FC2[-\s]?(PPV[-\s]?)?(\d{6,8})'
        matches_fc2 = re.findall(pattern_fc2, title_upper)
        for match in matches_fc2:
            ppv_part, fc2_num = match
            if ppv_part:  # PPV가 있으면 FC2-PPV-숫자
                codes.append(f"FC2-PPV-{fc2_num}")
            else:  # 없으면 FC2-숫자
                codes.append(f"FC2-{fc2_num}")
        
        # 일반 AV 코드 패턴 (예: IPX-358, MIDA-398, COGM-089, STARS-573)
        # 문자 길이를 1~10자로 확장하여 다양한 케이스 지원
        pattern_av = r'([A-Z]{1,10})[-\s]?(\d{3,6})(?=[^\w]|$)'
        matches_av = re.findall(pattern_av, title_upper)
        
        # 제외할 키워드 목록 (코덱, 포맷, 프로토콜 등)
        # FC2는 이미 별도 패턴으로 처리되므로 일반 패턴에서 제외
        excluded_prefixes = {
            'HTTP', 'HTTPS', 'HTML', 'URL', 'API', 'JPG', 'PNG', 'MP4', 'MKV', 
            # 비디오 코덱/인코더
            'H265', 'H264', 'H-265', 'H-264', 'HEVC', 'AVC',
            'X265', 'X264', 'X-265', 'X-264',
            # 기타 비디오 관련
            '1080P', '1080', '720P', '720', '480P', '480',
            '4K', 'UHD', 'HD',
            'MPEG', 'MJPEG', 'DIVX', 'XVID',
            # 오디오 코덱
            'AAC', 'MP3', 'FLAC', 'OGG', 'VORBIS',
            # 기타
            'BLU', 'RAY', 'DVD', 'ISO', 'RAR', 'ZIP', '7Z'
        }
        
        # 전체 코드 형태로도 제외할 목록 (코덱 정보 등)
        excluded_codes = {
            'H-265', 'H-264', 'H265', 'H264',
            'X-265', 'X-264', 'X265', 'X264'
        }
        
        for match in matches_av:
            prefix, number = match
            # 전체 코드 형태 생성
            code = f"{prefix}-{number}"
            
            # 제외 목록에 없고, 실제 작품번호처럼 보이는 것만 추가
            # prefix와 전체 code 모두 체크 (FC2는 이미 별도 패턴으로 처리되므로 제외)
            if prefix not in excluded_prefixes and code.upper() not in excluded_codes:
                # FC2는 이미 별도 패턴으로 처리했으므로 일반 패턴에서는 제외
                if prefix == 'FC2':
                    continue
                # 숫자가 너무 작거나 크면 제외 (작품번호는 보통 3-6자리)
                if len(number) >= 3 and len(number) <= 6:
                    codes.append(code)
        
        # 중복 제거 (순서 유지)
        codes = list(dict.fromkeys(codes))
        
        # 디버그 출력
        # 작품번호 추출 로그는 제거 (출력이 너무 많음)
        
        return codes[:3]  # 최대 3개
    
    def _search_fc2_adult_contents(self, fc2_number: str) -> dict:
        """https://adult.contents.fc2.com/에서 FC2 번호로 썸네일 및 스냅샷 검색
        
        Args:
            fc2_number: FC2 번호 (숫자만, 예: "4790416")
        
        Returns:
            dict: {'thumbnail': str, 'snapshots': List[str]}
        """
        result = {'thumbnail': '', 'snapshots': []}
        try:
            # FC2 번호만 추출 (숫자만)
            fc2_num = re.sub(r'[^\d]', '', fc2_number)
            if not fc2_num or len(fc2_num) < 6:
                return result
            
            # FC2 공식 사이트 URL (번호만 사용)
            product_url = f"https://adult.contents.fc2.com/article/{fc2_num}/"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
                'Referer': 'https://adult.contents.fc2.com/'
            }
            
            try:
                response = self._safe_get(product_url, headers=headers, timeout=self.http_timeout)
                if response and response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'lxml')
                else:
                    # HTTP 실패 시 Selenium으로 시도
                    if SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES:
                        return self._search_fc2_adult_contents_selenium(fc2_num)
                    return result
            except (ConnectionError, Timeout, RequestException) as e:
                # 네트워크 오류 시 Selenium으로 시도
                if SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES:
                    return self._search_fc2_adult_contents_selenium(fc2_num)
                return result
            
            # 썸네일 찾기 (메인 이미지)
            # 일반적으로 article 페이지에 있는 메인 이미지
            thumbnail_selectors = [
                'img.item_head_image',
                'img.main_image',
                '.item_head_image img',
                '.main_image img',
                'article img[src*="fc2.com"]',
                'img[src*="thumbnail"]',
                '.item_head img',
                'img[src*="article"]'
            ]
            
            for selector in thumbnail_selectors:
                img = soup.select_one(selector)
                if img:
                    img_src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                    if img_src:
                        if not img_src.startswith('http'):
                            if img_src.startswith('//'):
                                img_src = 'https:' + img_src
                            elif img_src.startswith('/'):
                                img_src = 'https://adult.contents.fc2.com' + img_src
                            else:
                                img_src = urljoin('https://adult.contents.fc2.com/', img_src)
                        if img_src.startswith('http'):
                            result['thumbnail'] = img_src
                            # 찾은 경우에만 출력 (출력이 너무 많음)
                            break
            
            # 스냅샷 찾기 (앨범 이미지들)
            snapshot_selectors = [
                '.snapshot img',
                '.album img',
                '.gallery img',
                '.snapshots img',
                'img[src*="snapshot"]',
                'img[src*="album"]',
                '.item_images img',
                '.sample_images img'
            ]
            
            for selector in snapshot_selectors:
                images = soup.select(selector)
                if images:
                    for img in images:
                        img_src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                        if img_src:
                            if not img_src.startswith('http'):
                                if img_src.startswith('//'):
                                    img_src = 'https:' + img_src
                                elif img_src.startswith('/'):
                                    img_src = 'https://adult.contents.fc2.com' + img_src
                                else:
                                    img_src = urljoin('https://adult.contents.fc2.com/', img_src)
                            if img_src.startswith('http') and img_src not in result['snapshots']:
                                # 썸네일과 중복되지 않는 경우만 추가
                                if img_src != result['thumbnail']:
                                    result['snapshots'].append(img_src)
                    
                    if result['snapshots']:
                        break
            
            return result
            
        except Exception as e:
            # 오류 메시지는 출력하지 않음 (출력이 너무 많음)
            return result
    
    def _search_fc2_adult_contents_selenium(self, fc2_num: str) -> dict:
        """Selenium을 이용한 FC2 공식 사이트 검색 (HTTP 실패 시 대체)"""
        result = {'thumbnail': '', 'snapshots': []}
        if not (SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES):
            return result
        
        try:
            driver = self._get_selenium_driver()
            self.selenium_use_count += 1
            
            product_url = f"https://adult.contents.fc2.com/article/{fc2_num}/"
            driver.get(product_url)
            
            # 페이지 로딩 대기
            try:
                WebDriverWait(driver, 3).until(
                    EC.presence_of_element_located((By.TAG_NAME, "img"))
                )
            except:
                time.sleep(0.5)
            
            page_source = driver.page_source
            soup = BeautifulSoup(page_source, 'lxml')
            
            # 썸네일 찾기
            thumbnail_selectors = [
                'img.item_head_image',
                'img.main_image',
                '.item_head_image img',
                '.main_image img',
                'article img[src*="fc2.com"]',
                'img[src*="thumbnail"]',
                '.item_head img',
                'img[src*="article"]'
            ]
            
            for selector in thumbnail_selectors:
                img = soup.select_one(selector)
                if img:
                    img_src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                    if img_src:
                        if not img_src.startswith('http'):
                            if img_src.startswith('//'):
                                img_src = 'https:' + img_src
                            elif img_src.startswith('/'):
                                img_src = 'https://adult.contents.fc2.com' + img_src
                            else:
                                img_src = urljoin('https://adult.contents.fc2.com/', img_src)
                        if img_src.startswith('http'):
                            result['thumbnail'] = img_src
                            break
            
            # 스냅샷 찾기
            snapshot_selectors = [
                '.snapshot img',
                '.album img',
                '.gallery img',
                '.snapshots img',
                'img[src*="snapshot"]',
                'img[src*="album"]',
                '.item_images img',
                '.sample_images img'
            ]
            
            for selector in snapshot_selectors:
                images = soup.select(selector)
                if images:
                    for img in images:
                        img_src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                        if img_src:
                            if not img_src.startswith('http'):
                                if img_src.startswith('//'):
                                    img_src = 'https:' + img_src
                                elif img_src.startswith('/'):
                                    img_src = 'https://adult.contents.fc2.com' + img_src
                                else:
                                    img_src = urljoin('https://adult.contents.fc2.com/', img_src)
                            if img_src.startswith('http') and img_src not in result['snapshots']:
                                if img_src != result['thumbnail']:
                                    result['snapshots'].append(img_src)
                    
                    if result['snapshots']:
                        break
            
            return result
            
        except Exception as e:
            # 오류 메시지는 출력하지 않음
            return result
    
    def _search_fc2ppv_stream(self, fc2_code: str) -> List[str]:
        """FC2PPV.stream에서 이미지 검색"""
        try:
            # FC2PPV.stream 검색 URL
            search_url = f"https://fc2ppv.stream/?s=FC2-PPV-{fc2_code}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://fc2ppv.stream/'
            }
            
            response = self._safe_get(search_url, headers=headers, timeout=self.http_timeout)
            if response.status_code != 200:
                return []
            
            soup = BeautifulSoup(response.content, 'lxml')
            image_urls = []
            
            # article 내 img 태그에서 이미지 찾기
            articles = soup.find_all('article')
            if articles:
                # 첫 번째 article의 이미지
                first_article = articles[0]
                img = first_article.find('img')
                if img:
                    img_src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                    if img_src:
                        # 상대 URL 처리
                        if not img_src.startswith('http'):
                            if img_src.startswith('//'):
                                img_src = 'https:' + img_src
                            elif img_src.startswith('/'):
                                img_src = 'https://fc2ppv.stream' + img_src
                            else:
                                img_src = urljoin('https://fc2ppv.stream', img_src)
                        if img_src.startswith('http'):
                            image_urls.append(img_src)
                            print(f"[ImageFinder] FC2PPV.stream에서 이미지 발견: FC2-PPV-{fc2_code}")
            
            return image_urls
            
        except Exception as e:
            print(f"[ImageFinder] FC2PPV.stream 검색 오류 (FC2-PPV-{fc2_code}): {e}")
            return []

    def _search_javdatabase(self, code: str) -> List[str]:
        """JAV Database에서 이미지 검색"""
        image_urls = []
        
        # 1. JAVLibrary 검색
        try:
            urls = self._search_javlibrary(code)
            image_urls.extend(urls)
        except Exception as e:
            print(f"[ImageFinder] JAVLibrary 검색 실패: {e}")
        
        # 2. JAVDB 검색 (백업 - 설정 및 가용 여부 확인)
        if not image_urls and self.javdb_available and self.enable_javdb:
            try:
                urls = self._search_javdb(code)
                image_urls.extend(urls)
            except Exception as e:
                # 개별 오류는 과도하게 찍지 않음
                pass
        
        return image_urls
    
    def _search_javlibrary(self, code: str) -> List[str]:
        """JAVLibrary.com에서 이미지 검색"""
        # HTTP 차단되었으면 바로 Selenium 사용
        if self.javlibrary_blocked:
            if SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES:
                return self._search_javlibrary_selenium(code)
            return []
        
        image_urls = []
        
        # 방법 1: 직접 작품 페이지 URL 시도 (영어/일본어 버전)
        for lang in ['en', 'ja']:
            try:
                direct_url = f"https://www.javlibrary.com/{lang}/?v={quote(code)}"
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
                    'Referer': f'https://www.javlibrary.com/{lang}/'
                }
                
                response = self._safe_get(direct_url, headers=headers, timeout=self.http_timeout)
                # HTTP 403이면 차단 플래그 설정하고 Selenium으로 전환
                if response.status_code == 403:
                    if not self.javlibrary_blocked:
                        self.javlibrary_blocked = True
                        # HTTP 403 감지 시 Selenium으로 전환 (로그 생략)
                    if SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES:
                        selenium_urls = self._search_javlibrary_selenium(code)
                        if selenium_urls:
                            return selenium_urls
                    # 차단되었으므로 나머지 HTTP 요청도 시도하지 않음
                    break  # 루프 종료하고 Selenium으로 전환
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'lxml')
                    
                    # 썸네일 이미지 찾기 (여러 방법 시도)
                    cover_img = soup.find('img', id='video_jacket_img')
                    if not cover_img:
                        cover_img = soup.find('img', {'id': 'video_jacket'})
                    if not cover_img:
                        cover_img = soup.find('img', class_='cover')
                    if not cover_img:
                        # img 태그 중 src에 'cover' 또는 'jacket'가 포함된 것 찾기
                        for img in soup.find_all('img'):
                            img_src = img.get('src', '') or img.get('data-src', '')
                            if img_src and ('cover' in img_src.lower() or 'jacket' in img_src.lower()):
                                cover_img = img
                                break
                    
                    # DMM 이미지 URL도 찾기 (페이지 내 링크나 이미지에서)
                    dmm_url = None
                    page_text = str(soup)
                    # DMM 이미지 URL 패턴 찾기: pics.dmm.co.jp/mono/movie/adult/{code}/
                    code_clean = code.replace('-', '').replace('_', '').lower()
                    dmm_patterns = [
                        f'pics.dmm.co.jp/mono/movie/adult/{code_clean}/{code_clean}pl.jpg',
                        f'pics.dmm.co.jp/mono/movie/adult/{code_clean}/{code_clean}ps.jpg',
                        f'pics.dmm.co.jp/digital/video/{code_clean}/{code_clean}pl.jpg',
                    ]
                    for pattern in dmm_patterns:
                        if pattern in page_text:
                            # URL 추출
                            import re
                            dmm_match = re.search(r'https?://[^"\s<>]+' + re.escape(pattern.replace(code_clean, r'[^"\s<>]+')), page_text)
                            if dmm_match:
                                dmm_url = dmm_match.group(0)
                                break
                            else:
                                # 패턴으로 직접 URL 생성
                                dmm_url = f'https://{pattern}'
                                break
                    
                    if cover_img:
                        img_src = cover_img.get('src', '') or cover_img.get('data-src', '') or cover_img.get('data-lazy-src', '')
                        if img_src:
                            # 상대 URL 처리
                            if not img_src.startswith('http'):
                                if img_src.startswith('//'):
                                    img_src = 'https:' + img_src
                                elif img_src.startswith('/'):
                                    img_src = f'https://www.javlibrary.com{img_src}'
                                else:
                                    img_src = urljoin(f'https://www.javlibrary.com/{lang}/', img_src)
                            # 실제 이미지 URL인지 확인
                            is_image_url = (
                                img_src.startswith('http') and
                                (
                                    any(img_src.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']) or
                                    '/image' in img_src.lower() or 
                                    '/img' in img_src.lower() or 
                                    '/cover' in img_src.lower() or 
                                    '/jacket' in img_src.lower() or
                                    '/pl' in img_src.lower() or  # DMM 이미지 패턴
                                    '/ps' in img_src.lower()    # DMM 이미지 패턴
                                )
                            )
                            
                            if is_image_url:
                                image_urls.append(img_src)
                            # 이미지가 아닌 URL은 스킵 (로그 생략)
                    
                    # DMM URL이 있으면 추가 (우선순위 높음)
                    if dmm_url:
                        image_urls.insert(0, dmm_url)  # 맨 앞에 추가
                        print(f"[ImageFinder] JAVLibrary에서 DMM 이미지 발견: {code} -> {dmm_url}")
                    
                    if image_urls:
                        print(f"[ImageFinder] JAVLibrary에서 이미지 발견 (직접 URL, {lang}): {code}")
                        return image_urls  # 찾았으면 바로 반환
            except Exception as e:
                # 직접 URL 실패는 조용히 넘어감 (다음 방법 시도)
                pass
        
        # 방법 2: 검색 페이지를 통한 검색 (기존 방법)
        # 차단 플래그 확인 (이미 차단되었으면 HTTP 요청 시도 안 함)
        if self.javlibrary_blocked:
            if SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES:
                selenium_urls = self._search_javlibrary_selenium(code)
                if selenium_urls:
                    return selenium_urls
            return image_urls
        
        try:
            # 일본어 버전 검색
            search_url = f"https://www.javlibrary.com/ja/vl_searchbyid.php?keyword={quote(code)}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
                'Referer': 'https://www.javlibrary.com/ja/'
            }
            
            response = self._safe_get(search_url, headers=headers, timeout=self.http_timeout)
            if response.status_code != 200:
                # HTTP 403 (Forbidden) 오류면 차단 플래그 설정하고 Selenium으로 시도
                if response.status_code == 403:
                    if not self.javlibrary_blocked:
                        self.javlibrary_blocked = True
                        # HTTP 403 감지 시 Selenium으로 전환 (로그 생략)
                    if SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES:
                        selenium_urls = self._search_javlibrary_selenium(code)
                        if selenium_urls:
                            return selenium_urls
                    return image_urls  # 403이면 조용히 반환 (이미 메시지 출력됨)
                # 403이 아닌 다른 오류만 로그 출력
                print(f"[ImageFinder] JAVLibrary 검색 페이지 HTTP {response.status_code}: {code}")
                return image_urls
            
            soup = BeautifulSoup(response.content, 'lxml')
            
            # 작품 페이지 링크 찾기 (여러 방법 시도)
            video_link = soup.find('a', class_='video')
            if not video_link:
                # 다른 방법: div.video로 찾기
                video_div = soup.find('div', class_='video')
                if video_div:
                    video_link = video_div.find('a')
            
            if not video_link:
                print(f"[ImageFinder] JAVLibrary 검색 결과에서 작품 링크를 찾지 못함: {code}")
                return image_urls
            
            href = video_link.get('href', '')
            if not href:
                print(f"[ImageFinder] JAVLibrary 작품 링크에 href가 없음: {code}")
                return image_urls
            
            # 상대 URL 처리
            if href.startswith('/'):
                video_url = f"https://www.javlibrary.com{href}"
            elif href.startswith('http'):
                video_url = href
            else:
                video_url = urljoin('https://www.javlibrary.com/ja/', href)
            
            # 상세 페이지에서 이미지 가져오기
            detail_response = self._safe_get(video_url, headers=headers, timeout=self.http_timeout)
            if detail_response.status_code != 200:
                # HTTP 403이면 차단 플래그 설정하고 Selenium으로 전환
                if detail_response.status_code == 403:
                    if not self.javlibrary_blocked:
                        self.javlibrary_blocked = True
                        # HTTP 403 감지 시 Selenium으로 전환 (로그 생략)
                    if SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES:
                        selenium_urls = self._search_javlibrary_selenium(code)
                        if selenium_urls:
                            return selenium_urls
                    return image_urls  # 403이면 조용히 반환 (이미 메시지 출력됨)
                # 403이 아닌 다른 오류만 로그 출력
                print(f"[ImageFinder] JAVLibrary 상세 페이지 HTTP {detail_response.status_code}: {code}")
                return image_urls
            
            detail_soup = BeautifulSoup(detail_response.content, 'lxml')
            
            # DMM 이미지 URL도 찾기 (페이지 내 링크나 이미지에서)
            dmm_url = None
            page_text = str(detail_soup)
            # DMM 이미지 URL 패턴 찾기: pics.dmm.co.jp/mono/movie/adult/{code}/
            code_clean = code.replace('-', '').replace('_', '').lower()
            dmm_patterns = [
                f'pics.dmm.co.jp/mono/movie/adult/{code_clean}/{code_clean}pl.jpg',
                f'pics.dmm.co.jp/mono/movie/adult/{code_clean}/{code_clean}ps.jpg',
                f'pics.dmm.co.jp/digital/video/{code_clean}/{code_clean}pl.jpg',
            ]
            for pattern in dmm_patterns:
                if pattern in page_text:
                    # URL 추출
                    import re
                    dmm_match = re.search(r'https?://[^"\s<>]+' + re.escape(pattern.replace(code_clean, r'[^"\s<>]+')), page_text)
                    if dmm_match:
                        dmm_url = dmm_match.group(0)
                        break
                    else:
                        # 패턴으로 직접 URL 생성
                        dmm_url = f'https://{pattern}'
                        break
            
            # 썸네일 이미지 (여러 방법 시도)
            cover_img = detail_soup.find('img', id='video_jacket_img')
            if not cover_img:
                cover_img = detail_soup.find('img', {'id': 'video_jacket'})
            if not cover_img:
                cover_img = detail_soup.find('img', class_='cover')
            if not cover_img:
                # img 태그 중 src에 'cover' 또는 'jacket'가 포함된 것 찾기
                for img in detail_soup.find_all('img'):
                    img_src = img.get('src', '') or img.get('data-src', '')
                    if img_src and ('cover' in img_src.lower() or 'jacket' in img_src.lower()):
                        cover_img = img
                        break
            
            if cover_img:
                img_src = cover_img.get('src', '') or cover_img.get('data-src', '') or cover_img.get('data-lazy-src', '')
                if img_src:
                    # 상대 URL 처리
                    if not img_src.startswith('http'):
                        if img_src.startswith('//'):
                            img_src = 'https:' + img_src
                        elif img_src.startswith('/'):
                            img_src = 'https://www.javlibrary.com' + img_src
                        else:
                            img_src = urljoin('https://www.javlibrary.com/ja/', img_src)
                    if img_src.startswith('http'):
                        image_urls.append(img_src)
            
            # DMM URL이 있으면 추가 (우선순위 높음)
            if dmm_url:
                image_urls.insert(0, dmm_url)  # 맨 앞에 추가
                print(f"[ImageFinder] JAVLibrary에서 DMM 이미지 발견: {code} -> {dmm_url}")
            
            if image_urls:
                print(f"[ImageFinder] JAVLibrary에서 이미지 발견 (검색): {code}")
            else:
                print(f"[ImageFinder] JAVLibrary 상세 페이지에서 이미지를 찾지 못함: {code}")
            
            return image_urls
            
        except Exception as e:
            print(f"[ImageFinder] JAVLibrary 검색 오류 ({code}): {e}")
            import traceback
            print(f"[ImageFinder] JAVLibrary 검색 오류 상세: {traceback.format_exc()}")
            return []
    
    def _search_javdb(self, code: str) -> List[str]:
        """JAVDB.com에서 이미지 검색"""
        if not self.javdb_available:
            return []
        try:
            # JAVDB 검색 URL
            search_url = f"https://javdb.com/search?q={quote(code)}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://javdb.com/'
            }
            
            response = self._safe_get(search_url, headers=headers, timeout=self.http_timeout)
            if response.status_code != 200:
                # HTTP 에러면 셀레니움 우회 시도
                if SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES:
                    return self._search_javdb_selenium(code)
                return []
            
            soup = BeautifulSoup(response.content, 'lxml')
            image_urls = []
            
            # 작품 카드에서 이미지 찾기 (여러 클래스명 시도)
            video_cards = soup.find_all('div', class_='item')
            if not video_cards:
                video_cards = soup.find_all('div', class_='movie-item')
            if not video_cards:
                video_cards = soup.find_all('a', class_='box')
            
            if video_cards:
                # 첫 번째 결과의 이미지
                first_card = video_cards[0]
                img = first_card.find('img')
                if img:
                    img_src = img.get('src', '') or img.get('data-src', '') or img.get('data-original', '')
                    if img_src:
                        # 상대 URL 처리
                        if not img_src.startswith('http'):
                            if img_src.startswith('//'):
                                img_src = 'https:' + img_src
                            elif img_src.startswith('/'):
                                img_src = 'https://javdb.com' + img_src
                            else:
                                img_src = urljoin('https://javdb.com', img_src)
                        if img_src.startswith('http'):
                            image_urls.append(img_src)
                            print(f"[ImageFinder] JAVDB에서 이미지 발견: {code}")
            # 요청 성공이므로 실패 카운터 리셋
            if image_urls:
                self.javdb_fail_count = 0
                return image_urls
            # HTML 로딩은 됐지만 이미지 못 찾은 경우 셀레니움 시도
            if SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES:
                return self._search_javdb_selenium(code)
            return image_urls
            
        except (ConnectionError, Timeout) as e:
            # 연속 실패 카운트 및 자동 비활성화
            self.javdb_fail_count += 1
            if self.javdb_fail_count >= 3 and self.javdb_available:
                self.javdb_available = False
                print("[ImageFinder] JAVDB 연결 불가 감지: 이후 요청부터 JAVDB 검색을 비활성화합니다.")
            return []
        except RequestException:
            return []

    def _get_selenium_driver(self):
        """Selenium 드라이버 가져오기 (재사용)"""
        # 드라이버가 없거나 사용 횟수 초과 시 새로 생성
        if self.selenium_driver is None or self.selenium_use_count >= self.selenium_max_reuse:
            # 기존 드라이버 종료
            if self.selenium_driver is not None:
                try:
                    self.selenium_driver.quit()
                except:
                    pass
            
            # 새 드라이버 생성
            options = Options()
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument('--disable-quic')
            options.add_argument('--ignore-certificate-errors')
            options.add_argument('--ignore-ssl-errors')
            options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            if PROXY_URL:
                options.add_argument(f'--proxy-server={PROXY_URL}')
            service = Service(ChromeDriverManager().install())
            self.selenium_driver = webdriver.Chrome(service=service, options=options)
            self.selenium_driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            self.selenium_use_count = 0
        
        return self.selenium_driver
    
    def _search_missav_selenium(self, code: str) -> List[str]:
        """Selenium을 이용한 MissAV 검색 (1순위 소스)"""
        if not (SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES):
            return []
        try:
            driver = self._get_selenium_driver()
            self.selenium_use_count += 1
            
            # MissAV 검색 URL
            url = f"https://missav123.to/ko/search?keyword={quote(code)}"
            driver.get(url)
            
            # 페이지 로딩 대기
            try:
                WebDriverWait(driver, 3).until(
                    EC.presence_of_element_located((By.TAG_NAME, "img"))
                )
            except:
                time.sleep(0.5)
            
            image_urls: List[str] = []
            
            # 방법 1: code가 포함된 href를 가진 a 태그 내부의 img 찾기
            try:
                imgs = driver.find_elements(By.CSS_SELECTOR, f'a[href*="{code.lower()}"] img')
                for img in imgs[:3]:  # 처음 3개만
                    src = img.get_attribute('src') or img.get_attribute('data-src')
                    if src and 'cover' in src.lower() and src.startswith('http'):
                        image_urls.append(src)
                        print(f"[ImageFinder] MissAV에서 이미지 발견: {code}")
            except:
                pass
            
            # 방법 2: 실패 시 모든 img 태그에서 code 포함 src 찾기
            if not image_urls:
                try:
                    imgs = driver.find_elements(By.TAG_NAME, 'img')
                    for img in imgs:
                        src = img.get_attribute('src') or img.get_attribute('data-src')
                        if src and code.upper() in src.upper() and 'cover' in src.lower():
                            image_urls.append(src)
                            print(f"[ImageFinder] MissAV에서 이미지 발견: {code}")
                            break
                except:
                    pass
            
            return image_urls[:1]  # 첫 번째 결과만 반환
            
        except Exception as e:
            return []
    
    def _search_javmost(self, code: str) -> List[str]:
        """javmost.com에서 이미지 검색"""
        try:
            # javmost.com 검색 URL (CODE 검색)
            search_url = f"https://www5.javmost.com/search/{quote(code)}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9,ja;q=0.8',
                'Referer': 'https://www5.javmost.com/'
            }
            
            try:
                response = self._safe_get(search_url, headers=headers, timeout=self.http_timeout)
                if response and response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'lxml')
                else:
                    # HTTP 실패 시 Selenium으로 시도
                    if SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES:
                        return self._search_javmost_selenium(code)
                    return []
            except (ConnectionError, Timeout, RequestException) as e:
                # 네트워크 오류 시 Selenium으로 시도
                if SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES:
                    return self._search_javmost_selenium(code)
                return []
            
            image_urls = []
            
            # 작품 카드에서 이미지 찾기 (javmost.com 구조에 맞게)
            video_cards = soup.find_all('div', class_='item')
            if not video_cards:
                video_cards = soup.find_all('div', class_='movie-item')
            if not video_cards:
                video_cards = soup.find_all('a', class_='box')
            if not video_cards:
                video_cards = soup.find_all('div', class_='video-item')
            if not video_cards:
                # javmost.com 특정 구조 시도
                video_cards = soup.find_all('div', class_='thumbnail')
            
            if video_cards:
                # 첫 번째 결과의 이미지
                first_card = video_cards[0]
                img = first_card.find('img')
                if img:
                    img_src = img.get('src', '') or img.get('data-src', '') or img.get('data-original', '') or img.get('data-lazy-src', '')
                    if img_src:
                        # 상대 URL 처리
                        if not img_src.startswith('http'):
                            if img_src.startswith('//'):
                                img_src = 'https:' + img_src
                            elif img_src.startswith('/'):
                                img_src = 'https://www5.javmost.com' + img_src
                            else:
                                img_src = urljoin('https://www5.javmost.com/', img_src)
                        if img_src.startswith('http'):
                            image_urls.append(img_src)
            
            return image_urls
            
        except Exception as e:
            # 오류 발생 시 Selenium으로 시도
            if SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES:
                return self._search_javmost_selenium(code)
            return []
    
    def _search_javmost_selenium(self, code: str) -> List[str]:
        """Selenium을 이용한 javmost.com 검색 (통신 실패 시 대체)"""
        if not (SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES):
            return []
        try:
            driver = self._get_selenium_driver()
            self.selenium_use_count += 1
            
            url = f"https://www5.javmost.com/search/{quote(code)}"
            driver.get(url)
            
            # 최소 대기로 속도 개선
            try:
                WebDriverWait(driver, 3).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.item, div.movie-item, a.box, div.video-item, div.thumbnail"))
                )
            except:
                time.sleep(0.5)  # 실패 시에만 짧은 대기
            
            page_source = driver.page_source
            soup = BeautifulSoup(page_source, 'lxml')
            image_urls: List[str] = []
            video_cards = soup.find_all('div', class_='item') or soup.find_all('div', class_='movie-item') or soup.find_all('a', class_='box') or soup.find_all('div', class_='video-item') or soup.find_all('div', class_='thumbnail')
            if video_cards:
                first = video_cards[0]
                img = first.find('img')
                if img:
                    img_src = img.get('src') or img.get('data-src') or img.get('data-original') or img.get('data-lazy-src')
                    if img_src and not img_src.startswith('http'):
                        if img_src.startswith('//'):
                            img_src = 'https:' + img_src
                        elif img_src.startswith('/'):
                            img_src = 'https://www5.javmost.com' + img_src
                        else:
                            img_src = urljoin('https://www5.javmost.com/', img_src)
                    if img_src and img_src.startswith('http'):
                        image_urls.append(img_src)
            return image_urls
        except Exception:
            # 에러 발생 시 드라이버 재생성을 위해 None 설정
            if self.selenium_driver:
                try:
                    self.selenium_driver.quit()
                except:
                    pass
                self.selenium_driver = None
            return []
    
    def _search_javlibrary_selenium(self, code: str) -> List[str]:
        """Selenium을 이용한 JAVLibrary 검색 (HTTP 403 우회)"""
        if not (SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES):
            return []
        try:
            driver = self._get_selenium_driver()
            self.selenium_use_count += 1
            
            image_urls = []
            
            # 방법 1: 직접 작품 페이지 URL 시도 (영어/일본어/중국어 버전)
            for lang in ['en', 'ja', 'cn']:
                try:
                    direct_url = f"https://www.javlibrary.com/{lang}/?v={quote(code)}"
                    driver.get(direct_url)
                    
                    # 페이지 로딩 대기 (더 긴 대기 시간)
                    try:
                        WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.TAG_NAME, "img"))
                        )
                    except:
                        time.sleep(1.0)  # 대기 시간 증가
                    
                    # 연령 확인/이용 약관 동의 팝업 처리
                    try:
                        # "동의한다" 버튼 찾기 (여러 방법 시도)
                        agree_button = None
                        # 방법 1: 텍스트로 찾기
                        try:
                            agree_button = driver.find_element(By.XPATH, "//button[contains(text(), '同意する')]")
                        except:
                            try:
                                agree_button = driver.find_element(By.XPATH, "//a[contains(text(), '同意する')]")
                            except:
                                try:
                                    agree_button = driver.find_element(By.XPATH, "//input[@value='同意する']")
                                except:
                                    pass
                        
                        # 방법 2: 클래스나 ID로 찾기
                        if not agree_button:
                            try:
                                agree_button = driver.find_element(By.CSS_SELECTOR, "button.agree, a.agree, input.agree")
                            except:
                                try:
                                    agree_button = driver.find_element(By.ID, "agree")
                                except:
                                    pass
                        
                        # 방법 3: 모달 내부의 버튼 찾기
                        if not agree_button:
                            try:
                                # 모달이나 팝업 내부의 버튼 찾기
                                modals = driver.find_elements(By.CSS_SELECTOR, "div[class*='modal'], div[class*='popup'], div[class*='dialog']")
                                for modal in modals:
                                    try:
                                        buttons = modal.find_elements(By.TAG_NAME, "button")
                                        for btn in buttons:
                                            if '同意' in btn.text or 'agree' in btn.text.lower():
                                                agree_button = btn
                                                break
                                        if agree_button:
                                            break
                                    except:
                                        continue
                            except:
                                pass
                        
                        if agree_button:
                            agree_button.click()
                            time.sleep(1.0)  # 팝업 닫힌 후 페이지 로딩 대기
                            print(f"[ImageFinder] JAVLibrary 연령 확인 팝업 처리 완료: {code}")
                    except Exception as e:
                        # 팝업이 없거나 이미 처리된 경우는 무시
                        pass
                    
                    # URL이 리다이렉트되었는지 확인
                    current_url = driver.current_url
                    if 'vl_searchbyid' in current_url or 'search' in current_url:
                        # 검색 결과 페이지로 리다이렉트된 경우
                        # Selenium으로 직접 링크 찾아서 클릭 시도
                        try:
                            # 방법 1: class='video'인 a 태그 찾기
                            video_elements = driver.find_elements(By.CSS_SELECTOR, 'a.video')
                            if not video_elements:
                                # 방법 2: div.video 내부의 a 태그
                                video_elements = driver.find_elements(By.CSS_SELECTOR, 'div.video a')
                            if not video_elements:
                                # 방법 3: href에 code가 포함된 링크
                                video_elements = driver.find_elements(By.XPATH, f"//a[contains(@href, '{code.upper()}') or contains(@href, '{code.lower()}')]")
                            
                            if video_elements:
                                # 첫 번째 링크 클릭
                                video_elements[0].click()
                                time.sleep(1.5)  # 클릭 후 페이지 로딩 대기
                                current_url = driver.current_url
                                print(f"[ImageFinder] JAVLibrary 검색 결과에서 링크 클릭 성공: {code}")
                            else:
                                # Selenium으로 못 찾았으면 BeautifulSoup으로 시도
                                page_source = driver.page_source
                                soup = BeautifulSoup(page_source, 'lxml')
                                
                                video_link = soup.find('a', class_='video')
                                if not video_link:
                                    video_div = soup.find('div', class_='video')
                                    if video_div:
                                        video_link = video_div.find('a')
                                if not video_link:
                                    for a in soup.find_all('a', href=True):
                                        href = a.get('href', '')
                                        if code.upper() in href.upper() or code.lower() in href.lower():
                                            video_link = a
                                            break
                                
                                if video_link:
                                    href = video_link.get('href', '')
                                    if href:
                                        # 상대 URL 처리
                                        if href.startswith('/'):
                                            video_url = f"https://www.javlibrary.com{href}"
                                        elif href.startswith('http'):
                                            video_url = href
                                        else:
                                            video_url = urljoin(f'https://www.javlibrary.com/{lang}/', href)
                                        
                                        # 상세 페이지로 이동
                                        driver.get(video_url)
                                        time.sleep(1.0)
                                        current_url = driver.current_url
                        except Exception as e:
                            # 클릭 실패 시 기존 방식으로 URL 이동
                            page_source = driver.page_source
                            soup = BeautifulSoup(page_source, 'lxml')
                            
                            video_link = soup.find('a', class_='video')
                            if not video_link:
                                video_div = soup.find('div', class_='video')
                                if video_div:
                                    video_link = video_div.find('a')
                            if not video_link:
                                for a in soup.find_all('a', href=True):
                                    href = a.get('href', '')
                                    if code.upper() in href.upper() or code.lower() in href.lower():
                                        video_link = a
                                        break
                            
                            if video_link:
                                href = video_link.get('href', '')
                                if href:
                                    if href.startswith('/'):
                                        video_url = f"https://www.javlibrary.com{href}"
                                    elif href.startswith('http'):
                                        video_url = href
                                    else:
                                        video_url = urljoin(f'https://www.javlibrary.com/{lang}/', href)
                                    
                                    driver.get(video_url)
                                    time.sleep(1.0)
                                    current_url = driver.current_url
                    
                    page_source = driver.page_source
                    soup = BeautifulSoup(page_source, 'lxml')
                    
                    # 썸네일 이미지 찾기 (더 많은 방법 시도)
                    cover_img = None
                    # 방법 1: id='video_jacket_img'
                    cover_img = soup.find('img', id='video_jacket_img')
                    # 방법 2: id='video_jacket'
                    if not cover_img:
                        cover_img = soup.find('img', {'id': 'video_jacket'})
                    # 방법 3: class='cover'
                    if not cover_img:
                        cover_img = soup.find('img', class_='cover')
                    # 방법 4: img 태그 중 src에 'cover' 또는 'jacket'가 포함된 것
                    if not cover_img:
                        for img in soup.find_all('img'):
                            img_src = img.get('src', '') or img.get('data-src', '') or img.get('data-lazy-src', '')
                            if img_src and ('cover' in img_src.lower() or 'jacket' in img_src.lower() or 'poster' in img_src.lower()):
                                cover_img = img
                                break
                    # 방법 5: 가장 큰 이미지 (일반적으로 커버 이미지)
                    if not cover_img:
                        imgs = soup.find_all('img')
                        if imgs:
                            # src에 'javlibrary'가 포함된 이미지 우선
                            for img in imgs:
                                img_src = img.get('src', '') or img.get('data-src', '')
                                if img_src and 'javlibrary' in img_src.lower():
                                    cover_img = img
                                    break
                            # 없으면 첫 번째 이미지
                            if not cover_img and imgs:
                                cover_img = imgs[0]
                    
                    if cover_img:
                        img_src = cover_img.get('src', '') or cover_img.get('data-src', '') or cover_img.get('data-lazy-src', '') or cover_img.get('data-original', '')
                        if img_src:
                            # 상대 URL 처리
                            if not img_src.startswith('http'):
                                if img_src.startswith('//'):
                                    img_src = 'https:' + img_src
                                elif img_src.startswith('/'):
                                    img_src = f'https://www.javlibrary.com{img_src}'
                                else:
                                    img_src = urljoin(f'https://www.javlibrary.com/{lang}/', img_src)
                            # 실제 이미지 URL인지 확인 (이미지 확장자 또는 이미지 경로 포함)
                            is_image_url = (
                                img_src.startswith('http') and 
                                'javlibrary' in img_src.lower() and
                                (
                                    any(img_src.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']) or
                                    '/image' in img_src.lower() or 
                                    '/img' in img_src.lower() or 
                                    '/cover' in img_src.lower() or 
                                    '/jacket' in img_src.lower() or
                                    '/pl' in img_src.lower() or  # DMM 이미지 패턴
                                    '/ps' in img_src.lower()    # DMM 이미지 패턴
                                )
                            )
                            
                            if is_image_url:
                                image_urls.append(img_src)
                                print(f"[ImageFinder] JAVLibrary에서 이미지 발견 (Selenium, 직접 URL, {lang}): {code} -> {img_src[:80]}...")
                                return image_urls  # 찾았으면 바로 반환
                            # 이미지가 아닌 URL은 스킵 (로그 생략)
                except Exception as e:
                    # 직접 URL 실패는 조용히 넘어감 (다음 방법 시도)
                    pass
            
            # 방법 2: 검색 페이지를 통한 검색 (여러 언어 버전)
            for lang in ['ja', 'en', 'cn']:
                try:
                    search_url = f"https://www.javlibrary.com/{lang}/vl_searchbyid.php?keyword={quote(code)}"
                    driver.get(search_url)
                    
                    # 페이지 로딩 대기
                    try:
                        WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.TAG_NAME, "a"))
                        )
                    except:
                        time.sleep(1.0)
                    
                    # 연령 확인/이용 약관 동의 팝업 처리
                    try:
                        agree_button = None
                        try:
                            agree_button = driver.find_element(By.XPATH, "//button[contains(text(), '同意する')]")
                        except:
                            try:
                                agree_button = driver.find_element(By.XPATH, "//a[contains(text(), '同意する')]")
                            except:
                                try:
                                    agree_button = driver.find_element(By.XPATH, "//input[@value='同意する']")
                                except:
                                    pass
                        
                        if not agree_button:
                            try:
                                agree_button = driver.find_element(By.CSS_SELECTOR, "button.agree, a.agree, input.agree")
                            except:
                                try:
                                    agree_button = driver.find_element(By.ID, "agree")
                                except:
                                    pass
                        
                        if not agree_button:
                            try:
                                modals = driver.find_elements(By.CSS_SELECTOR, "div[class*='modal'], div[class*='popup'], div[class*='dialog']")
                                for modal in modals:
                                    try:
                                        buttons = modal.find_elements(By.TAG_NAME, "button")
                                        for btn in buttons:
                                            if '同意' in btn.text or 'agree' in btn.text.lower():
                                                agree_button = btn
                                                break
                                        if agree_button:
                                            break
                                    except:
                                        continue
                            except:
                                pass
                        
                        if agree_button:
                            agree_button.click()
                            time.sleep(1.0)
                            print(f"[ImageFinder] JAVLibrary 연령 확인 팝업 처리 완료 (검색 페이지): {code}")
                    except Exception as e:
                        pass
                    
                    page_source = driver.page_source
                    soup = BeautifulSoup(page_source, 'lxml')
                    
                    # 작품 페이지 링크 찾기 (더 많은 방법 시도)
                    video_link = None
                    # 방법 1: class='video'인 a 태그
                    video_link = soup.find('a', class_='video')
                    # 방법 2: div.video 내부의 a 태그
                    if not video_link:
                        video_div = soup.find('div', class_='video')
                        if video_div:
                            video_link = video_div.find('a')
                    # 방법 3: href에 code가 포함된 링크
                    if not video_link:
                        for a in soup.find_all('a', href=True):
                            href = a.get('href', '')
                            if code.upper() in href.upper() or code.lower() in href.lower():
                                video_link = a
                                break
                    # 방법 4: title에 code가 포함된 링크
                    if not video_link:
                        for a in soup.find_all('a', title=True):
                            title = a.get('title', '')
                            if code.upper() in title.upper() or code.lower() in title.lower():
                                video_link = a
                                break
                    
                    if video_link:
                        href = video_link.get('href', '')
                        if href:
                            # 상대 URL 처리
                            if href.startswith('/'):
                                video_url = f"https://www.javlibrary.com{href}"
                            elif href.startswith('http'):
                                video_url = href
                            else:
                                video_url = urljoin(f'https://www.javlibrary.com/{lang}/', href)
                            
                            # 상세 페이지로 이동
                            driver.get(video_url)
                            time.sleep(1.0)
                            
                            detail_source = driver.page_source
                            detail_soup = BeautifulSoup(detail_source, 'lxml')
                            
                            # DMM 이미지 URL도 찾기 (페이지 내 링크나 이미지에서)
                            dmm_url = None
                            page_text = str(detail_soup)
                            # DMM 이미지 URL 패턴 찾기: pics.dmm.co.jp/mono/movie/adult/{code}/
                            code_clean = code.replace('-', '').replace('_', '').lower()
                            dmm_patterns = [
                                f'pics.dmm.co.jp/mono/movie/adult/{code_clean}/{code_clean}pl.jpg',
                                f'pics.dmm.co.jp/mono/movie/adult/{code_clean}/{code_clean}ps.jpg',
                                f'pics.dmm.co.jp/digital/video/{code_clean}/{code_clean}pl.jpg',
                            ]
                            for pattern in dmm_patterns:
                                if pattern in page_text:
                                    # URL 추출
                                    import re
                                    dmm_match = re.search(r'https?://[^"\s<>]+' + re.escape(pattern.replace(code_clean, r'[^"\s<>]+')), page_text)
                                    if dmm_match:
                                        dmm_url = dmm_match.group(0)
                                        break
                                    else:
                                        # 패턴으로 직접 URL 생성
                                        dmm_url = f'https://{pattern}'
                                        break
                            
                            # 썸네일 이미지 찾기 (더 많은 방법 시도)
                            cover_img = None
                            cover_img = detail_soup.find('img', id='video_jacket_img')
                            if not cover_img:
                                cover_img = detail_soup.find('img', {'id': 'video_jacket'})
                            if not cover_img:
                                cover_img = detail_soup.find('img', class_='cover')
                            if not cover_img:
                                for img in detail_soup.find_all('img'):
                                    img_src = img.get('src', '') or img.get('data-src', '')
                                    if img_src and ('cover' in img_src.lower() or 'jacket' in img_src.lower() or 'poster' in img_src.lower()):
                                        cover_img = img
                                        break
                            if not cover_img:
                                imgs = detail_soup.find_all('img')
                                if imgs:
                                    for img in imgs:
                                        img_src = img.get('src', '') or img.get('data-src', '')
                                        if img_src and 'javlibrary' in img_src.lower():
                                            cover_img = img
                                            break
                            
                            if cover_img:
                                img_src = cover_img.get('src', '') or cover_img.get('data-src', '') or cover_img.get('data-lazy-src', '') or cover_img.get('data-original', '')
                                if img_src:
                                    # 상대 URL 처리
                                    if not img_src.startswith('http'):
                                        if img_src.startswith('//'):
                                            img_src = 'https:' + img_src
                                        elif img_src.startswith('/'):
                                            img_src = 'https://www.javlibrary.com' + img_src
                                        else:
                                            img_src = urljoin(f'https://www.javlibrary.com/{lang}/', img_src)
                                    if img_src.startswith('http'):
                                        image_urls.append(img_src)
                            
                            # DMM URL이 있으면 추가 (우선순위 높음)
                            if dmm_url:
                                image_urls.insert(0, dmm_url)  # 맨 앞에 추가
                                print(f"[ImageFinder] JAVLibrary에서 DMM 이미지 발견 (Selenium): {code} -> {dmm_url}")
                            
                            if image_urls:
                                print(f"[ImageFinder] JAVLibrary에서 이미지 발견 (Selenium, 검색, {lang}): {code}")
                                return image_urls  # 찾았으면 바로 반환
                except Exception as e:
                    pass
            
            return image_urls
        except Exception as e:
            print(f"[ImageFinder] JAVLibrary Selenium 검색 오류 ({code}): {e}")
            import traceback
            print(f"[ImageFinder] JAVLibrary Selenium 검색 오류 상세: {traceback.format_exc()}")
            return []
    
    def _search_javdb_selenium(self, code: str) -> List[str]:
        """Selenium을 이용한 JAVDB 검색 (Cloudflare/차단 우회, 드라이버 재사용)"""
        if not (SELENIUM_AVAILABLE and ENABLE_SELENIUM_FOR_IMAGES):
            return []
        try:
            driver = self._get_selenium_driver()
            self.selenium_use_count += 1
            
            url = f"https://javdb.com/search?q={quote(code)}"
            driver.get(url)
            
            # 최소 대기로 속도 개선
            try:
                WebDriverWait(driver, 2).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.item, div.movie-item, a.box"))
                )
            except:
                time.sleep(0.3)  # 실패 시에만 짧은 대기
            
            page_source = driver.page_source
            soup = BeautifulSoup(page_source, 'lxml')
            image_urls: List[str] = []
            video_cards = soup.find_all('div', class_='item') or soup.find_all('div', class_='movie-item') or soup.find_all('a', class_='box')
            if video_cards:
                first = video_cards[0]
                img = first.find('img')
                if img:
                    img_src = img.get('src') or img.get('data-src') or img.get('data-original')
                    if img_src and not img_src.startswith('http'):
                        if img_src.startswith('//'):
                            img_src = 'https:' + img_src
                        elif img_src.startswith('/'):
                            img_src = 'https://javdb.com' + img_src
                        else:
                            img_src = urljoin('https://javdb.com', img_src)
                    if img_src and img_src.startswith('http'):
                        image_urls.append(img_src)
            if image_urls:
                self.javdb_fail_count = 0
            return image_urls
        except Exception:
            # 에러 발생 시 드라이버 재생성을 위해 None 설정
            if self.selenium_driver:
                try:
                    self.selenium_driver.quit()
                except:
                    pass
                self.selenium_driver = None
            return []
    
    def _search_javbee(self, query: str, title: str = None) -> List[str]:
        """javbee.vip에서 이미지 검색 (test.py 로직 기반)
        
        Args:
            query: 검색 쿼리 (작품번호 등)
            title: 원본 제목 (로그 출력용)
        """
        try:
            # 검색어 정제 (FC2-PPV-숫자 또는 JAV 코드)
            search_query = query.strip()
            display_title = title[:50] + "..." if title and len(title) > 50 else (title or search_query)
            
            # javbee.vip 검색 URL
            search_url = f"https://javbee.vip/search?keyword={quote(search_query)}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://javbee.vip/'
            }
            
            response = self._safe_get(search_url, headers=headers, timeout=self.http_timeout)
            if not response or response.status_code != 200:
                print(f"[ImageFinder] JAVBee 검색 페이지 접근 실패 (제목: {display_title}): {response.status_code if response else 'No response'}")
                return []
            
            soup = BeautifulSoup(response.content, 'lxml')
            image_urls = []
            
            # test.py 로직 기반 함수들
            def compile_keyword_strict(keyword: str):
                """
                '문자+숫자'가 정확히 같고, 문자/숫자 사이의 '-' 만 옵션.
                예) 'STARS-080' -> (?<!alnum)STARS-?080(?!alnum)
                """
                m = re.match(r"^\s*([A-Za-z]+)\s*-?\s*(\d+)\s*$", keyword.strip())
                if not m:
                    k = keyword.strip()
                    k = re.escape(k).replace(r"\-", "-?")
                    return re.compile(rf"(?<![A-Za-z0-9]){k}(?![A-Za-z0-9])", re.I)
                prefix, num = m.groups()
                return re.compile(
                    rf"(?<![A-Za-z0-9]){re.escape(prefix)}-?{re.escape(num)}(?![A-Za-z0-9])",
                    re.I,
                )
            
            # 카드 타이틀(제목) 찾기
            ignore_title_words_re = re.compile(r"(show\s*screenshot|torrent|magnet)", re.I)
            
            def find_card_title_text(anchor, max_up=6):
                """
                'Show Screenshot' 앵커를 기준으로, 같은 카드의 제목 텍스트를 찾아 반환.
                - 우선: 앵커의 이전 형제들 중 텍스트가 긴 <a>/<h1..h6>.
                - 대안: 부모로 올라가며 그 안에서 후보 찾기(최대 max_up 단계).
                """
                # 1) 이전 형제 스캔
                for sib in anchor.previous_siblings:
                    name = getattr(sib, "name", None)
                    if name in ("a", "h1", "h2", "h3", "h4", "h5", "h6"):
                        txt = sib.get_text(" ", strip=True) or ""
                        if txt and not ignore_title_words_re.search(txt) and len(txt) >= 6:
                            return txt
                    if getattr(sib, "find_all", None):
                        for cand in sib.find_all(["a", "h1", "h2", "h3", "h4", "h5", "h6"]):
                            txt = cand.get_text(" ", strip=True) or ""
                            if txt and not ignore_title_words_re.search(txt) and len(txt) >= 6:
                                return txt
                # 2) 부모로 상승하며 내부에서 찾기
                node = anchor
                for _ in range(max_up):
                    node = getattr(node, "parent", None)
                    if node is None:
                        break
                    for cand in node.find_all(["a", "h1", "h2", "h3", "h4", "h5", "h6"]):
                        txt = cand.get_text(" ", strip=True) or ""
                        if txt and not ignore_title_words_re.search(txt) and len(txt) >= 6:
                            return txt
                return None
            
            # uploads/upload 경로만 허용
            uploads_re = re.compile(r"/(?:wp-content/)?uploads?/", re.I)
            
            # 자산(logo/favicon/icon/banner/ads 등) 세그먼트 기준 필터
            asset_seg_re = re.compile(
                r"(?:^|/)(?:logo|favicon|sprite|icons?|ads?|banners?|static|assets|themes|emoji|svg)(?:/|$)",
                re.I
            )
            
            def is_probably_asset(url: str) -> bool:
                """경로 세그먼트 기준으로 자산인지 확인"""
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    path = parsed.path
                    return bool(asset_seg_re.search(path))
                except Exception:
                    return False
            
            def img_candidate_urls(img_tag, base_url: str):
                """img 태그에서 모든 가능한 이미지 URL 추출 (src, data-src, data-original, srcset)"""
                cands = []
                def add(u, how):
                    if u:
                        full_url = urljoin(base_url, u)
                        cands.append({"url": full_url, "how": how})
                
                add(img_tag.get("src"), "src")
                add(img_tag.get("data-src"), "data-src")
                add(img_tag.get("data-original"), "data-original")
                
                # srcset → 해상도 큰 후보(뒤쪽)를 우선으로
                srcset = img_tag.get("srcset")
                if srcset:
                    parts = [p.strip() for p in srcset.split(",") if p.strip()]
                    # 큰 것부터 시도하도록 역순
                    for p in reversed(parts):
                        url_part = p.split()[0] if p.split() else p
                        add(url_part, "srcset")
                
                # 중복 제거
                seen, uniq = set(), []
                for item in cands:
                    if item["url"] not in seen:
                        uniq.append(item)
                        seen.add(item["url"])
                return uniq
            
            def closest_container_with_images(anchor):
                """앵커에서 가장 가까운 컨테이너와 이미지 찾기"""
                node = anchor
                for _ in range(6):
                    if node is None:
                        break
                    if hasattr(node, "find_all"):
                        imgs = node.find_all("img")
                        if imgs:
                            return node, imgs
                    node = getattr(node, "parent", None)
                return None, []
            
            def images_in_previous_siblings(anchor):
                """앵커의 이전 형제에서 이미지 찾기"""
                imgs = []
                for sib in anchor.previous_siblings:
                    if hasattr(sib, "find_all"):
                        imgs.extend(sib.find_all("img"))
                    elif hasattr(sib, "name") and sib.name == "img":
                        imgs.append(sib)
                return imgs
            
            def find_show_screenshot_anchors(soup: BeautifulSoup):
                """'Show Screenshot' 텍스트가 있는 앵커/버튼 찾기"""
                anchors = []
                for t in soup.find_all(["a", "button"]):
                    txt = t.get_text(" ", strip=True) or ""
                    if re.search(r"\bshow\s*screens?hot\b", txt, re.I):
                        anchors.append(t)
                # 중복 제거
                seen, uniq = set(), []
                for a in anchors:
                    k = str(a)
                    if k not in seen:
                        uniq.append(a)
                        seen.add(k)
                return uniq
            
            # test.py 로직: "Show Screenshot" 앵커를 기준으로 같은 카드의 이미지 찾기
            anchors = find_show_screenshot_anchors(soup)
            
            if not anchors:
                print(f"[ImageFinder] JAVBee 'Show Screenshot' 앵커를 찾을 수 없음 - 이미지 없음으로 처리 (제목: {display_title})")
                return []  # 앵커가 없으면 즉시 반환하여 다른 서버에서 검색하도록 함
            
            # 첫 번째 카드만 사용 (test.py 로직)
            anchor = anchors[0]
            print(f"[ImageFinder] JAVBee 'Show Screenshot' 앵커 {len(anchors)}개 발견, 첫 번째 카드 사용 (제목: {display_title})")
            
            # 카드 제목 추출 & 키워드 엄격 매칭
            title_text = find_card_title_text(anchor)
            kw_re = compile_keyword_strict(search_query)
            title_ok = bool(title_text and kw_re.search(title_text))
            
            if not title_ok:
                print(f"[ImageFinder] JAVBee 카드 제목 불일치 - 이미지 없음으로 처리 (제목: {display_title}, 카드제목: {title_text})")
                return []  # 제목이 키워드와 일치하지 않으면 즉시 반환
            
            print(f"[ImageFinder] JAVBee 카드 제목 매칭 성공 (제목: {display_title}, 카드제목: {title_text})")
            
            # 카드의 좌측 이미지 영역에서 후보 수집
            all_img_tags = []
            # 이전 형제에서 이미지 찾기
            all_img_tags.extend(images_in_previous_siblings(anchor))
            # 가장 가까운 컨테이너에서 이미지 찾기
            _, imgs = closest_container_with_images(anchor)
            all_img_tags.extend(imgs)
            
            # 후보 URL 만들기
            cand_urls = []
            for img in all_img_tags:
                cand_urls.extend(img_candidate_urls(img, search_url))
            
            # 중복 제거
            uniq_urls, seen = [], set()
            for c in cand_urls:
                if c["url"] not in seen:
                    uniq_urls.append(c)
                    seen.add(c["url"])
            
            # 필터링: uploads/upload 경로 선호, 자산 제외 (파일명에 키워드가 없어도 OK - 제목으로 이미 검증됨)
            filtered = []
            for c in uniq_urls:
                u = c["url"]
                
                # javbee.vip/storage/ 경로 차단
                if 'javbee.vip/storage/' in u.lower():
                    continue
                
                # javbee.vip/images/loading 차단
                if 'javbee.vip/images/loading' in u.lower():
                    continue
                
                # 자산 필터링 (경로 세그먼트 기준)
                if is_probably_asset(u):
                    continue
                
                # uploads/upload 경로 선호 (필수는 아님)
                from urllib.parse import urlparse
                path = urlparse(u).path
                # uploads가 아니더라도 이미지 확장자가 있으면 허용 (제목으로 이미 검증됨)
                path_ext = pathlib.Path(path).suffix.lower()
                if not uploads_re.search(path) and path_ext not in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
                    continue
                
                filtered.append(c)
            
            # 필터링된 URL을 image_urls에 추가
            for c in filtered:
                u = c["url"]
                if u not in image_urls:
                    # javbee.image-sky.com/wp-content/uploads/ 경로는 최우선
                    if 'javbee.image-sky.com' in u.lower() and 'wp-content/uploads/' in u.lower():
                        image_urls.insert(0, u)
                        print(f"[ImageFinder] JAVBee에서 이미지 발견 (Show Screenshot, javbee.image-sky.com) - 제목: {display_title}")
                        print(f"  URL: {u}")
                    else:
                        image_urls.append(u)
                        print(f"[ImageFinder] JAVBee에서 이미지 발견 (Show Screenshot) - 제목: {display_title}")
                        print(f"  URL: {u}")
                    if len(image_urls) >= 3:
                        break
            
            # test.py 로직: 상세 페이지 방문하지 않음, 검색 결과 페이지에서만 찾음
            
            if image_urls:
                print(f"[ImageFinder] JAVBee 검색 성공: {len(image_urls)}개 이미지 발견 (제목: {display_title})")
            else:
                print(f"[ImageFinder] JAVBee 검색 결과 없음: {search_query} (제목: {display_title})")
            
            return image_urls[:3]
            
        except Exception as e:
            display_title = title[:50] + "..." if title and len(title) > 50 else (title or query.strip())
            print(f"[ImageFinder] JAVBee 검색 실패 (제목: {display_title}): {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def __del__(self):
        """소멸자: Selenium 드라이버 정리"""
        if self.selenium_driver:
            try:
                self.selenium_driver.quit()
            except:
                pass
    
    # Google 이미지 검색은 성인 컨텐츠 필터링으로 인해 비활성화
    # def _search_google_images(self, query: str, max_results: int = 5) -> List[str]:
    #     """Google 이미지 검색 - 성인 컨텐츠 필터링으로 사용 불가"""
    #     return []
    
    def _search_bing_images(self, query: str, max_results: int = 5) -> List[str]:
        """Bing 이미지 검색 (빠른 HTTP 요청) - 성인 컨텐츠 검색 가능"""
        try:
            # 검색어 정제
            clean_query = self._clean_query(query)
            
            # 작품 코드만 검색 (더 정확한 결과)
            codes = self._extract_codes(query)
            if codes:
                clean_query = codes[0]  # 첫 번째 코드만 사용
            
            # Bing 이미지 검색 URL (SafeSearch 끄기)
            url = "https://www.bing.com/images/search"
            params = {
                'q': clean_query + ' JAV',  # JAV 키워드 추가로 정확도 향상
                'first': 1,
                'count': max_results * 3,  # 더 많이 가져와서 필터링
                'safeSearch': 'off',  # SafeSearch 끄기
                'qft': '+filterui:imagesize-large'  # 큰 이미지만
            }
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
            }
            
            response = self.session.get(url, params=params, headers=headers, timeout=10)
            if response.status_code != 200:
                return []
            
            soup = BeautifulSoup(response.content, 'lxml')
            
            # 이미지 URL 추출
            image_urls = []
            
            # 방법 1: m= 파라미터에서 추출 (Bing의 주요 방법)
            for a in soup.find_all('a', class_='iusc'):
                m = a.get('m')
                if m:
                    try:
                        data = json.loads(m)
                        if 'murl' in data:
                            img_url = data['murl']
                            if img_url.startswith('http') and img_url not in image_urls:
                                image_urls.append(img_url)
                                if len(image_urls) >= max_results:
                                    break
                    except:
                        continue
            
            # 방법 2: 이미지 태그에서 직접 추출
            if len(image_urls) < max_results:
                for img in soup.find_all('img'):
                    src = img.get('src') or img.get('data-src') or img.get('data-original')
                    if src and src.startswith('http') and src not in image_urls:
                        # 썸네일이 아닌 실제 이미지 URL만
                        if 'bing.com/th' not in src and 'simg' not in src:
                            image_urls.append(src)
                            if len(image_urls) >= max_results:
                                break
            
            # 방법 3: data-src 속성에서 추출
            if len(image_urls) < max_results:
                for elem in soup.find_all(attrs={'data-src': True}):
                    img_url = elem.get('data-src', '')
                    if img_url and img_url.startswith('http') and img_url not in image_urls:
                        image_urls.append(img_url)
                        if len(image_urls) >= max_results:
                            break
            
            return image_urls[:max_results]
            
        except Exception as e:
            print(f"[ImageFinder] Bing 검색 실패: {e}")
            return []
    
    # DuckDuckGo 이미지 검색도 성인 컨텐츠 필터링으로 인해 비활성화
    # def _search_duckduckgo_images(self, query: str, max_results: int = 5) -> List[str]:
    #     """DuckDuckGo 이미지 검색 - 성인 컨텐츠 필터링으로 사용 불가"""
    #     return []
    
    def _clean_query(self, title: str) -> str:
        """검색어 정제
        
        파일 크기, 특수문자 등 제거
        """
        # 대괄호 안의 내용 제거
        query = re.sub(r'\[.*?\]', '', title)
        
        # 파일 크기 정보 제거
        query = re.sub(r'\d+\.?\d*\s*(GB|MB|GiB|MiB)', '', query, flags=re.IGNORECASE)
        
        # 특수문자 제거
        query = re.sub(r'[^\w\s\-]', ' ', query)
        
        # 다중 공백 제거
        query = re.sub(r'\s+', ' ', query).strip()
        
        # 검색어 길이 제한 (처음 60자)
        if len(query) > 60:
            query = query[:60]
        
        return query


class ThumbnailEnhancer:
    """썸네일 자동 검색 및 추가"""
    
    def __init__(self):
        self.finder = ImageFinder()
    
    def enhance_torrent(self, torrent_data: dict, force: bool = False) -> dict:
        """토렌트 데이터에 썸네일 추가
        
        Args:
            torrent_data: 토렌트 정보
            force: 이미 있어도 다시 검색
            
        Returns:
            업데이트된 토렌트 데이터
        """
        # 이미 썸네일이 있고 force가 아니면 스킵
        if torrent_data.get('thumbnail_url') and not force:
            return torrent_data
        
        title = torrent_data.get('title', '')
        if not title:
            return torrent_data
        
        print(f"[ThumbnailEnhancer] 이미지 검색 중: {title[:40]}...")
        
        # 이미지 검색
        images = self.finder.search_images(title, max_images=5)
        
        # 결과 적용
        if images['thumbnail']:
            torrent_data['thumbnail_url'] = images['thumbnail']
            print(f"[ThumbnailEnhancer] OK 썸네일 발견! {images['thumbnail']}")

        # 스냅샷 비활성화

        # 딜레이 (과도한 요청 방지, 차단 방지)
        time.sleep(random.uniform(0.1, 0.5))

        return torrent_data

