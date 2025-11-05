"""JAVTorrent 스크래퍼"""
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import urljoin, quote
from .base_scraper import BaseScraper


class JAVTorrentScraper(BaseScraper):
    """JAVTorrent.re 전용 스크래퍼"""
    
    def __init__(self):
        super().__init__('https://www.javtorrent.re', 'JAVTorrent')
    
    def scrape_page(self, page: int = 1, sort_by: str = 'date', order: str = 'desc') -> List[Dict]:
        """페이지에서 토렌트 정보 스크래핑
        
        Args:
            page: 페이지 번호
            sort_by: 정렬 기준
            order: 정렬 순서
            
        Returns:
            토렌트 정보 딕셔너리 리스트
        """
        # JAVTorrent는 페이지 번호가 다르게 작동
        url = f"{self.base_url}/page/{page}" if page > 1 else self.base_url
        
        soup = self.get_page(url)
        if not soup:
            return []
        
        torrents = []
        
        # 토렌트 아이템 찾기
        items = soup.find_all('div', class_='post-item')
        
        for item in items:
            torrent_data = self.parse_torrent_item(item)
            if torrent_data:
                torrents.append(torrent_data)
        
        return torrents
    
    def parse_torrent_item(self, item) -> Optional[Dict]:
        """개별 토렌트 아이템 파싱
        
        Args:
            item: HTML div 요소
            
        Returns:
            토렌트 정보 딕셔너리 또는 None
        """
        try:
            # 제목 및 링크
            title_elem = item.find('h2', class_='post-title')
            if not title_elem:
                title_elem = item.find('a', class_='post-title')
            
            if not title_elem:
                return None
            
            title_link = title_elem.find('a') if title_elem.name != 'a' else title_elem
            if not title_link:
                return None
            
            title = title_link.get_text(strip=True)
            view_url = title_link.get('href', '')
            
            # source_id 추출
            source_id = view_url.split('/')[-1] if view_url else ''
            
            # Magnet 링크 찾기
            magnet_link = ''
            magnet_elem = item.find('a', href=lambda x: x and x.startswith('magnet:'))
            if magnet_elem:
                magnet_link = magnet_elem.get('href', '')
            
            # 메타 정보
            meta_info = item.find('div', class_='post-meta')
            upload_date = datetime.utcnow()
            
            if meta_info:
                date_elem = meta_info.find('time')
                if date_elem:
                    date_str = date_elem.get('datetime', '')
                    if date_str:
                        try:
                            upload_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        except:
                            pass
            
            # 크기 정보
            size = 'N/A'
            size_bytes = 0
            size_elem = item.find('span', text=lambda x: x and ('GB' in str(x) or 'MB' in str(x)))
            if size_elem:
                size = size_elem.get_text(strip=True)
                size_bytes = self.convert_size_to_bytes(size)
            
            # JAVTorrent는 주로 일본 AV (무검열 많음)
            country = 'JP'
            censored = False  # JAVTorrent는 주로 무검열
            
            # 제목에서 검열 여부 재확인
            if any(keyword in title.lower() for keyword in ['censored', '有码', '有碼']):
                censored = True
            
            # 장르 추측
            genres = self._detect_genres(title)
            
            # 썸네일
            thumbnail_url = ''
            img_elem = item.find('img')
            if img_elem:
                thumbnail_url = img_elem.get('src', '') or img_elem.get('data-src', '')
                if thumbnail_url and not thumbnail_url.startswith('http'):
                    thumbnail_url = urljoin(self.base_url, thumbnail_url)
            
            return {
                'title': title,
                'source_id': source_id,
                'source_site': 'javtorrent.re',
                'magnet_link': magnet_link,
                'torrent_link': '',
                'size': size,
                'size_bytes': size_bytes,
                'category': 'JAV',
                'censored': censored,
                'country': country,
                'seeders': 0,  # JAVTorrent는 시더 정보 제공 안 함
                'leechers': 0,
                'downloads': 0,
                'comments': 0,
                'upload_date': upload_date,
                'thumbnail_url': thumbnail_url,
                'snapshot_urls': '',
                'genres': genres
            }
        
        except Exception as e:
            print(f"[JAVTorrent] 토렌트 파싱 실패: {e}")
            return None
    
    def _detect_genres(self, title: str) -> List[str]:
        """제목에서 장르 감지"""
        genres = []
        title_lower = title.lower()
        
        genre_keywords = {
            'Blowjob': ['blowjob', 'bj', 'fellatio', 'oral', 'フェラ'],
            'Handjob': ['handjob', 'hj', '手コキ'],
            'Threesome': ['threesome', '3p', '3some'],
            'Creampie': ['creampie', 'nakadashi', '中出し', '中出'],
            'Anal': ['anal', 'アナル'],
            'BDSM': ['bdsm', 'bondage', 'sm'],
            'Cosplay': ['cosplay', 'cos', 'costume', 'コスプレ'],
            'Schoolgirl': ['schoolgirl', 'student', '学生', '制服', 'uniform', 'jk'],
            'MILF': ['milf', 'mature', '熟女', '人妻'],
            'Amateur': ['amateur', '素人'],
            'POV': ['pov', 'gonzo', '主観'],
            'Gangbang': ['gangbang', '輪姦', '乱交'],
            'Lesbian': ['lesbian', 'レズ'],
        }
        
        for genre, keywords in genre_keywords.items():
            if any(keyword in title_lower for keyword in keywords):
                genres.append(genre)
        
        if not genres:
            genres.append('Amateur')
        
        return genres

