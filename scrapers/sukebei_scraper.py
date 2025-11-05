"""Sukebei.nyaa.si 스크래퍼"""
import re
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import urljoin
from .base_scraper import BaseScraper


class SukebeiScraper(BaseScraper):
    """Sukebei.nyaa.si 전용 스크래퍼"""
    
    def __init__(self):
        super().__init__('https://sukebei.nyaa.si', 'Sukebei')
    
    def scrape_page(self, page: int = 1, sort_by: str = 'id', order: str = 'desc') -> List[Dict]:
        """페이지에서 토렌트 정보 스크래핑
        
        Args:
            page: 페이지 번호
            sort_by: 정렬 기준 (id, comments, size, seeders, leechers, downloads)
            order: 정렬 순서 (asc, desc)
            
        Returns:
            토렌트 정보 딕셔너리 리스트
        """
        params = {
            'p': page,
            's': sort_by,
            'o': order
        }
        
        soup = self.get_page(self.base_url, params=params)
        if not soup:
            return []
        
        torrents = []
        
        # 테이블의 모든 행 찾기
        table = soup.find('table', class_='torrent-list')
        if not table:
            print("토렌트 테이블을 찾을 수 없습니다.")
            return []
        
        rows = table.find('tbody').find_all('tr')
        
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
            if len(columns) < 7:
                return None
            
            # 카테고리
            category_col = columns[0]
            category_link = category_col.find('a')
            category = category_link.get('title', '') if category_link else ''
            
            # 제목 및 링크
            name_col = columns[1]
            title_links = name_col.find_all('a')
            
            if not title_links:
                return None
            
            # 마지막 링크가 실제 제목
            title_link = title_links[-1]
            title = title_link.get_text(strip=True)
            view_url = title_link.get('href', '')
            source_id = view_url.split('/')[-1] if view_url else ''
            
            # Magnet 링크
            magnet_link = ''
            torrent_link = ''
            for link in name_col.find_all('a'):
                href = link.get('href', '')
                if href.startswith('magnet:'):
                    magnet_link = href
                elif href.endswith('.torrent'):
                    torrent_link = urljoin(self.base_url, href)
            
            # 토렌트 링크
            download_links = name_col.find_all('a', class_='')
            for link in download_links:
                href = link.get('href', '')
                if '/download/' in href:
                    torrent_link = urljoin(self.base_url, href)
            
            # 파일 크기
            size_col = columns[3]
            size = size_col.get_text(strip=True)
            size_bytes = self.convert_size_to_bytes(size)
            
            # 업로드 날짜
            date_col = columns[4]
            date_str = date_col.get('data-timestamp', '')
            upload_date = datetime.fromtimestamp(int(date_str)) if date_str else datetime.utcnow()
            
            # Seeders
            seeders_col = columns[5]
            seeders = int(seeders_col.get_text(strip=True) or 0)
            
            # Leechers
            leechers_col = columns[6]
            leechers = int(leechers_col.get_text(strip=True) or 0)
            
            # Downloads
            downloads_col = columns[7]
            downloads = int(downloads_col.get_text(strip=True) or 0)
            
            # 국가 및 검열 여부 추측
            country, censored = self._detect_country_and_censorship(title, category)
            
            # 장르 추측
            genres = self._detect_genres(title)
            
            return {
                'title': title,
                'source_id': source_id,
                'source_site': 'sukebei.nyaa.si',
                'magnet_link': magnet_link,
                'torrent_link': torrent_link,
                'size': size,
                'size_bytes': size_bytes,
                'category': category,
                'censored': censored,
                'country': country,
                'seeders': seeders,
                'leechers': leechers,
                'downloads': downloads,
                'comments': 0,  # 코멘트는 상세 페이지에서만 확인 가능
                'views': downloads * 5,  # 추정 조회수 (다운로드의 5배)
                'upload_date': upload_date,
                'thumbnail_url': '',  # 기본적으로 썸네일 없음
                'snapshot_urls': '',
                'genres': genres
            }
        
        except Exception as e:
            print(f"토렌트 파싱 실패: {e}")
            return None
    
    def _detect_country_and_censorship(self, title: str, category: str) -> tuple[str, bool]:
        """제목과 카테고리에서 국가와 검열 여부 추측
        
        Args:
            title: 토렌트 제목
            category: 카테고리
            
        Returns:
            (국가 코드, 검열 여부) 튜플
        """
        title_upper = title.upper()
        
        # 국가 감지
        country = 'OTHER'
        if any(indicator in title for indicator in ['中文', '国产', '麻豆', '91', 'chinese']):
            country = 'CN'
        elif any(indicator in title for indicator in ['FC2', 'HEYZO', 'CARIB', 'TOKYO', 'HUNTA', 'AVOP', 'SIRO', 'SSNI', 'ABP', 'IPX', 'MIDE', 'PRED', 'STARS']):
            country = 'JP'
        elif any(indicator in title for indicator in ['한국', 'KOREAN', 'BJ', '국산']):
            country = 'KR'
        elif any(indicator in title for indicator in ['BRAZZERS', 'BANGBROS', 'NAUGHTYAMERICA', 'REALITYKINGS']):
            country = 'US'
        elif 'THAI' in title_upper:
            country = 'TH'
        
        # 검열 여부 감지 (일본은 기본 검열, Uncensored 명시된 경우 제외)
        censored = True
        if country == 'JP':
            # 무수정 키워드
            uncensored_keywords = ['uncensored', 'uncen', '無修正', '无码', '无修正', 'reducing mosaic', 'fc2', 'heyzo', 'carib', 'caribbean']
            if any(keyword in title.lower() for keyword in uncensored_keywords):
                censored = False
        else:
            # 일본 외 국가는 기본적으로 무검열
            censored = False
        
        return country, censored
    
    def _detect_genres(self, title: str) -> List[str]:
        """제목에서 장르 감지
        
        Args:
            title: 토렌트 제목
            
        Returns:
            장르 이름 리스트
        """
        genres = []
        title_lower = title.lower()
        
        # 장르 키워드 매핑
        genre_keywords = {
            'Blowjob': ['blowjob', 'bj', 'fellatio', 'oral', 'フェラ', '口交'],
            'Handjob': ['handjob', 'hj', '手コキ', '手交'],
            'Threesome': ['threesome', '3p', '3some', '3p', '三人'],
            'Creampie': ['creampie', 'nakadashi', '中出し', '中出', '内射'],
            'Anal': ['anal', 'アナル', '肛交', '菊花'],
            'BDSM': ['bdsm', 'bondage', 'sm'],
            'Cosplay': ['cosplay', 'cos', 'costume', 'コスプレ', 'コス'],
            'Schoolgirl': ['schoolgirl', 'student', '女学生', '学生', '制服', 'uniform'],
            'MILF': ['milf', 'mature', '熟女', '人妻', '美少妇'],
            'Amateur': ['amateur', '素人', 'fc2', '自拍'],
            'POV': ['pov', 'gonzo', '主観'],
            'Gangbang': ['gangbang', '輪姦', '乱交', '群p'],
            'Lesbian': ['lesbian', 'レズ', '女同', '蕾丝'],
            'Masturbation': ['masturbation', 'solo', '自慰', 'オナニー'],
            'Toy': ['toy', 'vibrator', 'dildo', '玩具', 'バイブ', '道具'],
            'Squirting': ['squirting', 'squirt', '潮吹', '喷水'],
            'Bukkake': ['bukkake', 'gokkun', 'ぶっかけ', '颜射'],
        }
        
        for genre, keywords in genre_keywords.items():
            if any(keyword in title_lower for keyword in keywords):
                genres.append(genre)
        
        # 기본 장르 (아무것도 없으면)
        if not genres:
            genres.append('Amateur')
        
        return genres
    
    def get_torrent_details(self, source_id: str) -> Optional[Dict]:
        """토렌트 상세 정보 조회 (썸네일, 스냅샷 등)
        
        Args:
            source_id: 토렌트 ID
            
        Returns:
            상세 정보 딕셔너리 또는 None
        """
        url = f"{self.base_url}/view/{source_id}"
        soup = self.get_page(url)
        
        if not soup:
            return None
        
        details = {
            'thumbnail_url': '',
            'snapshot_urls': [],
            'description': ''
        }
        
        # 설명란에서 이미지 찾기
        description_div = soup.find('div', id='torrent-description')
        if description_div:
            details['description'] = description_div.get_text(strip=True)
            
            # 모든 이미지 찾기
            images = description_div.find_all('img')
            if images:
                details['thumbnail_url'] = images[0].get('src', '')
                details['snapshot_urls'] = [img.get('src', '') for img in images]
        
        return details

