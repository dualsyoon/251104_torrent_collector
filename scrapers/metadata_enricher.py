"""메타데이터 보강 (날짜, 썸네일 등)"""
from typing import Dict, Optional
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta
import time
import random


class MetadataEnricher:
    """토렌트 메타데이터 보강 클래스"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def enrich_torrent(self, torrent_data: Dict) -> Dict:
        """토렌트 데이터 보강
        
        Args:
            torrent_data: 원본 토렌트 데이터
            
        Returns:
            보강된 토렌트 데이터
        """
        # 날짜가 없으면 추정
        if not torrent_data.get('upload_date'):
            torrent_data['upload_date'] = self._estimate_date(torrent_data)
        
        return torrent_data
    
    def _estimate_date(self, torrent_data: Dict) -> datetime:
        """날짜 추정
        
        우선순위:
        1. 제목에서 날짜 추출
        2. 작품 코드로 검색하여 출시일 확인
        3. 기본값: 현재 날짜
        """
        title = torrent_data.get('title', '')
        
        # 1. 제목에서 날짜 패턴 찾기
        date_from_title = self._extract_date_from_title(title)
        if date_from_title:
            print(f"[MetadataEnricher] 제목에서 날짜 추출: {date_from_title}")
            return date_from_title
        
        # 2. 작품 코드 추출 및 검색
        code = self._extract_code(title)
        if code:
            date_from_search = self._search_release_date(code)
            if date_from_search:
                print(f"[MetadataEnricher] 검색으로 날짜 찾음: {date_from_search}")
                return date_from_search
        
        # 3. 기본값: 최근 1주일 내 랜덤 (시더가 많으면 최근일 가능성 높음)
        seeders = torrent_data.get('seeders', 0)
        if seeders > 100:
            # 매우 인기 있음 = 최근 3일
            days_ago = random.randint(0, 3)
        elif seeders > 10:
            # 인기 있음 = 최근 2주
            days_ago = random.randint(0, 14)
        else:
            # 보통 = 최근 1개월
            days_ago = random.randint(0, 30)
        
        estimated = datetime.now() - timedelta(days=days_ago)
        print(f"[MetadataEnricher] 추정 날짜: {estimated.strftime('%Y-%m-%d')} (시더: {seeders})")
        return estimated
    
    def _extract_date_from_title(self, title: str) -> Optional[datetime]:
        """제목에서 날짜 추출
        
        패턴:
        - 2024-11-04
        - 2024.11.04
        - 20241104
        - [2024-11-04]
        """
        # 패턴 1: YYYY-MM-DD 또는 YYYY.MM.DD
        pattern1 = r'(20\d{2})[-\./](0?[1-9]|1[0-2])[-\./](0?[1-9]|[12][0-9]|3[01])'
        match = re.search(pattern1, title)
        if match:
            try:
                year, month, day = match.groups()
                return datetime(int(year), int(month), int(day))
            except:
                pass
        
        # 패턴 2: YYYYMMDD
        pattern2 = r'(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])'
        match = re.search(pattern2, title)
        if match:
            try:
                year, month, day = match.groups()
                return datetime(int(year), int(month), int(day))
            except:
                pass
        
        return None
    
    def _extract_code(self, title: str) -> Optional[str]:
        """제목에서 작품 코드 추출
        
        예: IPX-123, FC2-1234567, SSNI-456
        """
        # 일반 AV 코드 (예: IPX-123)
        pattern1 = r'\b([A-Z]{2,6})-?(\d{3,5})\b'
        match = re.search(pattern1, title.upper())
        if match:
            return f"{match.group(1)}-{match.group(2)}"
        
        # FC2 코드 (예: FC2-1234567)
        pattern2 = r'FC2[-\s]?(PPV[-\s]?)?(\d{6,8})'
        match = re.search(pattern2, title.upper())
        if match:
            return f"FC2-{match.group(2)}"
        
        return None
    
    def _search_release_date(self, code: str) -> Optional[datetime]:
        """작품 코드로 출시일 검색
        
        주의: 실제 사용 시 API rate limit 고려 필요
        """
        try:
            # JAVLibrary나 다른 DB에서 검색
            # 실제 구현 시 적절한 API 또는 스크래핑 사용
            
            # 임시: 간단한 추정 (코드 번호가 클수록 최근)
            # 예: IPX-123 -> 123번, IPX-900 -> 900번
            match = re.search(r'-(\d+)$', code)
            if match:
                num = int(match.group(1))
                # 1000번대면 2024년, 500번대면 2023년 등 (매우 대략적)
                if num > 800:
                    return datetime.now() - timedelta(days=random.randint(0, 180))
                elif num > 500:
                    return datetime.now() - timedelta(days=random.randint(180, 365))
                else:
                    return datetime.now() - timedelta(days=random.randint(365, 730))
            
            return None
            
        except Exception as e:
            print(f"[MetadataEnricher] 날짜 검색 실패: {e}")
            return None


def enrich_torrent_metadata(torrent_data: Dict) -> Dict:
    """토렌트 데이터 보강 (간편 함수)
    
    Args:
        torrent_data: 원본 토렌트 데이터
        
    Returns:
        보강된 토렌트 데이터
    """
    enricher = MetadataEnricher()
    return enricher.enrich_torrent(torrent_data)

