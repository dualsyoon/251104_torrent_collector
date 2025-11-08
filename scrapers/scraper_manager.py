"""스크래퍼 매니저 - 다중 소스 관리 (모두 Selenium)"""
from typing import List, Dict, Optional

# Selenium 스크래퍼들
try:
    from .selenium_scraper import SeleniumSukebeiScraper
    from .selenium_javtorrent import SeleniumJAVTorrentScraper
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    print("⚠️ Selenium 스크래퍼를 사용하려면: pip install selenium webdriver-manager")


class ScraperManager:
    """여러 스크래퍼를 관리하는 매니저 클래스"""
    
    def __init__(self):
        """Selenium 기반 스크래퍼들 초기화"""
        self.scrapers = {}

        # Selenium 스크래퍼만 사용 (ISP 차단 우회)
        if SELENIUM_AVAILABLE:
            self.scrapers = {
                'selenium_sukebei': {
                    'name': 'Sukebei (Selenium)',
                    'scraper': None,  # 필요시 생성
                    'description': 'AV 전문 - ISP 차단 우회 ⭐',
                    'enabled': True,
                    'sort_by': 'id',
                    'order': 'desc'
                },
                'selenium_sukebei_seeders': {
                    'name': 'Sukebei (Seeders)',
                    'scraper': None,
                    'description': '시더수순 인기 정렬 (범위 다름)',
                    'enabled': True,
                    'sort_by': 'seeders',
                    'order': 'desc'
                },
                'selenium_sukebei_downloads': {
                    'name': 'Sukebei (Downloads)',
                    'scraper': None,
                    'description': '다운로드순 인기 정렬 (범위 다름)',
                    'enabled': True,
                    'sort_by': 'downloads',
                    'order': 'desc'
                }
            }
            print("[OK] Selenium 스크래퍼 사용 가능!")
        else:
            print("[X] Selenium이 설치되지 않았습니다.")
            print("    설치: pip install selenium webdriver-manager")
    
    def get_available_sources(self) -> Dict[str, Dict]:
        """사용 가능한 소스 목록 반환"""
        return {
            key: {
                'name': value['name'],
                'description': value['description'],
                'enabled': value['enabled']
            }
            for key, value in self.scrapers.items()
        }
    
    def get_scraper(self, source_key: str):
        """특정 소스의 스크래퍼 가져오기"""
        if source_key in self.scrapers:
            return self.scrapers[source_key]['scraper']
        return None
    
    def scrape_all_sources(self, pages: int = 3) -> Dict[str, List[Dict]]:
        """모든 활성화된 소스에서 토렌트 수집
        
        Args:
            pages: 각 소스에서 수집할 페이지 수
            
        Returns:
            {source_key: [torrents]} 형태의 딕셔너리
        """
        results = {}
        
        for key, source in self.scrapers.items():
            if not source['enabled']:
                continue
            
            print(f"\n[{source['name']}] 수집 시작...")
            source_torrents = []
            
            try:
                scraper = source['scraper']
                
                for page in range(1, pages + 1):
                    print(f"  페이지 {page}/{pages} 수집 중...")
                    
                    # TorrentKitty는 특별한 처리
                    if key == 'torrentkitty':
                        torrents = scraper.scrape_page(page=page, query='uncensored')
                    else:
                        sort_by = source.get('sort_by', 'seeders')
                        order = source.get('order', 'desc')
                        torrents = scraper.scrape_page(page=page, sort_by=sort_by, order=order)
                    
                    source_torrents.extend(torrents)
                    print(f"  ✓ {len(torrents)}개 수집")
                
                results[key] = source_torrents
                print(f"[{source['name']}] 총 {len(source_torrents)}개 수집 완료")
            
            except Exception as e:
                print(f"[{source['name']}] 오류 발생: {e}")
                results[key] = []
        
        return results
    
    def scrape_source(self, source_key: str, pages: int = 3) -> List[Dict]:
        """특정 소스에서만 토렌트 수집
        
        Args:
            source_key: 소스 키 (sukebei, javtorrent, torrentkitty, selenium_sukebei)
            pages: 수집할 페이지 수
            
        Returns:
            토렌트 리스트
        """
        if source_key not in self.scrapers:
            print(f"알 수 없는 소스: {source_key}")
            return []
        
        source = self.scrapers[source_key]
        if not source['enabled']:
            print(f"{source['name']}은(는) 비활성화되어 있습니다.")
            return []
        
        print(f"[{source['name']}] 수집 시작...")
        all_torrents = []
        
        try:
            # Selenium 스크래퍼 생성 (필요시)
            if source['scraper'] is None:
                if source_key == 'selenium_sukebei':
                    source['scraper'] = SeleniumSukebeiScraper()
                elif source_key == 'selenium_sukebei_seeders':
                    source['scraper'] = SeleniumSukebeiScraper()
                elif source_key == 'selenium_sukebei_downloads':
                    source['scraper'] = SeleniumSukebeiScraper()
                
            
            scraper = source['scraper']
            
            sort_by = source.get('sort_by', 'seeders')
            order = source.get('order', 'desc')
            for page in range(1, pages + 1):
                print(f"  페이지 {page}/{pages} 수집 중...")
                
                # 스크래핑 (AV만 수집)
                torrents = scraper.scrape_page(page=page, sort_by=sort_by, order=order)
                
                all_torrents.extend(torrents)
                print(f"  OK {len(torrents)}개 수집")
            
            # Selenium 스크래퍼는 재사용하도록 유지 (종료하지 않음)
            # scraper.close()는 프로그램 종료 시 자동으로 호출됨
            
            print(f"[{source['name']}] 총 {len(all_torrents)}개 수집 완료")
        
        except Exception as e:
            print(f"[{source['name']}] 오류 발생: {e}")
            # 에러 발생 시에도 Selenium 유지 (재사용)
        
        return all_torrents
    
    def enable_source(self, source_key: str, enabled: bool = True):
        """소스 활성화/비활성화"""
        if source_key in self.scrapers:
            self.scrapers[source_key]['enabled'] = enabled
    
    def search_query(self, query: str, source_key: Optional[str] = None, pages: int = 2) -> List[Dict]:
        """검색어로 토렌트 검색
        
        Args:
            query: 검색어
            source_key: 특정 소스만 검색 (None이면 TorrentKitty 사용)
            pages: 페이지 수
            
        Returns:
            토렌트 리스트
        """
        # 기본적으로 TorrentKitty 사용 (검색 엔진)
        if not source_key:
            source_key = 'torrentkitty'
        
        if source_key not in self.scrapers:
            return []
        
        source = self.scrapers[source_key]
        scraper = source['scraper']
        
        print(f"[{source['name']}] '{query}' 검색 중...")
        all_torrents = []
        
        try:
            for page in range(1, pages + 1):
                if hasattr(scraper, 'scrape_page'):
                    # TorrentKitty 스타일
                    torrents = scraper.scrape_page(page=page, query=query)
                    all_torrents.extend(torrents)
            
            print(f"[{source['name']}] 총 {len(all_torrents)}개 검색 완료")
        
        except Exception as e:
            print(f"[{source['name']}] 검색 오류: {e}")
        
        return all_torrents
    
    def scrape_source_smart(self, source_key: str, db, max_pages: int = 100, stop_on_duplicate: bool = True, stop_callback=None, progress_callback=None, query: str = None, db_writer=None) -> List[Dict]:
        """스마트 스크래핑: 페이지별로 중복 체크하여 중복 발견 시 중단
        
        Args:
            source_key: 소스 키
            db: Database 객체 (기존 데이터 확인용)
            max_pages: 최대 스크래핑할 페이지 수 (기본값: 100)
            stop_on_duplicate: 중복 발견 시 중단 여부 (기본값: True)
            stop_callback: 중단 여부를 확인하는 콜백 함수 (호출 시 True 반환하면 중단)
            progress_callback: 진행률 콜백 함수 (현재 페이지, 최대 페이지, 메시지)
            
        Returns:
            토렌트 리스트
        """
        if source_key not in self.scrapers:
            print(f"알 수 없는 소스: {source_key}")
            return []
        
        source = self.scrapers[source_key]
        if not source['enabled']:
            print(f"{source['name']}은(는) 비활성화되어 있습니다.")
            return []
        
        print(f"[{source['name']}] 스마트 스크래핑 시작...")
        all_torrents = []
        
        try:
            # Selenium 스크래퍼 생성 (필요시)
            if source['scraper'] is None:
                if source_key == 'selenium_sukebei':
                    source['scraper'] = SeleniumSukebeiScraper()
                elif source_key == 'selenium_sukebei_seeders':
                    source['scraper'] = SeleniumSukebeiScraper()
                elif source_key == 'selenium_sukebei_downloads':
                    source['scraper'] = SeleniumSukebeiScraper()
                
            
            scraper = source['scraper']
            
            # DB 세션 생성
            session = db.get_session()
            try:
                # 첫 페이지를 먼저 스크래핑하여 source_site 확인
                sort_by = source.get('sort_by', 'seeders')
                order = source.get('order', 'desc')
                # 검색어: 함수 파라미터가 우선, 없으면 소스 설정에서 가져오기
                search_query = query if query is not None else source.get('query', None)
                category = source.get('category', None)  # 카테고리
                test_page = scraper.scrape_page(page=1, sort_by=sort_by, order=order, query=search_query, category=category)
                if not test_page:
                    print(f"[{source['name']}] 첫 페이지에 데이터 없음")
                    return []
                
                # 실제 source_site 가져오기 (스크래퍼에서 설정한 값)
                source_site = test_page[0].get('source_site', source_key)
                
                # 기존 데이터 확인
                has_existing = db.has_torrents_from_source(session, source_site)
                
                if has_existing and stop_on_duplicate:
                    # 기존 source_id 집합 가져오기 (중복 체크용)
                    existing_ids = db.get_existing_source_ids(session, source_site)
                    print(f"[{source['name']}] 기존 데이터 {len(existing_ids)}개 발견 - 중복 발견 시 중단")
                else:
                    # 첫 실행: 모든 페이지 수집
                    existing_ids = set()
                    print(f"[{source['name']}] 첫 실행 - 전체 수집 모드")
                
                # 첫 페이지 데이터 처리 (이미 스크래핑했으므로)
                page_duplicates = 0
                page_new = 0
                
                # 진행률 보고
                if progress_callback:
                    progress_callback(1, max_pages, f"페이지 1/{max_pages} 처리 중...")
                
                # 중단 체크
                if stop_callback and stop_callback():
                    print(f"[{source['name']}] 사용자에 의해 중단됨")
                    return all_torrents
                
                for torrent_data in test_page:
                    source_id = torrent_data.get('source_id')
                    if source_id and source_id in existing_ids:
                        # 중복이지만 필드 업데이트를 위해 항목 추가 (마킹)
                        page_duplicates += 1
                        torrent_data['_is_update'] = True  # 업데이트 마킹
                        all_torrents.append(torrent_data)
                    else:
                        page_new += 1
                        all_torrents.append(torrent_data)
                        # 새로 발견한 ID 추가
                        if source_id:
                            existing_ids.add(source_id)
                
                print(f"  ✓ 페이지 1: 신규 {page_new}개, 업데이트 {page_duplicates}개")
                
                # db_writer가 있으면 첫 페이지도 실시간 저장
                if db_writer:
                    batch_torrents = [t for t in test_page if t.get('source_id')]
                    if batch_torrents:
                        db_writer.batch_add_torrents(batch_torrents)
                
                # 페이지별로 스크래핑 (2페이지부터)
                for page in range(2, max_pages + 1):
                    # 진행률 보고
                    if progress_callback:
                        progress_callback(page, max_pages, f"페이지 {page}/{max_pages} 수집 중...")
                    
                    # 중단 체크
                    if stop_callback and stop_callback():
                        print(f"[{source['name']}] 사용자에 의해 중단됨 (페이지 {page}에서)")
                        break
                    
                    print(f"  페이지 {page} 수집 중...")
                    
                    # 스크래핑
                    page_torrents = scraper.scrape_page(page=page, sort_by=sort_by, order=order, query=search_query, category=category)
                    
                    if not page_torrents:
                        print(f"  페이지 {page}에 데이터 없음 - 중단")
                        break
                    
                    # 중단 체크
                    if stop_callback and stop_callback():
                        print(f"[{source['name']}] 사용자에 의해 중단됨 (페이지 {page} 처리 중)")
                        break
                    
                    # 중복 체크 및 업데이트
                    page_duplicates = 0
                    page_new = 0
                    
                    for torrent_data in page_torrents:
                        # 중단 체크 (각 토렌트 처리 중에도)
                        if stop_callback and stop_callback():
                            break
                        source_id = torrent_data.get('source_id')
                        if source_id and source_id in existing_ids:
                            # 중복이지만 필드 업데이트를 위해 항목 추가 (마킹)
                            page_duplicates += 1
                            torrent_data['_is_update'] = True  # 업데이트 마킹
                            all_torrents.append(torrent_data)
                        else:
                            page_new += 1
                            all_torrents.append(torrent_data)
                            # 새로 발견한 ID 추가
                            if source_id:
                                existing_ids.add(source_id)
                    
                    print(f"  ✓ 페이지 {page}: 신규 {page_new}개, 업데이트 {page_duplicates}개")
                    
                    # db_writer가 있으면 실시간으로 큐에 추가 (비동기 저장)
                    if db_writer:
                        batch_torrents = [t for t in page_torrents if t.get('source_id')]
                        if batch_torrents:
                            db_writer.batch_add_torrents(batch_torrents)
                    
                    # 중복 체크 로직 제거 - 모든 페이지를 계속 읽음
                    # 시드/다운로드 수 등의 필드가 업데이트될 수 있으므로
                
                print(f"[{source['name']}] 총 {len(all_torrents)}개 수집 완료")
                
                # db_writer를 사용한 경우, 큐에 남은 작업이 완료될 때까지 대기
                if db_writer:
                    queue_size = db_writer.queue.qsize()
                    print(f"[{source['name']}] DB 저장 큐 완료 대기 중... (큐 크기: {queue_size})")
                    if queue_size > 0:
                        db_writer.queue.join()  # 모든 작업이 완료될 때까지 대기
                        print(f"[{source['name']}] DB 저장 완료 (큐 처리 완료)")
                    else:
                        print(f"[{source['name']}] ⚠️ DB 저장 큐가 비어있습니다. 작업이 큐에 추가되지 않았을 수 있습니다.")
                else:
                    print(f"[{source['name']}] ⚠️ db_writer가 None입니다!")
            
            finally:
                session.close()
            
            # Selenium 스크래퍼는 사용 후 종료
            if scraper and 'selenium' in source_key:
                scraper.close()
                source['scraper'] = None
        
        except Exception as e:
            print(f"[{source['name']}] 오류 발생: {e}")
            # 에러 발생 시에도 Selenium 유지 (재사용)
        
        return all_torrents

