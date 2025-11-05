"""Selenium 기반 JAVTorrent 스크래퍼"""
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
import time
import random
import re
from datetime import datetime
from .selenium_base import SeleniumBaseScraper


class SeleniumJAVTorrentScraper(SeleniumBaseScraper):
    """Selenium을 사용하여 JAVTorrent를 스크래핑"""
    
    def __init__(self):
        super().__init__('https://javtorrent.re', 'Selenium-JAVTorrent')
    
    def scrape_page(self, page: int = 1, sort_by: str = 'seeders', order: str = 'desc') -> List[Dict]:
        """페이지에서 토렌트 정보 스크래핑"""
        if page > 1:
            delay = random.uniform(0.5, 1.5)
            print(f"[{self.name}] 다음 페이지 요청 전 {delay:.1f}초 대기 중...")
            time.sleep(delay)
        
        # URL 구성
        url = f"{self.base_url}/ko/torrents/page/{page}"
        
        soup = self.get_page_selenium(url)
        if not soup:
            return []
        
        torrents = []
        
        # 토렌트 목록 찾기
        torrent_rows = soup.select('tr.default')
        
        print(f"[{self.name}] {len(torrent_rows)}개의 토렌트 항목 발견")
        
        for row in torrent_rows:
            try:
                # 제목 및 링크
                title_elem = row.select_one('a.text-truncate')
                if not title_elem:
                    continue
                
                title = title_elem.get_text(strip=True)
                detail_link = title_elem.get('href', '')
                
                # AV만 수집 (동인지/게임 제외)
                title_lower = title.lower()
                excluded = ['doujin', 'manga', 'game', 'comic', 'picture']
                if any(keyword in title_lower for keyword in excluded):
                    continue
                
                # 마그넷 링크 (여러 방법 시도)
                magnet_link = ''
                
                # 방법 1: 직접 magnet 링크
                magnet_elem = row.select_one('a[href^="magnet:"]')
                if magnet_elem:
                    magnet_link = magnet_elem.get('href', '')
                
                # 방법 2: 다운로드 버튼/아이콘에서
                if not magnet_link:
                    download_links = row.select('a')
                    for link in download_links:
                        href = link.get('href', '')
                        if href.startswith('magnet:'):
                            magnet_link = href
                            break
                
                # 방법 3: 상세 페이지 링크가 있으면 나중에 가져오기 (일단 스킵)
                if not magnet_link:
                    if detail_link:
                        # 상세 페이지에서 가져와야 함 (추후 구현)
                        print(f"[{self.name}] 마그넷 링크 없음, 스킵: {title[:50]}...")
                        continue
                    else:
                        continue
                
                # 크기
                size_elem = row.select_one('td.text-center:nth-of-type(3)')
                size = size_elem.get_text(strip=True) if size_elem else 'N/A'
                
                # 시더/리처
                seeder_elem = row.select_one('td:nth-of-type(5)')
                leecher_elem = row.select_one('td:nth-of-type(6)')
                
                seeders = int(seeder_elem.get_text(strip=True)) if seeder_elem else 0
                leechers = int(leecher_elem.get_text(strip=True)) if leecher_elem else 0
                
                # 다운로드 수 (추정)
                downloads = seeders * 2
                
                # 날짜
                date_elem = row.select_one('td:nth-of-type(4)')
                upload_date = datetime.now()
                if date_elem:
                    try:
                        date_text = date_elem.get_text(strip=True)
                        upload_date = datetime.strptime(date_text, '%Y-%m-%d')
                    except:
                        pass
                
                # 검열 판단
                censored = True
                uncensored_keywords = ['uncensored', '無修正', 'uncen', 'fc2']
                if any(keyword in title_lower for keyword in uncensored_keywords):
                    censored = False
                
                # Source ID
                source_id = f"javtorrent_{abs(hash(magnet_link)) % 10**8}"
                
                torrent_data = {
                    'title': title,
                    'source_id': source_id,
                    'source_site': 'javtorrent.re',
                    'magnet_link': magnet_link,
                    'torrent_link': detail_link,
                    'size': size,
                    'size_bytes': 0,
                    'category': 'JAV',
                    'censored': censored,
                    'country': 'JP',
                    'seeders': seeders,
                    'leechers': leechers,
                    'downloads': downloads,
                    'comments': 0,
                    'views': downloads * 5,
                    'upload_date': upload_date,
                    'thumbnail_url': '',
                    'snapshot_urls': '',
                    'genres': []
                }
                
                torrents.append(torrent_data)
                
            except Exception as e:
                print(f"[{self.name}] 항목 파싱 실패: {e}")
                continue
        
        print(f"[{self.name}] OK {len(torrents)}개 토렌트 추가")
        return torrents

