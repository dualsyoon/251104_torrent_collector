"""Sukebei.nyaa.si 웹 스크래퍼"""
import re
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import time


class SukebeiScraper:
    """Sukebei.nyaa.si 스크래퍼"""
    
    BASE_URL = 'https://sukebei.nyaa.si'
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
    
    def scrape_page(self, page: int = 1, category: str = '', filter_option: str = '', sort: str = 'id', order: str = 'desc') -> List[Dict]:
        """페이지 스크래핑
        
        Args:
            page: 페이지 번호
            category: 카테고리 (예: 2_2 for Real Life - Videos)
            filter_option: 필터 (0=no filter, 1=no remakes, 2=trusted only)
            sort: 정렬 기준 (id, seeders, leechers, downloads, size, comments)
            order: 정렬 순서 (asc, desc)
        """
        try:
            params = {
                'p': page,
                's': sort,
                'o': order
            }
            
            if category:
                params['c'] = category
            
            if filter_option:
                params['f'] = filter_option
            
            url = f"{self.BASE_URL}/"
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'lxml')
            torrents = []
            
            # 토렌트 테이블 파싱
            table = soup.find('table', class_='torrent-list')
            if not table:
                return []
            
            rows = table.find('tbody').find_all('tr')
            
            for row in rows:
                try:
                    torrent = self._parse_row(row)
                    if torrent:
                        torrents.append(torrent)
                except Exception as e:
                    print(f"행 파싱 오류: {e}")
                    continue
            
            return torrents
            
        except Exception as e:
            print(f"페이지 스크래핑 실패: {e}")
            return []
    
    def _parse_row(self, row) -> Optional[Dict]:
        """테이블 행 파싱"""
        try:
            cols = row.find_all('td')
            if len(cols) < 8:
                return None
            
            # 카테고리
            category_tag = cols[0].find('a')
            category = category_tag.get('title', '') if category_tag else ''
            
            # 제목 및 링크
            title_col = cols[1]
            title_link = title_col.find('a', href=re.compile(r'/view/'))
            if not title_link:
                return None
            
            title = title_link.get('title', title_link.text.strip())
            view_url = self.BASE_URL + title_link['href']
            torrent_id = view_url.split('/')[-1]
            
            # Magnet 링크
            magnet_link = title_col.find('a', href=re.compile(r'magnet:'))
            magnet = magnet_link['href'] if magnet_link else ''
            
            # Torrent 파일 링크
            torrent_link = title_col.find('a', href=re.compile(r'/download/'))
            download_url = self.BASE_URL + torrent_link['href'] if torrent_link else ''
            
            # 댓글 수
            comments = cols[2].text.strip()
            
            # 파일 크기
            size = cols[3].text.strip()
            
            # 날짜
            date = cols[4].text.strip()
            
            # 시더
            seeders = int(cols[5].text.strip() or 0)
            
            # 리처
            leechers = int(cols[6].text.strip() or 0)
            
            # 다운로드 수
            downloads = int(cols[7].text.strip() or 0)
            
            # 상세 정보 가져오기
            details = self._get_details(view_url)
            
            return {
                'id': torrent_id,
                'title': title,
                'category': category,
                'view_url': view_url,
                'magnet': magnet,
                'download_url': download_url,
                'size': size,
                'date': date,
                'seeders': seeders,
                'leechers': leechers,
                'downloads': downloads,
                'comments': comments,
                'description': details.get('description', ''),
                'thumbnail': details.get('thumbnail', ''),
                'screenshots': details.get('screenshots', []),
                'censorship': self._detect_censorship(title, details.get('description', '')),
                'country': self._detect_country(title, details.get('description', '')),
                'genres': self._detect_genres(title, details.get('description', '')),
                'scraped_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            print(f"행 파싱 오류: {e}")
            return None
    
    def _get_details(self, view_url: str) -> Dict:
        """상세 페이지에서 정보 가져오기"""
        try:
            time.sleep(0.5)  # 요청 제한 준수
            response = self.session.get(view_url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'lxml')
            
            # 설명
            description_div = soup.find('div', id='torrent-description')
            description = description_div.text.strip() if description_div else ''
            
            # 이미지 찾기
            images = []
            if description_div:
                img_tags = description_div.find_all('img')
                for img in img_tags:
                    src = img.get('src', '')
                    if src:
                        images.append(src)
            
            # 썸네일과 스크린샷 분리
            thumbnail = images[0] if images else ''
            screenshots = images[1:] if len(images) > 1 else images
            
            return {
                'description': description,
                'thumbnail': thumbnail,
                'screenshots': screenshots
            }
            
        except Exception as e:
            print(f"상세 정보 가져오기 실패: {e}")
            return {'description': '', 'thumbnail': '', 'screenshots': []}
    
    def _detect_censorship(self, title: str, description: str) -> str:
        """검열 여부 감지"""
        text = (title + ' ' + description).lower()
        
        # Uncensored 키워드
        uncensored_keywords = ['uncensored', '無修正', '无码', '무수정', 'no mosaic']
        for keyword in uncensored_keywords:
            if keyword in text:
                return 'Uncensored'
        
        # Censored 키워드
        censored_keywords = ['censored', '有修正', '有码', '모자이크', 'mosaic']
        for keyword in censored_keywords:
            if keyword in text:
                return 'Censored'
        
        # 기본값: 일본 컨텐츠는 Censored, 나머지는 판단 불가
        if any(k in text for k in ['fc2', 'ppv', 'japanese', '日本', '일본']):
            return 'Censored'
        
        return 'Unknown'
    
    def _detect_country(self, title: str, description: str) -> str:
        """국가 감지"""
        text = (title + ' ' + description).lower()
        
        country_keywords = {
            'Japan': ['fc2', 'ppv', 'japanese', '日本', '일본', 'jav', 'av女優'],
            'China': ['chinese', '中國', '中国', '中文', '国产', '중국'],
            'Korea': ['korean', '韓国', '한국', 'k-', '국산'],
            'USA': ['american', 'usa', 'us ', 'brazzers', 'bangbros'],
            'Europe': ['european', 'french', 'german', 'italian', 'russian']
        }
        
        for country, keywords in country_keywords.items():
            for keyword in keywords:
                if keyword in text:
                    return country
        
        return 'Other'
    
    def _detect_genres(self, title: str, description: str) -> List[str]:
        """장르 감지"""
        text = (title + ' ' + description).lower()
        genres = []
        
        genre_keywords = {
            'Amateur': ['amateur', '素人', '아마추어'],
            'Anal': ['anal', 'アナル', '항문'],
            'Asian': ['asian', 'アジア', '아시아'],
            'BBW': ['bbw', 'chubby', 'plump'],
            'BDSM': ['bdsm', 'bondage', '緊縛', '속박'],
            'Big Tits': ['big tits', 'huge tits', '爆乳', '巨乳', '큰가슴'],
            'Blowjob': ['blowjob', 'bj', 'fellatio', 'フェラ', '블로우잡'],
            'Bukkake': ['bukkake', 'ぶっかけ', '부카케'],
            'Cosplay': ['cosplay', 'コスプレ', '코스프레'],
            'Creampie': ['creampie', 'nakadashi', '中出し', '크림파이'],
            'Cumshot': ['cumshot', 'cum shot', '射精', '사정'],
            'Facial': ['facial', '顔射', '페이셜'],
            'Fetish': ['fetish', 'フェチ', '페티쉬'],
            'Gangbang': ['gangbang', 'gang bang', '輪姦', '윤간'],
            'Handjob': ['handjob', 'hand job', '手コキ', '핸드잡'],
            'Hardcore': ['hardcore', 'ハードコア', '하드코어'],
            'Hentai': ['hentai', '変態', 'へんたい', '헨타이'],
            'Interracial': ['interracial', 'inter racial', '異人種間'],
            'Lesbian': ['lesbian', 'レズ', '레즈비언'],
            'MILF': ['milf', 'mature', '熟女', '밀프'],
            'Masturbation': ['masturbation', 'オナニー', '자위'],
            'Orgy': ['orgy', '乱交', '난교'],
            'POV': ['pov', 'point of view', '主観'],
            'Schoolgirl': ['schoolgirl', 'school girl', '女子校生', '女学生', '여학생'],
            'Softcore': ['softcore', 'ソフトコア', '소프트코어'],
            'Solo': ['solo', 'ソロ', '솔로'],
            'Squirting': ['squirt', '潮吹き', '스퀴트'],
            'Threesome': ['threesome', '3p', '3some'],
            'Toys': ['toy', 'dildo', 'vibrator', 'バイブ', '딜도'],
            'Uniform': ['uniform', '制服', '유니폼'],
        }
        
        for genre, keywords in genre_keywords.items():
            for keyword in keywords:
                if keyword in text:
                    genres.append(genre)
                    break
        
        return genres if genres else ['Other']
    
    def scrape_recent(self, days: int = 1, max_pages: int = 5) -> List[Dict]:
        """최근 토렌트 스크래핑"""
        all_torrents = []
        cutoff_date = datetime.now() - timedelta(days=days)
        
        for page in range(1, max_pages + 1):
            print(f"페이지 {page} 스크래핑 중...")
            torrents = self.scrape_page(page=page, sort='id', order='desc')
            
            if not torrents:
                break
            
            all_torrents.extend(torrents)
            time.sleep(1)  # 요청 제한 준수
        
        return all_torrents


# 싱글톤 인스턴스
scraper = SukebeiScraper()
