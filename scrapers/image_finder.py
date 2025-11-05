"""썸네일 및 스냅샷 이미지 검색"""
import requests
from bs4 import BeautifulSoup
import re
from typing import List, Optional
import time
import random
import json
import os
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
        
        # JAVDB 실패 시 FC2PPV.stream 시도 (FC2 코드가 있을 때만)
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
        try:
            # JAVLibrary 검색 URL (일본어 버전)
            search_url = f"https://www.javlibrary.com/ja/vl_searchbyid.php?keyword={quote(code)}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
                'Referer': 'https://www.javlibrary.com/ja/'
            }
            
            response = self._safe_get(search_url, headers=headers, timeout=self.http_timeout)
            if response.status_code != 200:
                return []
            
            soup = BeautifulSoup(response.content, 'lxml')
            image_urls = []
            
            # 작품 페이지 링크 찾기 (여러 방법 시도)
            video_link = soup.find('a', class_='video')
            if not video_link:
                # 다른 방법: div.video로 찾기
                video_div = soup.find('div', class_='video')
                if video_div:
                    video_link = video_div.find('a')
            
            if video_link:
                href = video_link.get('href', '')
                if href:
                    # 상대 URL 처리
                    if href.startswith('/'):
                        video_url = f"https://www.javlibrary.com{href}"
                    elif href.startswith('http'):
                        video_url = href
                    else:
                        video_url = urljoin('https://www.javlibrary.com/ja/', href)
                    
                    # 상세 페이지에서 이미지 가져오기
                    detail_response = self._safe_get(video_url, headers=headers, timeout=self.http_timeout)
                    if detail_response.status_code == 200:
                        detail_soup = BeautifulSoup(detail_response.content, 'lxml')
                        
                        # 썸네일 이미지 (여러 방법 시도)
                        cover_img = detail_soup.find('img', id='video_jacket_img')
                        if not cover_img:
                            cover_img = detail_soup.find('img', {'id': 'video_jacket'})
                        if not cover_img:
                            # poster 이미지 찾기
                            cover_img = detail_soup.find('img', class_='cover')
                        
                        if cover_img:
                            img_src = cover_img.get('src', '') or cover_img.get('data-src', '')
                            if img_src:
                                # 상대 URL 처리
                                if not img_src.startswith('http'):
                                    if img_src.startswith('//'):
                                        img_src = 'https:' + img_src
                                    elif img_src.startswith('/'):
                                        img_src = 'https://www.javlibrary.com' + img_src
                                    else:
                                        img_src = urljoin('https://www.javlibrary.com/ja/', img_src)
                                image_urls.append(img_src)
                                print(f"[ImageFinder] JAVLibrary에서 이미지 발견: {code}")
            
            return image_urls
            
        except Exception as e:
            print(f"[ImageFinder] JAVLibrary 검색 오류 ({code}): {e}")
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

