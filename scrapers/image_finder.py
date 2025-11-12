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
from urllib.parse import quote, urljoin, urlencode
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
        # 프로그램 시작 시 항상 활성화 상태로 리셋 (이전 실행의 차단 상태 무시)
        # enable_javdb가 True이면 항상 활성화 상태로 시작
        self.javdb_available = True if self.enable_javdb else False
        self.javdb_fail_count = 0
        self.javdb_403_count = 0  # 연속 403 응답 카운트
        
        # JAVLibrary HTTP 차단 감지 플래그
        self.javlibrary_blocked = False  # HTTP 403이 발생하면 True로 설정
        
        # JAVBEE, JAVGURU, JAVMOST HTTP 차단 감지 플래그
        self.javbee_blocked = False  # HTTP 403이 연속 50회 발생하면 True로 설정
        self.javbee_403_count = 0  # 연속 403 응답 카운트
        self.javguru_blocked = False  # HTTP 403이 연속 50회 발생하면 True로 설정
        self.javguru_403_count = 0  # 연속 403 응답 카운트
        self.javmost_blocked = False  # HTTP 403이 연속 50회 발생하면 True로 설정
        self.javmost_403_count = 0  # 연속 403 응답 카운트
        self.javmost_session = None  # JAVMOST 전용 세션 (재활용, 403 시 재생성)
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
    
    def search_images(self, title: str, max_images: int = 5, exclude_hosts: List[str] = None, exclude_servers: List[str] = None) -> dict:
        """제목으로 이미지 검색 (MissAV → JAVLibrary → JAVDB → FC2PPV.stream)
        exclude_hosts: 해당 호스트를 포함하는 URL은 제외 (교체 기능용)
        exclude_servers: 탐색하지 않을 서버 목록 (예: ['javlibrary', 'javdb'])
        """
        if exclude_servers is None:
            exclude_servers = []
        codes = self._extract_codes(title)
        image_urls: List[str] = []

        def _filter_urls(urls: List[str]) -> List[str]:
            filtered: List[str] = []
            for u in urls:
                if not u:
                    continue
                # GIF 파일 제외 (더 정확한 패턴)
                u_lower = u.lower()
                import re
                is_gif = (
                    u_lower.endswith('.gif') or
                    u_lower.endswith('.gif?') or
                    u_lower.endswith('.gif&') or
                    u_lower.endswith('.gif#') or
                    u_lower.endswith('.gif/') or
                    '.gif?' in u_lower or
                    '.gif&' in u_lower or
                    '.gif#' in u_lower or
                    '.gif/' in u_lower or
                    re.search(r'\.gif[\?#&/]', u_lower) is not None
                )
                if is_gif:
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

            # 1) MissAV 최우선 시도 (Selenium 사용) - missav는 현재 비활성화
            # if 'missav' not in exclude_servers and ENABLE_SELENIUM_FOR_IMAGES:
            #     for code in codes:
            #         urls = self._search_missav_selenium(code)
            #         image_urls.extend(urls)
            #         if len(image_urls) >= max_images:
            #             break
            #     
            #     # MissAV 결과가 있으면 바로 리턴
            #     if image_urls:
            #         image_urls = _filter_urls(image_urls)
            #         if image_urls:
            #             print(f"[ImageFinder] MissAV 성공!")
            #             return {
            #                 'thumbnail': image_urls[0],
            #                 'snapshots': []
            #             }

            # 2) JAVLibrary 시도
            if 'javlibrary' not in exclude_servers:
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
            
            # 3) JAVDB 시도 (Selenium 활성화 시 Selenium만, 아니면 HTTP만) - javdb는 현재 비활성화
            # if 'javdb' not in exclude_servers:
            #     for code in codes:
            #         if ENABLE_SELENIUM_FOR_IMAGES:
            #             # Selenium만 사용 (HTTP는 대부분 차단됨)
            #             urls = self._search_javdb_selenium(code)
            #             image_urls.extend(urls)
            #         else:
            #             # HTTP만 사용
            #             urls_http = self._search_javdb(code)
            #             image_urls.extend(urls_http)
            #         
            #         if len(image_urls) >= max_images:
            #             break
            #     
            #     # 4) 결과가 있으면 바로 리턴 (빠른 응답)
            #     if image_urls:
            #         image_urls = _filter_urls(image_urls)
            #         if image_urls:
            #             print(f"[ImageFinder] JAVDB 성공!")
            #             return {
            #                 'thumbnail': image_urls[0],
            #                 'snapshots': []
            #             }
            
            # 5) JAVBee 시도 (현재 활성화된 서버)
            if 'javbee' not in exclude_servers:
                for code in codes:
                    urls = self._search_javbee(code, title=title)
                    image_urls.extend(urls)
                    if len(image_urls) >= max_images:
                        break
                
                # 결과가 있으면 바로 리턴
                if image_urls:
                    image_urls = _filter_urls(image_urls)
                    if image_urls:
                        print(f"[ImageFinder] JAVBee 성공!")
                        return {
                            'thumbnail': image_urls[0],
                            'snapshots': []
                        }
            
            # 6) Sukebei Nyaa 시도
            if 'nyaa' not in exclude_servers:
                for code in codes:
                    urls = self._search_nyaa(code)
                    image_urls.extend(urls)
                    if len(image_urls) >= max_images:
                        break
                
                # 결과가 있으면 바로 리턴
                if image_urls:
                    image_urls = _filter_urls(image_urls)
                    if image_urls:
                        print(f"[ImageFinder] Sukebei Nyaa 성공!")
                        return {
                            'thumbnail': image_urls[0],
                            'snapshots': []
                        }
            
            # 7) JAV.GURU 시도 (모든 형태의 제목 검색 가능)
            if 'javguru' not in exclude_servers:
                for code in codes:
                    urls = self._search_javguru(code)
                    image_urls.extend(urls)
                    if len(image_urls) >= max_images:
                        break
                
                # 결과가 있으면 바로 리턴
                if image_urls:
                    image_urls = _filter_urls(image_urls)
                    if image_urls:
                        print(f"[ImageFinder] JAV.GURU 성공!")
                        return {
                            'thumbnail': image_urls[0],
                            'snapshots': []
                        }
            
            # 8) JAVMOST 시도 (모든 형태의 제목 검색 가능)
            if 'javmost' not in exclude_servers:
                for code in codes:
                    urls = self._search_javmost(code)
                    image_urls.extend(urls)
                    if len(image_urls) >= max_images:
                        break
                
                # 결과가 있으면 바로 리턴
                if image_urls:
                    image_urls = _filter_urls(image_urls)
                    if image_urls:
                        print(f"[ImageFinder] JAVMOST 성공!")
                        return {
                            'thumbnail': image_urls[0],
                            'snapshots': []
                        }
            
            # 9) 최후의 수단: FC2PPV.stream (FC2 코드가 있을 때만)
            if 'fc2ppv' not in exclude_servers and fc2_codes:
                for fc2_code in fc2_codes:
                    urls = self._search_fc2ppv_stream(fc2_code)
                    image_urls.extend(urls)
                    if len(image_urls) >= max_images:
                        break
                
                # FC2PPV.stream 결과가 있으면 리턴
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
        
        # MissAV 먼저 시도 (Selenium 사용 시) - missav는 현재 비활성화
        # if 'missav' not in exclude_servers and ENABLE_SELENIUM_FOR_IMAGES:
        #     urls = self._search_missav_selenium(title)
        #     image_urls.extend(urls)
        
        # MissAV 실패 시 JAVLibrary 시도
        if 'javlibrary' not in exclude_servers and not image_urls:
            urls = self._search_javdatabase(title)
            image_urls.extend(urls)
        
        # JAVLibrary 실패 시 JAVDB 시도 - javdb는 현재 비활성화
        # if 'javdb' not in exclude_servers and not image_urls:
        #     if ENABLE_SELENIUM_FOR_IMAGES:
        #         urls = self._search_javdb_selenium(title)
        #         image_urls.extend(urls)
        #     else:
        #         urls_http = self._search_javdb(title)
        #         image_urls.extend(urls_http)
        
        # JAVDB 실패 시 JAVBee 시도
        if 'javbee' not in exclude_servers and not image_urls:
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
        
        # JAVBee 실패 시 Sukebei Nyaa 시도 (모든 형태의 품번 검색 가능)
        if 'nyaa' not in exclude_servers and not image_urls:
            if codes:
                for code in codes:
                    urls = self._search_nyaa(code)
                    image_urls.extend(urls)
                    if image_urls:
                        break
            else:
                # 코드가 없으면 전체 제목으로 검색
                urls = self._search_nyaa(title)
                image_urls.extend(urls)
        
        # Sukebei Nyaa 실패 시 JAV.GURU 시도 (모든 형태의 제목 검색 가능)
        if 'javguru' not in exclude_servers and not image_urls:
            if codes:
                for code in codes:
                    urls = self._search_javguru(code)
                    image_urls.extend(urls)
                    if image_urls:
                        break
            else:
                # 코드가 없으면 전체 제목으로 검색
                urls = self._search_javguru(title)
                image_urls.extend(urls)
        
        # JAV.GURU 실패 시 JAVMOST 시도 (모든 형태의 제목 검색 가능)
        if 'javmost' not in exclude_servers and not image_urls:
            if codes:
                for code in codes:
                    urls = self._search_javmost(code)
                    image_urls.extend(urls)
                    if image_urls:
                        break
            else:
                # 코드가 없으면 전체 제목으로 검색
                urls = self._search_javmost(title)
                image_urls.extend(urls)
        
        # JAV.GURU 실패 시 FC2PPV.stream 시도 (FC2 코드가 있을 때만)
        if 'fc2ppv' not in exclude_servers and not image_urls and fc2_codes:
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
        
        # 숫자-문자-숫자 패턴 (예: 4017-PPV147, 4092-PPV352, 4092-PPV-352)
        # 숫자로 시작하는 패턴을 먼저 확인 (더 구체적인 코드일 가능성이 높음)
        pattern_num_alpha_num = r'(\d{3,5})[-\s]?([A-Z]{2,10})[-\s]?(\d{3,6})(?=[^\w]|$)'
        # 원본 형식을 유지하기 위해 finditer 사용
        matches_num_alpha_num = re.finditer(pattern_num_alpha_num, title_upper)
        
        # 제외할 키워드 목록 (코덱, 포맷, 프로토콜 등)
        excluded_alpha = {
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
        
        for match_obj in matches_num_alpha_num:
            num_prefix, alpha, number = match_obj.groups()
            # 원본 형식 그대로 사용 (대문자로 변환, 공백은 하이픈으로)
            code = match_obj.group(0).replace(' ', '-').upper()
            
            # 제외 목록에 없고, 실제 작품번호처럼 보이는 것만 추가
            if alpha not in excluded_alpha:
                # 숫자가 너무 작거나 크면 제외 (작품번호는 보통 3-6자리)
                if len(number) >= 3 and len(number) <= 6:
                    codes.append(code)
        
        # 문자-숫자-문자 패턴 (예: MXNB-001S, IPX-123A) - 숫자 뒤에 문자가 있는 경우
        pattern_av_suffix = r'([A-Z]{1,10})[-\s]?(\d{3,6})[-\s]?([A-Z]{1,3})(?=[^\w]|$)'
        matches_av_suffix = re.finditer(pattern_av_suffix, title_upper)
        
        for match_obj in matches_av_suffix:
            prefix, number, suffix = match_obj.groups()
            # 원본 형식 그대로 사용 (대문자로 변환, 공백은 하이픈으로)
            code = match_obj.group(0).replace(' ', '-').upper()
            
            # 제외 목록에 없고, 실제 작품번호처럼 보이는 것만 추가
            if prefix not in excluded_alpha:
                # 숫자가 너무 작거나 크면 제외 (작품번호는 보통 3-6자리)
                if len(number) >= 3 and len(number) <= 6:
                    codes.append(code)
        
        # 일반 AV 코드 패턴 (예: IPX-358, MIDA-398, COGM-089, STARS-573, heydouga 4144-051)
        # 문자 길이를 1~10자로 확장하여 다양한 케이스 지원
        # 숫자 뒤에 하이픈과 추가 숫자가 있는 경우도 포함 (예: heydouga 4144-051)
        # 원본 형식을 유지하기 위해 원본 제목에서 직접 매칭 (대소문자 구분 없이)
        pattern_av_extended = r'([A-Za-z]{1,10})[-\s]?(\d{3,6})(?:[-\s](\d{1,6}))?(?=[^\w]|$)'
        matches_av_extended = re.finditer(pattern_av_extended, title, re.IGNORECASE)
        
        # 전체 코드 형태로도 제외할 목록 (코덱 정보 등)
        excluded_codes = {
            'H-265', 'H-264', 'H265', 'H264',
            'X-265', 'X-264', 'X265', 'X264'
        }
        
        for match_obj in matches_av_extended:
            prefix, number, suffix_num = match_obj.groups()
            # 원본 형식 그대로 사용 (공백은 그대로 유지, 대소문자도 원본 유지)
            code = match_obj.group(0)
            
            # 이미 문자-숫자-문자 패턴으로 추가된 코드는 제외 (대소문자 구분 없이 비교)
            if any(c.upper() == code.upper() for c in codes):
                continue
            
            # 제외 목록에 없고, 실제 작품번호처럼 보이는 것만 추가
            # prefix와 전체 code 모두 체크 (FC2는 이미 별도 패턴으로 처리되므로 제외)
            if prefix.upper() not in excluded_alpha and code.upper() not in excluded_codes:
                # FC2는 이미 별도 패턴으로 처리했으므로 일반 패턴에서는 제외
                if prefix.upper() == 'FC2':
                    continue
                # 숫자가 너무 작거나 크면 제외 (작품번호는 보통 3-6자리)
                if len(number) >= 3 and len(number) <= 6:
                    # suffix_num이 있으면 더 긴 코드를 우선시 (예: heydouga 4144-051)
                    # suffix_num이 없으면 기본 코드 (예: heydouga 4144)
                    codes.append(code)
        
        # 중복 제거 및 더 긴 코드 우선 정렬
        # 예: "heydouga 4144"와 "heydouga 4144-051"이 모두 있으면 더 긴 것을 우선
        unique_codes = {}
        for code in codes:
            # 같은 prefix로 시작하는 코드 중 더 긴 것을 우선 (대소문자 구분 없이)
            prefix_match = re.match(r'^([A-Za-z]+)', code, re.IGNORECASE)
            if prefix_match:
                prefix = prefix_match.group(1).upper()  # 비교를 위해 대문자로 변환
                if prefix not in unique_codes or len(code) > len(unique_codes[prefix]):
                    unique_codes[prefix] = code
        
        # unique_codes의 값들을 리스트로 변환 (순서 유지)
        codes = list(unique_codes.values())
        
        # 추가로 완전히 동일한 코드 중복 제거
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
        """JAVDB.com에서 이미지 검색 (test_javdb_cover_search.py 기반)"""
        if not self.javdb_available:
            return []
        try:
            # JAVDB 미러 도메인 목록
            javdb_bases = [
                "https://javdb.com",
            ]
            
            # cloudscraper 사용 가능 여부 확인
            use_cloudscraper = False
            try:
                import cloudscraper
                use_cloudscraper = True
            except ImportError:
                pass
            
            # HTTP 클라이언트 생성 (cloudscraper 우선 사용)
            session = None
            if use_cloudscraper:
                try:
                    import cloudscraper
                    session = cloudscraper.create_scraper(
                        browser={"browser": "chrome", "platform": "windows", "mobile": False}
                    )
                    print(f"[ImageFinder] JAVDB: cloudscraper 사용")
                except Exception as e:
                    print(f"[ImageFinder] JAVDB: cloudscraper 실패, requests 사용: {e}")
                    session = None
            
            if session is None:
                session = requests.Session()
                print(f"[ImageFinder] JAVDB: requests.Session 사용")
            
            # 세션 헤더 설정
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9,ko;q=0.8',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'DNT': '1',
            })
            
            # 쿠키 설정
            for dom in [".javdb.com"]:
                try:
                    session.cookies.set("over18", "1", domain=dom)
                except Exception:
                    pass
            
            # 미러 도메인 시도
            html = None
            base_url = None
            search_url = None
            
            for base in javdb_bases:
                try:
                    url = f"{base}/search?{urlencode({'q': code, 'f': 'all'})}"
                    # 홈 워밍업
                    session.get(base + "/", headers={"Referer": base + "/"}, timeout=20)
                    # 검색 요청
                    r = session.get(url, headers={"Referer": base + "/"}, timeout=25, allow_redirects=True)
                    # 403 응답이면 연결을 처음부터 다시 시도
                    if r.status_code == 403:
                        self.javdb_403_count += 1
                        
                        if self.javdb_403_count >= 50:
                            print(f"[ImageFinder] JAVDB 서버 연결 안됨: 50번 연속 403 응답으로 인해 JAVDB 비활성화")
                            self.javdb_available = False
                            self.javdb_fail_count = 999  # 차단 상태 유지
                            return []  # 50번 연속이면 차단
                        
                        # 세션 쿠키 초기화하고 처음부터 다시 시도
                        time.sleep(1.2)  # 403 발생 시 대기
                        session.cookies.clear()  # 쿠키 초기화
                        
                        # 쿠키 다시 설정
                        for dom in [".javdb.com"]:
                            try:
                                session.cookies.set("over18", "1", domain=dom)
                            except Exception:
                                pass
                        
                        # 홈 워밍업 (처음부터)
                        session.get(base + "/", headers={"Referer": base + "/"}, timeout=20)
                        # 검색 요청 재시도
                        r = session.get(url, headers={"Referer": base + "/"}, timeout=25, allow_redirects=True)
                        
                        # 재시도 후에도 403이면 다음 미러로
                        if r.status_code == 403:
                            continue  # 다음 미러 도메인으로
                    
                    if r.status_code == 200 and len(r.text) > 1000:
                        html = r.text
                        base_url = base
                        search_url = url
                        # 연결 성공 시 활성화 상태 복구 및 403 카운트 리셋
                        if not self.javdb_available:
                            self.javdb_available = True
                            self.javdb_fail_count = 0
                        self.javdb_403_count = 0  # 성공 시 403 카운트 리셋
                        break
                except Exception as e:
                    continue
            
            if not html:
                # 403이 아닌 다른 오류만 실패 카운트 증가
                # (403은 javdb_403_count로 별도 처리되므로 여기서는 증가하지 않음)
                # 403이 발생한 경우는 이미 위에서 continue로 넘어갔으므로 여기 도달하지 않음
                # 하지만 혹시 모를 경우를 대비해 403 카운트가 증가하지 않은 경우만 실패 카운트 증가
                if self.javdb_403_count == 0:  # 403이 아닌 다른 오류인 경우만
                    self.javdb_fail_count += 1
                    if self.javdb_fail_count >= 3 and self.javdb_available:
                        self.javdb_available = False
                        print("[ImageFinder] JAVDB 서버 연결 안됨: 이후 요청부터 JAVDB 검색을 비활성화합니다.")
                return []
            
            soup = BeautifulSoup(html, 'lxml')
            image_urls = []
            
            # 첫 번째 결과 카드 찾기 (test_javdb_cover_search.py 로직)
            def find_first_card_and_title(soup):
                """첫 번째 결과 카드와 제목 텍스트 반환"""
                anchors = soup.select('a[href^="/v/"]')
                for a in anchors:
                    has_visual = bool(a.select_one("img, .cover, .video-cover, .image"))
                    if not has_visual:
                        continue
                    title_node = a.select_one(".title, strong, h3, h2")
                    title_txt = (title_node.get_text(" ", strip=True) if title_node else a.get_text(" ", strip=True)) or ""
                    if len(title_txt) >= 4:
                        return a, title_txt
                return None, None
            
            anchor, title_text = find_first_card_and_title(soup)
            if not anchor:
                return []
            
            # 제목 매칭 검증 (키워드 엄격 매칭)
            def compile_keyword_strict(keyword):
                """문자+숫자 정확 일치, 문자/숫자 사이 '-' 옵션"""
                m = re.match(r"^\s*([A-Za-z]+)\s*-?\s*(\d+)\s*$", keyword.strip())
                if not m:
                    k = keyword.strip()
                    k = re.escape(k).replace(r"\-", "-?")
                    return re.compile(rf"(?<![A-Za-z0-9]){k}(?![A-Za-z0-9])", re.I)
                prefix, num = m.groups()
                return re.compile(rf"(?<![A-Za-z0-9]){re.escape(prefix)}-?{re.escape(num)}(?![A-Za-z0-9])", re.I)
            
            kw_re = compile_keyword_strict(code)
            match_result = kw_re.search(title_text) if title_text else None
            if not title_text or not match_result:
                return []  # 제목 불일치
            
            # 카드 이미지 수집
            def _extract_bg_url(style_str):
                """스타일에서 background-image URL 추출"""
                if not style_str:
                    return None
                m = re.search(r"url\((['\"]?)(.+?)\1\)", style_str)
                return m.group(2) if m else None
            
            def collect_card_images(anchor, base_url):
                """카드 내부의 커버 이미지 수집"""
                cands = []
                
                def add(u, how):
                    if not u:
                        return
                    if u.startswith("//"):
                        u = "https:" + u
                    cands.append({"url": urljoin(base_url, u), "how": how})
                
                # <img>
                for img in anchor.select("img"):
                    add(img.get("src"), "src")
                    add(img.get("data-src"), "data-src")
                    add(img.get("data-original"), "data-original")
                    ss = img.get("srcset")
                    if ss:
                        parts = [p.strip() for p in ss.split(",") if p.strip()]
                        for p in reversed(parts):  # 해상도 큰 것부터
                            add(p.split()[0], "srcset")
                
                # background-image
                for cov in anchor.select(".cover, .video-cover, .image"):
                    bg = _extract_bg_url(cov.get("style", ""))
                    add(bg, "bg-style")
                
                # 중복 제거
                uniq, seen = [], set()
                for c in cands:
                    if c["url"] not in seen:
                        uniq.append(c)
                        seen.add(c["url"])
                return uniq
            
            url_items = collect_card_images(anchor, base_url or "https://javdb.com")
            
            # URL 추출 (첫 번째 이미지만 반환)
            for item in url_items[:1]:  # 첫 번째 이미지만 사용
                url = item["url"]
                if url and url.startswith('http'):
                    image_urls.append(url)
            
            # HTTP 200 응답을 받았으므로 403 카운트 리셋 (이미지를 찾았든 못 찾았든)
            self.javdb_403_count = 0  # 제대로 응답을 받았으므로 403 카운트 초기화
            
            # 요청 성공이므로 실패 카운터 리셋 및 활성화 상태 복구
            if image_urls:
                self.javdb_fail_count = 0
                if not self.javdb_available:
                    self.javdb_available = True
                    print(f"[ImageFinder] JAVDB 재활성화: 이미지 발견으로 인해 활성화 상태로 복구")
                return image_urls
            
            return []
            
        except (ConnectionError, Timeout) as e:
            # 연속 실패 카운트 및 자동 비활성화
            self.javdb_fail_count += 1
            if self.javdb_fail_count >= 3 and self.javdb_available:
                self.javdb_available = False
                print("[ImageFinder] JAVDB 서버 연결 안됨: 이후 요청부터 JAVDB 검색을 비활성화합니다.")
            return []
        except RequestException as e:
            print(f"[ImageFinder] JAVDB 요청 예외 (코드: {code}): {e}")
            return []
        except Exception as e:
            import traceback
            print(f"[ImageFinder] JAVDB 검색 오류 (코드: {code}): {e}")
            print(f"[ImageFinder] JAVDB 검색 오류 상세: {traceback.format_exc()}")
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
    
    def _search_javmost(self, keyword: str) -> List[str]:
        """JAVMOST(www5.javmost.com)에서 이미지 검색 (test_javmost_cover_search.py 로직 기반)
        
        Args:
            keyword: 검색 키워드 (작품번호 또는 제목)
        """
        if self.javmost_blocked:
            return []
        
        try:
            from urllib.parse import urlparse, urljoin as urljoin_parse
            BASE = "https://www5.javmost.com/"
            MIN_BYTES = 10 * 1024  # 10KB
            UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            
            # 세션 재활용 (403 에러 시에만 재생성)
            if self.javmost_session is None:
                self.javmost_session = requests.Session()
            javmost_session = self.javmost_session
            
            # 자산 필터
            THUMB_HOSTS = ("i0.wp.com", "i1.wp.com", "i2.wp.com")
            ASSET_SEG_RE = re.compile(
                r"(?:^|/)(?:logo|favicon|sprite|icons?|ads?|adserver|banners?|static|assets|themes|emoji|svg)(?:/|$)",
                re.I
            )
            AD_HOST_RE = re.compile(r"(?:exosrv|exdynsrv|syndication|doubleclick|adnxs|taboola|outbrain|histats)", re.I)
            
            def is_probably_asset(u: str) -> bool:
                pr = urlparse(u)
                if AD_HOST_RE.search(pr.netloc):
                    return True
                path = pr.path
                if any(host in pr.netloc for host in THUMB_HOSTS):
                    return True
                return bool(ASSET_SEG_RE.search(path))
            
            def compile_keyword_strict(keyword: str):
                """문자+숫자 정확 일치, 문자/숫자 사이 '-' 옵션."""
                m = re.match(r"^\s*([A-Za-z]+)\s*-?\s*(\d+)\s*$", keyword.strip())
                if not m:
                    k = keyword.strip()
                    k = re.escape(k).replace(r"\-", "-?")
                    return re.compile(rf"(?<![A-Za-z0-9]){k}(?![A-Za-z0-9])", re.I)
                prefix, num = m.groups()
                return re.compile(
                    rf"(?<![A-Za-z0-9]){re.escape(prefix)}-?{re.escape(num)}(?![A-Za-z0-9])",
                    re.I
                )
            
            def normalize_code(keyword: str):
                """키워드를 'PREFIX-NNN' 형태로 정규화."""
                m = re.match(r"^\s*([A-Za-z]+)\s*-?\s*(\d+)\s*$", keyword.strip())
                if not m:
                    return None, None, None
                prefix, num = m.groups()
                code = f"{prefix.upper()}-{num}"
                return prefix.upper(), num, code
            
            def make_headers(referer: str | None = None) -> dict:
                """테스트 코드와 동일한 헤더 생성"""
                h = {
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "Upgrade-Insecure-Requests": "1",
                    "DNT": "1",
                }
                if referer:
                    h["Referer"] = referer
                return h
            
            def get_html(url: str, referer: str | None = None) -> str:
                """테스트 코드와 동일한 get_html 함수"""
                r = javmost_session.get(url, headers=make_headers(referer), timeout=25)
                r.raise_for_status()
                return r.text
            
            def head_or_small_get(url: str, referer: str) -> dict:
                """이미지 여부/크기 검사: HEAD → 실패 시 작은 GET"""
                headers = make_headers(referer)
                try:
                    r = javmost_session.head(url, headers=headers, allow_redirects=True, timeout=15)
                    ct = (r.headers.get("content-type") or "").lower()
                    cl = r.headers.get("content-length")
                    size = int(cl) if cl and cl.isdigit() else None
                    return {"ok": ct.startswith("image/"), "final_url": r.url, "ct": ct, "size": size}
                except Exception:
                    pass
                try:
                    with javmost_session.get(url, headers=headers, stream=True, allow_redirects=True, timeout=25) as g:
                        ct = (g.headers.get("content-type") or "").lower()
                        cl = g.headers.get("content-length")
                        size = int(cl) if cl and cl.isdigit() else None
                        return {"ok": ct.startswith("image/"), "final_url": g.url, "ct": ct, "size": size}
                except Exception:
                    return {"ok": False, "final_url": url, "ct": "", "size": None}
            
            def try_direct_view(code: str):
                """/<CODE>/ 직행 시도."""
                view = urljoin_parse(BASE, f"{code}/")
                try:
                    html = get_html(view, referer=BASE)
                    soup = BeautifulSoup(html, "lxml")
                    title = ""
                    if soup.title and soup.title.get_text():
                        title = soup.title.get_text(" ", strip=True)
                    h = soup.find(["h1", "h2"])
                    if not title and h:
                        title = h.get_text(" ", strip=True)
                    return view, (title or "")
                except Exception:
                    return None, None
            
            def find_from_tag_listing(prefix: str, kw_re: re.Pattern):
                """/tag/<PREFIX>/ 목록에서 '첫번째 엄격매칭' 포스트 링크를 찾는다."""
                tag_url = urljoin_parse(BASE, f"tag/{prefix}/")
                try:
                    html = get_html(tag_url, referer=BASE)
                    soup = BeautifulSoup(html, "lxml")
                    
                    for a in soup.select("a[href]"):
                        txt = (a.get_text(" ", strip=True) or "")[:500]
                        href = a.get("href") or ""
                        if not href:
                            continue
                        if any(seg in href for seg in ("/tag/", "/maker/", "/director/", "/category/", "/search/", "/allcode/")):
                            continue
                        if kw_re.search(txt):
                            return urljoin_parse(BASE, href), txt
                    return None, None
                except Exception:
                    return None, None
            
            def resolve_view_url_and_title(keyword: str):
                """키워드로 상세(view) URL과 제목을 찾아준다."""
                kw_re = compile_keyword_strict(keyword)
                prefix, num, code = normalize_code(keyword)
                
                # 1) 직접 슬러그
                if code:
                    v, t = try_direct_view(code)
                    if v and kw_re.search((t or "")):
                        return v, t
                
                # 2) 태그 목록 검색
                if prefix:
                    v, t = find_from_tag_listing(prefix, kw_re)
                    if v and t and kw_re.search(t):
                        return v, t
                
                return None, None
            
            def find_description_nodes(soup: BeautifulSoup):
                """상세 페이지에서 주 콘텐츠 영역 후보를 찾는다."""
                nodes = []
                nodes.extend(soup.select("article, main, section"))
                nodes.extend(soup.select("div.post, div.single, div.entry-content, div.content, .container"))
                if not nodes:
                    nodes = [soup]
                seen, uniq = set(), []
                for n in nodes:
                    k = str(n)
                    if k not in seen:
                        uniq.append(n)
                        seen.add(k)
                return uniq[:5]
            
            # 403 재시도 로직: 최대 3회 재시도 (서버 접속부터 다시)
            max_retries = 3
            view_url = None
            title_text = None
            
            for retry in range(max_retries):
                try:
                    view_url, title_text = resolve_view_url_and_title(keyword)
                    if view_url and title_text:
                        break
                except Exception as e:
                    if retry < max_retries - 1:
                        time.sleep(1.2)
                        continue
                    print(f"[ImageFinder] JAVMOST resolve_view_url_and_title 오류 (키워드: {keyword[:50]}): {e}")
                    return []
            
            if not view_url or not title_text:
                return []
            
            # 상세 페이지에서 이미지 추출 (테스트 코드와 동일하게 get_html 사용)
            # 403 체크를 위해 try-except로 처리
            html = None
            for retry in range(max_retries):
                try:
                    html = get_html(view_url, referer=BASE)
                    # 성공 시 403 카운트 리셋
                    if self.javmost_403_count > 0:
                        self.javmost_403_count = 0
                    break
                except requests.exceptions.HTTPError as e:
                    if e.response and e.response.status_code == 403:
                        self.javmost_403_count += 1
                        
                        if self.javmost_403_count >= 50:
                            if not self.javmost_blocked:
                                self.javmost_blocked = True
                                print(f"[ImageFinder] JAVMOST 서버 연결 안됨: 50번 연속 403 응답으로 인해 JAVMOST 검색 비활성화")
                            return []
                        
                        if retry < max_retries - 1:
                            time.sleep(1.2)
                            # 403 에러 시 세션 재생성
                            self.javmost_session = requests.Session()
                            javmost_session = self.javmost_session
                            try:
                                javmost_session.get(BASE, headers=make_headers(BASE), timeout=25)
                            except Exception:
                                pass
                            continue
                        else:
                            return []
                    else:
                        # 403이 아닌 다른 HTTP 오류
                        if retry < max_retries - 1:
                            time.sleep(1.2)
                            continue
                        else:
                            return []
                except Exception as e:
                    if retry < max_retries - 1:
                        time.sleep(1.2)
                        continue
                    else:
                        return []
            
            if not html:
                return []
            
            soup = BeautifulSoup(html, "lxml")
            results = []
            raw = []
            
            def add_img_url(u, how):
                if u:
                    raw.append({"url": urljoin_parse(view_url, u), "how": how})
            
            # 설명/본문 영역 후보
            nodes = find_description_nodes(soup) or [soup]
            extra = soup.select("div#main, div#primary, div#content, div.single, div.entry-content")
            nodes = (nodes + extra)[:8]
            
            # 1) 실제 DOM의 <img> + 광범위 data-* 속성 커버
            DATA_ATTR_HINTS = {
                "data-src", "data-original", "data-lazy", "data-lazy-src",
                "data-echo", "data-image", "data-img", "data-url", "data-srcset"
            }
            for n in nodes:
                for img in n.find_all("img"):
                    add_img_url(img.get("src"), "img.src")
                    srcset = img.get("srcset")
                    if srcset:
                        for p in [p.strip() for p in srcset.split(",") if p.strip()]:
                            add_img_url(p.split()[0], "img.srcset")
                    for k, v in img.attrs.items():
                        if not v or not isinstance(v, str):
                            continue
                        if k in DATA_ATTR_HINTS or k.startswith("data-"):
                            add_img_url(v, f"img.{k}")
            
            # 2) noscript 내 <img>
            for n in nodes:
                for nos in n.find_all("noscript"):
                    inner = BeautifulSoup(nos.get_text() or "", "lxml")
                    for img in inner.find_all("img"):
                        add_img_url(img.get("src"), "noscript.img.src")
                        for k in ("data-src", "data-original", "data-lazy", "data-lazy-src"):
                            add_img_url(img.get(k), f"noscript.img.{k}")
                        sset = img.get("srcset")
                        if sset:
                            for p in [p.strip() for p in sset.split(",") if p.strip()]:
                                add_img_url(p.split()[0], "noscript.img.srcset")
            
            # 3) 메타 폴백
            for m in soup.select('meta[property="og:image"], meta[name="twitter:image"]'):
                add_img_url(m.get("content"), "meta.og_or_twitter")
            for l in soup.select('link[rel="image_src"]'):
                add_img_url(l.get("href"), "link.image_src")
            
            # 4) JAVMOST 포스터 추정: https://img{1..5}.javmost.com/images/<CODE>.webp
            pr = urlparse(view_url)
            host = pr.netloc.lower()
            if host.endswith("javmost.com"):
                slug = pathlib.Path(pr.path).parts[-1].strip("/") or ""
                mcode = re.search(r"([A-Za-z]+-?\d+)", slug)
                if mcode:
                    code = mcode.group(1).upper().replace("--", "-")
                    for n in ("3", "2", "1", "4", "5"):
                        cand = f"https://img{n}.javmost.com/images/{code}.webp"
                        add_img_url(cand, f"poster.guess.img{n}")
            
            # 중복 제거
            seen, cands = set(), []
            for it in raw:
                u = (it["url"] or "").strip()
                if u and u not in seen:
                    cands.append(it)
                    seen.add(u)
            
            # 필터링 및 검증
            for item in cands:
                u, how = item["url"], item["how"]
                
                # 자산/광고 제외
                if is_probably_asset(u):
                    continue
                
                # .html 제외
                ext = pathlib.Path(urlparse(u).path).suffix.lower()
                if ext == ".html":
                    continue
                
                # 네트워크 검사: image/* 만 허용 + 최소 용량
                probe = head_or_small_get(u, view_url)
                if not probe["ok"]:
                    continue
                size_ok = (probe["size"] is None) or (probe["size"] >= MIN_BYTES)
                if not size_ok:
                    continue
                
                # GIF 제외
                if probe["ct"] and "gif" in probe["ct"].lower():
                    continue
                
                results.append(u)
                if len(results) >= 5:
                    break
            
            return results
            
        except Exception as e:
            print(f"[ImageFinder] JAVMOST 검색 오류: {e}")
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
            
            # 403 재시도 로직: 최대 3회 재시도 (서버 접속부터 다시)
            max_retries = 3
            response = None
            for retry in range(max_retries):
                response = self._safe_get(search_url, headers=headers, timeout=self.http_timeout)
                if not response:
                    if retry < max_retries - 1:
                        time.sleep(1.2)  # 재시도 전 대기
                        continue
                    return []
                
                # HTTP 403이면 카운트 증가 및 재시도 (서버 접속부터 다시)
                if response.status_code == 403:
                    self.javbee_403_count += 1
                    
                    if self.javbee_403_count >= 50:
                        if not self.javbee_blocked:
                            self.javbee_blocked = True
                            print(f"[ImageFinder] JAVBee 서버 연결 안됨: 50번 연속 403 응답으로 인해 JAVBee 검색 비활성화")
                        return []
                    
                    if retry < max_retries - 1:
                        # 세션 쿠키 초기화하고 처음부터 다시 시도
                        time.sleep(1.2)  # 403 발생 시 대기
                        self.session.cookies.clear()  # 쿠키 초기화
                        
                        # 홈페이지 워밍업 (처음부터)
                        try:
                            self._safe_get("https://javbee.vip/", headers={'Referer': 'https://javbee.vip/'}, timeout=self.http_timeout)
                        except Exception:
                            pass
                        
                        continue  # 재시도
                    else:
                        return []  # 재시도 실패
                
                # 200 응답이면 403 카운트 리셋
                if response.status_code == 200:
                    if self.javbee_403_count > 0:
                        self.javbee_403_count = 0  # 성공 시 403 카운트 리셋
                    break  # 성공, 루프 종료
                
                # 403이 아닌 다른 오류
                if retry < max_retries - 1:
                    time.sleep(1.2)  # 재시도 전 대기
                    continue
                else:
                    print(f"[ImageFinder] JAVBee 검색 페이지 접근 실패 (제목: {display_title}): {response.status_code}")
                    return []
            
            if not response or response.status_code != 200:
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
                # 앵커가 없으면 즉시 반환하여 다른 서버에서 검색하도록 함 (로그 생략)
                return []
            
            # 첫 번째 카드만 사용 (test.py 로직)
            anchor = anchors[0]
            
            # 카드 제목 추출 & 키워드 엄격 매칭
            title_text = find_card_title_text(anchor)
            
            # 카드 제목에서 코드 추출 (전체 코드 추출, 앞의 숫자 접두사 포함)
            def extract_code_from_title(card_title: str) -> str:
                """카드 제목에서 코드 추출 (예: '326KNTR-003' -> '326KNTR-003', '259LUXU-1560' -> '259LUXU-1560', 'FC2-PPV-3600322' -> 'FC2-PPV-3600322', '4092-PPV361' -> '4092-PPV361')"""
                if not card_title:
                    return ""
                # 패턴: [숫자]-[문자+숫자] 또는 [숫자][문자+숫자] 또는 FC2-PPV-숫자
                # 예: '326KNTR-003' -> '326KNTR-003' (전체), '259LUXU-1560' -> '259LUXU-1560' (전체), '4092-PPV361' -> '4092-PPV361'
                # FC2 코드 우선 확인 (반드시 FC2로 시작해야 함)
                fc2_pattern = re.compile(r'\bFC2[-\s]?PPV[-\s]?\d+\b', re.I)
                fc2_match = fc2_pattern.search(card_title)
                if fc2_match:
                    return fc2_match.group(0).replace(' ', '-').upper()
                
                # 일반 코드 패턴: [숫자]-[문자+숫자] 또는 [숫자][문자+숫자]
                # 예: "4092-PPV361", "4092PPV361", "326KNTR-003"
                code_pattern = re.compile(r'\d+-?[A-Za-z]+-?\d+', re.I)
                matches = code_pattern.findall(card_title)
                if matches:
                    # 가장 긴 매치를 선택 (코드일 가능성이 높음)
                    return max(matches, key=len)
                
                # 숫자 접두사 없는 코드도 시도
                code_pattern2 = re.compile(r'[A-Za-z]+-?\d+', re.I)
                matches2 = code_pattern2.findall(card_title)
                if matches2:
                    return max(matches2, key=len)
                return ""
            
            # 검색 쿼리에서도 코드 추출
            def extract_code_from_query(query: str) -> str:
                """검색 쿼리에서 코드 추출"""
                if not query:
                    return ""
                # FC2 코드 우선 확인 (반드시 FC2로 시작해야 함)
                fc2_pattern = re.compile(r'\bFC2[-\s]?PPV[-\s]?\d+\b', re.I)
                fc2_match = fc2_pattern.search(query)
                if fc2_match:
                    return fc2_match.group(0).replace(' ', '-').upper()
                
                # 일반 코드 패턴: [숫자]-[문자+숫자] 또는 [숫자][문자+숫자] 또는 [문자+숫자]
                # 예: "4092-PPV361", "4092PPV361", "PPV361", "SSNI-123"
                code_pattern = re.compile(r'\d+-?[A-Za-z]+-?\d+|[A-Za-z]+-?\d+', re.I)
                matches = code_pattern.findall(query)
                if matches:
                    # 가장 긴 매치를 선택 (더 구체적인 코드일 가능성이 높음)
                    return max(matches, key=len)
                return query  # 코드 패턴이 없으면 원본 반환
            
            # 카드 제목에서 코드 추출
            card_code = extract_code_from_title(title_text) if title_text else ""
            # 검색 쿼리에서 코드 추출
            search_code = extract_code_from_query(search_query)
            
            # 코드 매칭 (대소문자 무시, 하이픈 무시)
            title_ok = False
            if card_code and search_code:
                # 정규화: 대문자로 변환, 하이픈과 공백 제거
                card_code_norm = re.sub(r'[-\s]', '', card_code.upper())
                search_code_norm = re.sub(r'[-\s]', '', search_code.upper())
                
                # 1) 완전 일치 확인
                title_ok = card_code_norm == search_code_norm
                
                # 2) FC2 코드의 경우 부분 일치 확인 (PPV-360이 FC2-PPV-3600322에 포함되는지)
                if not title_ok:
                    # 검색 코드가 PPV-숫자 형태이고, 카드 코드가 FC2-PPV-숫자 형태인 경우
                    if re.match(r'^PPV\d+$', search_code_norm) and re.match(r'^FC2PPV\d+$', card_code_norm):
                        # PPV 뒤의 숫자가 카드 코드의 숫자 시작 부분과 일치하는지 확인
                        search_num_match = re.search(r'PPV(\d+)', search_code_norm)
                        card_num_match = re.search(r'FC2PPV(\d+)', card_code_norm)
                        if search_num_match and card_num_match:
                            search_num = search_num_match.group(1)
                            card_num = card_num_match.group(1)
                            # 검색 숫자가 카드 숫자의 시작 부분과 일치하는지 확인
                            title_ok = card_num.startswith(search_num)
                    
                    # 검색 코드가 FC2-PPV-숫자 형태이고, 카드 코드도 FC2-PPV-숫자 형태인 경우
                    elif re.match(r'^FC2PPV\d+$', search_code_norm) and re.match(r'^FC2PPV\d+$', card_code_norm):
                        # 숫자 부분만 비교 (FC2-PPV-360과 FC2-PPV-3600322 매칭)
                        search_num_match = re.search(r'FC2PPV(\d+)', search_code_norm)
                        card_num_match = re.search(r'FC2PPV(\d+)', card_code_norm)
                        if search_num_match and card_num_match:
                            search_num = search_num_match.group(1)
                            card_num = card_num_match.group(1)
                            # 검색 숫자가 카드 숫자의 시작 부분과 일치하거나, 카드 숫자가 검색 숫자의 시작 부분과 일치하는지 확인
                            title_ok = card_num.startswith(search_num) or search_num.startswith(card_num)
                    
                    # 숫자 접두사가 있는 코드 매칭 (326KNTR-003과 NTR-003 매칭)
                    elif re.match(r'^\d+[A-Z]+\d+$', search_code_norm) and re.match(r'^\d+[A-Z]+\d+$', card_code_norm):
                        # 숫자 접두사 제거 후 비교
                        search_clean = re.sub(r'^\d+', '', search_code_norm)
                        card_clean = re.sub(r'^\d+', '', card_code_norm)
                        title_ok = search_clean == card_clean
                    elif re.match(r'^\d+[A-Z]+\d+$', search_code_norm):
                        # 검색 코드에 숫자 접두사가 있고, 카드 코드에도 숫자 접두사가 있는 경우
                        search_clean = re.sub(r'^\d+', '', search_code_norm)
                        if card_code_norm.endswith(search_clean) or search_clean in card_code_norm:
                            title_ok = True
                    elif re.match(r'^\d+[A-Z]+\d+$', card_code_norm):
                        # 카드 코드에 숫자 접두사가 있고, 검색 코드에는 없는 경우
                        card_clean = re.sub(r'^\d+', '', card_code_norm)
                        if search_code_norm == card_clean:
                            title_ok = True
                    
                    # 일반 코드의 경우 부분 일치 확인 (검색 코드가 카드 코드에 포함되는지)
                    elif search_code_norm in card_code_norm or card_code_norm in search_code_norm:
                        title_ok = True
            elif title_text:
                # 코드 추출 실패 시 기존 방식으로 폴백 (키워드 매칭)
                kw_re = compile_keyword_strict(search_query)
                title_ok = bool(kw_re.search(title_text))
            
            if not title_ok:
                return []  # 제목이 키워드와 일치하지 않으면 즉시 반환
            
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
                    else:
                        image_urls.append(u)
                    if len(image_urls) >= 3:
                        break
            
            # test.py 로직: 상세 페이지 방문하지 않음, 검색 결과 페이지에서만 찾음
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
    
    def _search_nyaa(self, keyword: str) -> List[str]:
        """Sukebei Nyaa에서 이미지 검색 (test_nya_cover_search.py 로직 기반)
        
        Args:
            keyword: 검색 키워드 (작품번호 등)
        """
        try:
            from urllib.parse import urlparse, urljoin as urljoin_parse
            BASE_SEARCH = "https://sukebei.nyaa.si/"
            MIN_BYTES = 10 * 1024  # 10KB
            
            # 자산 필터
            ASSET_SEG_RE = re.compile(
                r"(?:^|/)(?:logo|favicon|sprite|icons?|ads?|banners?|static|assets|themes|emoji|svg)(?:/|$)",
                re.I
            )
            
            def is_probably_asset(u: str) -> bool:
                path = urlparse(u).path
                return bool(ASSET_SEG_RE.search(path))
            
            def compile_keyword_strict(keyword: str):
                """'문자+숫자'가 정확히 같고, 문자/숫자 사이의 '-' 만 옵션."""
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
            
            def build_search_url(keyword: str) -> str:
                qs = urlencode({"f": 0, "c": "0_0", "q": keyword})
                return f"{BASE_SEARCH}?{qs}"
            
            def find_first_result_and_title(soup: BeautifulSoup):
                """검색 결과 테이블에서 가장 먼저 나오는 '/view/xxxx' 링크와 제목 텍스트를 찾음."""
                a = soup.select_one("td a[href^='/view/'], a[href^='/view/']")
                if not a:
                    return None, None
                title = (a.get_text(" ", strip=True) or "")[:500]
                href = a.get("href")
                if not href:
                    return None, None
                return urljoin_parse(BASE_SEARCH, href), title
            
            def find_description_nodes(soup: BeautifulSoup):
                """상세 페이지에서 '설명'에 해당하는 컨테이너 후보들을 찾는다."""
                nodes = []
                nodes.extend(soup.select("#torrent-description, #description"))
                for panel in soup.select(".panel"):
                    header = panel.select_one(".panel-heading")
                    if header and re.search(r"\bdescription\b", header.get_text(" ", strip=True), re.I):
                        body = panel.select_one(".panel-body") or panel
                        nodes.append(body)
                if not nodes:
                    nodes.extend(soup.select("article"))
                if not nodes:
                    nodes.extend(soup.select("div.content, .content, .container"))
                seen, uniq = set(), []
                for n in nodes:
                    k = str(n)
                    if k not in seen:
                        uniq.append(n); seen.add(k)
                return uniq[:3]
            
            # 1) 검색
            search_url = build_search_url(keyword)
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9,ko;q=0.8',
                'Referer': BASE_SEARCH
            }
            
            response = self._safe_get(search_url, headers=headers, timeout=self.http_timeout)
            if not response or response.status_code != 200:
                return []
            
            soup = BeautifulSoup(response.content, 'lxml')
            view_url, title_text = find_first_result_and_title(soup)
            
            if not view_url or not title_text:
                return []
            
            # 2) 제목 엄격 매칭
            kw_re = compile_keyword_strict(keyword)
            if not kw_re.search(title_text or ""):
                return []
            
            # 3) 상세 페이지에서 이미지 추출
            html = self._safe_get(view_url, headers=headers, timeout=self.http_timeout)
            if not html or html.status_code != 200:
                return []
            
            dsoup = BeautifulSoup(html.content, 'lxml')
            nodes = find_description_nodes(dsoup)
            if not nodes:
                nodes = [dsoup]
            
            # 이미지 URL 수집
            raw = []
            def add_img_url(u, how):
                if u:
                    raw.append({"url": urljoin_parse(view_url, u), "how": how})
            
            for n in nodes:
                for img in n.find_all("img"):
                    add_img_url(img.get("src"), "img.src")
                    add_img_url(img.get("data-src"), "img.data-src")
                    add_img_url(img.get("data-original"), "img.data-original")
                    srcset = img.get("srcset")
                    if srcset:
                        parts = [p.strip() for p in srcset.split(",") if p.strip()]
                        for p in parts:
                            add_img_url(p.split()[0], "img.srcset")
            
            # 마크다운 이미지 추출
            MD_IMG_LINK_RE = re.compile(
                r'\[!\[[^\]]*\]\((?P<img>https?://[^\s\)\]]+)\)\]\((?P<link>https?://[^\s\)\]]+)\)',
                re.I
            )
            for m in MD_IMG_LINK_RE.finditer(html.text or ""):
                img_url = m.group("img")
                if img_url:
                    add_img_url(img_url, "md.img")
            
            # 중복 제거 및 필터링
            seen, results = set(), []
            for it in raw:
                u = it["url"]
                if u in seen:
                    continue
                seen.add(u)
                
                # 자산 제외
                if is_probably_asset(u):
                    continue
                
                # GIF 파일 제외
                u_lower = u.lower()
                is_gif = (
                    u_lower.endswith('.gif') or
                    u_lower.endswith('.gif?') or
                    u_lower.endswith('.gif&') or
                    u_lower.endswith('.gif#') or
                    u_lower.endswith('.gif/') or
                    '.gif?' in u_lower or
                    '.gif&' in u_lower or
                    '.gif#' in u_lower or
                    '.gif/' in u_lower or
                    re.search(r'\.gif[\?#&/]', u_lower) is not None
                )
                if is_gif:
                    continue
                
                # .html 제외
                ext = pathlib.Path(urlparse(u).path).suffix.lower()
                if ext == ".html" or ext == ".gif":
                    continue
                
                # 간단한 이미지 검증 (HEAD 요청)
                try:
                    head_resp = self.session.head(u, headers=headers, allow_redirects=True, timeout=10)
                    ct = (head_resp.headers.get("content-type") or "").lower()
                    if not ct.startswith("image/"):
                        continue
                    # GIF content-type 제외
                    if "gif" in ct:
                        continue
                    cl = head_resp.headers.get("content-length")
                    size = int(cl) if cl and cl.isdigit() else None
                    if size and size < MIN_BYTES:
                        continue
                    results.append(u)
                except Exception:
                    continue
            
            return results[:5]  # 최대 5개만 반환
            
        except Exception as e:
            print(f"[ImageFinder] Sukebei Nyaa 검색 오류: {e}")
            return []
    
    def _search_javguru(self, keyword: str) -> List[str]:
        """JAV.GURU에서 이미지 검색 (test_javguru_cover_search.py 로직 기반)
        
        Args:
            keyword: 검색 키워드 (작품번호 또는 제목)
        """
        try:
            from urllib.parse import urlparse, urljoin as urljoin_parse, urlencode
            BASE = "https://jav.guru"
            MIN_BYTES = 10 * 1024  # 10KB
            UA = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            # cloudscraper 우선 사용 (테스트 프로그램과 동일)
            def create_http_client():
                """cloudscraper 우선, 실패 시 requests.Session"""
                s = None
                try:
                    import cloudscraper
                    s = cloudscraper.create_scraper(
                        browser={"browser": "chrome", "platform": "windows", "mobile": False}
                    )
                except Exception:
                    s = None
                if s is None:
                    s = requests.Session()
                
                s.headers.update({
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    "DNT": "1",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                })
                try:
                    s.get(BASE + "/", headers={"Referer": BASE + "/"}, timeout=15)
                except Exception:
                    pass
                return s
            
            # JAVGURU 전용 세션 생성 (cloudscraper 우선)
            javguru_session = create_http_client()
            
            # 자산 필터
            ASSET_SEG_RE = re.compile(
                r"(?:^|/)(?:logo|favicon|sprite|icons?|ads?|banners?|static|themes|emoji|svg)(?:/|$)",
                re.I
            )
            
            def is_probably_asset(u: str) -> bool:
                path = urlparse(u).path
                return bool(ASSET_SEG_RE.search(path))
            
            def compile_keyword_strict(keyword: str):
                """문자+숫자 정확 일치, 문자/숫자 사이 '-' 옵션."""
                m = re.match(r"^\s*([A-Za-z]+)\s*-?\s*(\d+)\s*$", keyword.strip())
                if not m:
                    k = keyword.strip()
                    k = re.escape(k).replace(r"\-", "-?")
                    return re.compile(rf"(?<![A-Za-z0-9]){k}(?![A-Za-z0-9])", re.I)
                prefix, num = m.groups()
                return re.compile(
                    rf"(?<![A-Za-z0-9]){re.escape(prefix)}-?{re.escape(num)}(?![A-Za-z0-9])",
                    re.I
                )
            
            def ext_from_content_type(ct: str) -> str:
                ct = (ct or "").lower()
                if "jpeg" in ct:
                    return ".jpg"
                if "png" in ct:
                    return ".png"
                if "webp" in ct:
                    return ".webp"
                if "gif" in ct:
                    return ".gif"
                if "bmp" in ct:
                    return ".bmp"
                if "avif" in ct:
                    return ".avif"
                return ".jpg"
            
            def head_or_small_get(url: str, referer: str) -> dict:
                """이미지 여부/크기 검사: HEAD → 실패 시 작은 GET"""
                headers = {"User-Agent": UA, "Referer": referer}
                try:
                    r = javguru_session.head(url, headers=headers, allow_redirects=True, timeout=15)
                    ct = (r.headers.get("content-type") or "").lower()
                    cl = r.headers.get("content-length")
                    size = int(cl) if cl and cl.isdigit() else None
                    return {"ok": (ct.startswith("image/") if ct else False), "final_url": r.url, "ct": ct, "size": size}
                except Exception:
                    pass
                try:
                    with javguru_session.get(url, headers=headers, stream=True, allow_redirects=True, timeout=25) as g:
                        ct = (g.headers.get("content-type") or "").lower()
                        cl = g.headers.get("content-length")
                        size = int(cl) if cl and cl.isdigit() else None
                        return {"ok": (ct.startswith("image/") if ct else False), "final_url": g.url, "ct": ct, "size": size}
                except Exception:
                    return {"ok": False, "final_url": url, "ct": "", "size": None}
            
            def find_first_card_and_title_from_search(soup: BeautifulSoup):
                """검색 결과에서 첫 번째 카드(article)와 제목 텍스트 추출"""
                for art in soup.select("article"):
                    a = art.select_one("h2 a, h1 a, .entry-title a, a")
                    if not a:
                        continue
                    title_txt = (a.get_text(" ", strip=True) or "").strip()
                    if len(title_txt) >= 3:
                        return art, title_txt
                return None, None
            
            def collect_card_images(card_node, base_url: str) -> List[dict]:
                """카드 내부 썸네일/커버 이미지 수집"""
                cands: List[dict] = []
                
                def add(u: Optional[str], how: str):
                    if not u:
                        return
                    if u.startswith("//"):
                        u = "https:" + u
                    cands.append({"url": urljoin_parse(base_url, u), "how": how})
                
                if getattr(card_node, "select", None):
                    for img in card_node.select("img"):
                        add(img.get("src"), "img.src")
                        add(img.get("data-src"), "img.data-src")
                        add(img.get("data-original"), "img.data-original")
                        add(img.get("data-lazy-src"), "img.data-lazy-src")
                        ss = img.get("srcset") or img.get("data-lazy-srcset")
                        if ss:
                            parts = [p.strip() for p in ss.split(",") if p.strip()]
                            for p in reversed(parts):  # 큰 해상도 우선
                                add(p.split()[0], "img.srcset")
                    
                    for cov in card_node.select(".post-thumbnail, .thumb, .cover, .image"):
                        style = cov.get("style", "")
                        m = re.search(r"url\((['\"]?)(.+?)\1\)", style)
                        if m:
                            add(m.group(2), "bg-style")
                
                # 중복 제거
                uniq, seen = [], set()
                for c in cands:
                    if c["url"] not in seen:
                        uniq.append(c)
                        seen.add(c["url"])
                return uniq
            
            def find_first_post_via_rest(keyword: str) -> tuple:
                """WP REST 검색: /wp-json/wp/v2/search?search=<keyword>"""
                api = f"{BASE}/wp-json/wp/v2/search?{urlencode({'search': keyword, 'per_page': 10})}"
                try:
                    r = javguru_session.get(api, headers={"Referer": BASE + "/"}, timeout=20)
                    if r and r.status_code == 200:
                        data = r.json()
                        for obj in data:
                            link = obj.get("url") or obj.get("link")
                            title = obj.get("title") or obj.get("title_plain") or ""
                            if link and title:
                                title_text = BeautifulSoup(str(title), "html.parser").get_text(" ", strip=True)
                                return link, title_text
                except Exception:
                    pass
                return None, None
            
            def find_first_post_via_rss(keyword: str) -> tuple:
                """RSS 검색: /?s=<keyword>&feed=rss2"""
                feed = f"{BASE}/?{urlencode({'s': keyword, 'feed': 'rss2'})}"
                try:
                    r = javguru_session.get(feed, headers={"Referer": BASE + "/"}, timeout=20)
                    if r and r.status_code == 200:
                        soup = BeautifulSoup(r.text, "xml")
                        item = soup.find("item")
                        if item:
                            link = item.findtext("link")
                            title = item.findtext("title")
                            return link, title
                except Exception:
                    pass
                return None, None
            
            def collect_post_cover_images(post_html: str, base_url: str) -> List[dict]:
                """포스트 페이지에서 대표 이미지 후보 수집"""
                soup = BeautifulSoup(post_html, "lxml")
                cands: List[dict] = []
                
                def add(u: Optional[str], how: str):
                    if not u:
                        return
                    if u.startswith("//"):
                        u = "https:" + u
                    cands.append({"url": urljoin_parse(base_url, u), "how": how})
                
                # 1) 대표 이미지
                for sel in ["img.wp-post-image", ".post-thumbnail img"]:
                    tag = soup.select_one(sel)
                    if tag:
                        add(tag.get("src"), f"{sel}.src")
                        add(tag.get("data-src"), f"{sel}.data-src")
                
                # 2) 본문 첫 이미지
                first_img = soup.select_one(".entry-content img, article img")
                if first_img:
                    add(first_img.get("src"), "entry-first-img.src")
                    add(first_img.get("data-src"), "entry-first-img.data-src")
                
                # 3) OG/Twitter
                og = soup.find("meta", property="og:image")
                if og and og.get("content"):
                    add(og.get("content"), "meta.og:image")
                
                tw = soup.find("meta", attrs={"name": "twitter:image"})
                if tw and tw.get("content"):
                    add(tw.get("content"), "meta.twitter:image")
                
                # 중복 제거
                uniq, seen = [], set()
                for c in cands:
                    if c["url"] and c["url"] not in seen:
                        uniq.append(c)
                        seen.add(c["url"])
                return uniq
            
            # 1) 검색 페이지 시도
            search_url = f"{BASE}/?{urlencode({'s': keyword})}"
            headers = {
                "Referer": BASE + "/"
            }
            
            # 403 재시도 로직: 최대 3회 재시도 (서버 접속부터 다시)
            max_retries = 3
            response = None
            for retry in range(max_retries):
                try:
                    response = javguru_session.get(search_url, headers=headers, timeout=25, allow_redirects=True)
                except Exception as e:
                    if retry < max_retries - 1:
                        time.sleep(1.2)  # 재시도 전 대기
                        continue
                    return []
                
                if not response:
                    if retry < max_retries - 1:
                        time.sleep(1.2)  # 재시도 전 대기
                        continue
                    return []
                
                # HTTP 403이면 카운트 증가 및 재시도 (서버 접속부터 다시)
                if response.status_code == 403:
                    self.javguru_403_count += 1
                    
                    if self.javguru_403_count >= 50:
                        if not self.javguru_blocked:
                            self.javguru_blocked = True
                            print(f"[ImageFinder] JAVGURU 서버 연결 안됨: 50번 연속 403 응답으로 인해 JAVGURU 검색 비활성화")
                        return []
                    
                    if retry < max_retries - 1:
                        # 세션 쿠키 초기화하고 처음부터 다시 시도
                        time.sleep(1.2)  # 403 발생 시 대기
                        javguru_session.cookies.clear()  # 쿠키 초기화
                        
                        # 홈페이지 워밍업 (처음부터)
                        try:
                            javguru_session.get(BASE + "/", headers={"Referer": BASE + "/"}, timeout=25)
                        except Exception:
                            pass
                        
                        continue  # 재시도
                    else:
                        return []  # 재시도 실패
                
                # 200 응답이면 403 카운트 리셋
                if response.status_code == 200:
                    if self.javguru_403_count > 0:
                        self.javguru_403_count = 0  # 성공 시 403 카운트 리셋
                    break  # 성공, 루프 종료
                
                # 403이 아닌 다른 오류
                if retry < max_retries - 1:
                    time.sleep(1.2)  # 재시도 전 대기
                    continue
                else:
                    return []
            
            if response and response.status_code == 200 and len(response.text) > 500:
                soup = BeautifulSoup(response.text, "lxml")
                card, title = find_first_card_and_title_from_search(soup)
                
                if card and title:
                    kw_re = compile_keyword_strict(keyword)
                    if kw_re.search(title):
                        imgs = collect_card_images(card, BASE)
                        if imgs:
                            # 이미지 검증 및 필터링
                            results = []
                            for item in imgs[:10]:  # 최대 10개만 검증
                                u = item["url"]
                                
                                # 자산 제외
                                if is_probably_asset(u):
                                    continue
                                
                                # GIF 파일 제외
                                u_lower = u.lower()
                                is_gif = (
                                    u_lower.endswith('.gif') or
                                    u_lower.endswith('.gif?') or
                                    u_lower.endswith('.gif&') or
                                    u_lower.endswith('.gif#') or
                                    u_lower.endswith('.gif/') or
                                    '.gif?' in u_lower or
                                    '.gif&' in u_lower or
                                    '.gif#' in u_lower or
                                    '.gif/' in u_lower or
                                    re.search(r'\.gif[\?#&/]', u_lower) is not None
                                )
                                if is_gif:
                                    continue
                                
                                # 이미지 검증
                                probe = head_or_small_get(u, search_url)
                                size_ok = (probe["size"] is None) or (probe["size"] >= MIN_BYTES)
                                if not probe["ok"] and pathlib.Path(urlparse(u).path).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif"}:
                                    continue
                                if not size_ok:
                                    continue
                                
                                # GIF content-type 제외
                                if probe["ct"] and "gif" in probe["ct"].lower():
                                    continue
                                
                                results.append(probe["final_url"] or u)
                                if len(results) >= 5:
                                    break
                            
                            if results:
                                return results
                        else:
                            # 이미지가 없으면 폴백으로 넘어감 (테스트 프로그램과 동일)
                            pass
                    else:
                        # 제목이 매칭되지 않으면 폴백으로 넘어감 (테스트 프로그램과 동일)
                        pass
                else:
                    # 카드나 제목이 없으면 폴백으로 넘어감 (테스트 프로그램과 동일)
                    pass
            
            # 2) 폴백: WP REST → RSS
            post_url, post_title = find_first_post_via_rest(keyword)
            if not post_url:
                post_url, post_title = find_first_post_via_rss(keyword)
            
            if not post_url or not post_title:
                return []
            
            # 제목 엄격 매칭
            kw_re = compile_keyword_strict(keyword)
            if not kw_re.search(post_title or ""):
                return []
            
            # 포스트 페이지에서 대표 이미지 수집
            try:
                pr = javguru_session.get(post_url, headers=headers, timeout=25)
                if not pr or pr.status_code != 200:
                    return []
            except Exception:
                return []
            
            post_imgs = collect_post_cover_images(pr.text, post_url)
            if not post_imgs:
                return []
            
            # 이미지 검증 및 필터링
            results = []
            for item in post_imgs[:10]:  # 최대 10개만 검증
                u = item["url"]
                
                # 자산 제외
                if is_probably_asset(u):
                    continue
                
                # GIF 파일 제외
                u_lower = u.lower()
                is_gif = (
                    u_lower.endswith('.gif') or
                    u_lower.endswith('.gif?') or
                    u_lower.endswith('.gif&') or
                    u_lower.endswith('.gif#') or
                    u_lower.endswith('.gif/') or
                    '.gif?' in u_lower or
                    '.gif&' in u_lower or
                    '.gif#' in u_lower or
                    '.gif/' in u_lower or
                    re.search(r'\.gif[\?#&/]', u_lower) is not None
                )
                if is_gif:
                    continue
                
                # 이미지 검증
                probe = head_or_small_get(u, post_url)
                size_ok = (probe["size"] is None) or (probe["size"] >= MIN_BYTES)
                if not probe["ok"] and pathlib.Path(urlparse(u).path).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif"}:
                    continue
                if not size_ok:
                    continue
                
                # GIF content-type 제외
                if probe["ct"] and "gif" in probe["ct"].lower():
                    continue
                
                results.append(probe["final_url"] or u)
                if len(results) >= 5:
                    break
            
            return results
            
        except Exception as e:
            print(f"[ImageFinder] JAV.GURU 검색 오류: {e}")
            return []
    
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

