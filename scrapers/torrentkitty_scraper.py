"""TorrentKitty 스크래퍼"""
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import urljoin, quote
from .base_scraper import BaseScraper


class TorrentKittyScraper(BaseScraper):
    """TorrentKitty.tv 전용 스크래퍼 (검색 엔진)"""
    
    def __init__(self):
        super().__init__('https://www.torrentkitty.tv', 'TorrentKitty')
    
    def scrape_page(self, page: int = 1, query: str = '', sort_by: str = 'rel', order: str = 'desc') -> List[Dict]:
        """페이지에서 토렌트 정보 스크래핑
        
        Args:
            page: 페이지 번호
            query: 검색어 (비어있으면 기본 검색)
            sort_by: 정렬 기준
            order: 정렬 순서
            
        Returns:
            토렌트 정보 딕셔너리 리스트
        """
        # 기본 검색어 (성인 콘텐츠)
        if not query:
            query = 'uncensored'  # 무검열 위주로 검색
        
        # 검색 URL
        search_url = f"{self.base_url}/search/{quote(query)}/{page}"
        
        soup = self.get_page(search_url)
        if not soup:
            return []
        
        torrents = []
        
        # 토렌트 테이블 찾기
        table = soup.find('table', id='archiveResult')
        if not table:
            return []
        
        rows = table.find_all('tr')[1:]  # 헤더 제외
        
        for row in rows:
            torrent_data = self.parse_torrent_item(row)
            if torrent_data:
                torrents.append(torrent_data)
        
        return torrents
    
    def parse_torrent_item(self, row) -> Optional[Dict]:
        """개별 토렌트 행 파싱
        
        Args:
            row: HTML tr 요소
            
        Returns:
            토렌트 정보 딕셔너리 또는 None
        """
        try:
            columns = row.find_all('td')
            if len(columns) < 4:
                return None
            
            # 제목 및 링크
            title_col = columns[0]
            title_link = title_col.find('a', class_='name')
            if not title_link:
                return None
            
            title = title_link.get_text(strip=True)
            view_url = title_link.get('href', '')
            
            # Magnet 링크
            magnet_link = ''
            magnet_elem = title_col.find('a', href=lambda x: x and x.startswith('magnet:'))
            if magnet_elem:
                magnet_link = magnet_elem.get('href', '')
            
            # source_id
            source_id = view_url.split('/')[-1] if view_url else title[:20]
            
            # 크기
            size_col = columns[1]
            size = size_col.get_text(strip=True)
            size_bytes = self.convert_size_to_bytes(size)
            
            # 날짜
            date_col = columns[2]
            date_str = date_col.get_text(strip=True)
            upload_date = self._parse_date(date_str)
            
            # 시더 수
            seeders = 0
            if len(columns) > 3:
                try:
                    seeders = int(columns[3].get_text(strip=True) or 0)
                except:
                    pass
            
            # 국가 및 검열 여부 추측
            country, censored = self._detect_country_and_censorship(title)
            
            # 장르 추측
            genres = self._detect_genres(title)
            
            return {
                'title': title,
                'source_id': source_id,
                'source_site': 'torrentkitty.tv',
                'magnet_link': magnet_link,
                'torrent_link': '',
                'size': size,
                'size_bytes': size_bytes,
                'category': 'Adult',
                'censored': censored,
                'country': country,
                'seeders': seeders,
                'leechers': 0,
                'downloads': 0,
                'comments': 0,
                'upload_date': upload_date,
                'thumbnail_url': '',
                'snapshot_urls': '',
                'genres': genres
            }
        
        except Exception as e:
            print(f"[TorrentKitty] 토렌트 파싱 실패: {e}")
            return None
    
    def _parse_date(self, date_str: str) -> datetime:
        """날짜 문자열 파싱"""
        try:
            # "2024-01-15" 형식
            if '-' in date_str and len(date_str) >= 10:
                return datetime.strptime(date_str[:10], '%Y-%m-%d')
        except:
            pass
        
        return datetime.utcnow()
    
    def _detect_country_and_censorship(self, title: str) -> tuple[str, bool]:
        """제목에서 국가와 검열 여부 추측"""
        title_upper = title.upper()
        
        country = 'OTHER'
        if any(indicator in title for indicator in ['中文', '国产', '麻豆', '91']):
            country = 'CN'
        elif any(indicator in title for indicator in ['FC2', 'HEYZO', 'CARIB', 'SIRO', 'SSNI', 'ABP', 'IPX']):
            country = 'JP'
        elif any(indicator in title for indicator in ['한국', 'KOREAN', 'BJ']):
            country = 'KR'
        elif any(indicator in title for indicator in ['BRAZZERS', 'BANGBROS', 'NAUGHTYAMERICA']):
            country = 'US'
        
        # 검열 여부
        censored = True
        if country == 'JP':
            uncensored_keywords = ['uncensored', 'uncen', '無修正', '无码', 'fc2', 'heyzo', 'carib']
            if any(keyword in title.lower() for keyword in uncensored_keywords):
                censored = False
        else:
            censored = False
        
        return country, censored
    
    def _detect_genres(self, title: str) -> List[str]:
        """제목에서 장르 감지"""
        genres = []
        title_lower = title.lower()
        
        genre_keywords = {
            'Blowjob': ['blowjob', 'bj', 'fellatio', 'oral'],
            'Handjob': ['handjob', 'hj'],
            'Threesome': ['threesome', '3p', '3some'],
            'Creampie': ['creampie', 'nakadashi', '中出'],
            'Anal': ['anal'],
            'BDSM': ['bdsm', 'bondage', 'sm'],
            'Cosplay': ['cosplay', 'cos', 'costume'],
            'Schoolgirl': ['schoolgirl', 'student', 'uniform'],
            'MILF': ['milf', 'mature'],
            'Amateur': ['amateur'],
            'POV': ['pov', 'gonzo'],
            'Gangbang': ['gangbang'],
            'Lesbian': ['lesbian'],
        }
        
        for genre, keywords in genre_keywords.items():
            if any(keyword in title_lower for keyword in keywords):
                genres.append(genre)
        
        if not genres:
            genres.append('Amateur')
        
        return genres

