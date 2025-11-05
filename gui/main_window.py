"""메인 윈도우"""
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QProgressBar, QMessageBox, QStatusBar, QMenuBar, QMenu,
    QComboBox, QLabel, QLineEdit
)
from PySide6.QtCore import Qt, QThread, Signal, QSettings
from PySide6.QtGui import QAction
from .filter_panel import FilterPanel
from .torrent_list import TorrentListWidget
from database import Database
from database.db_writer import DBWriterThread
from scrapers import ScraperManager
from config import PAGE_SIZE, MAX_SCRAPE_PAGES, ENABLE_THUMBNAIL, MAX_CONSECUTIVE_DUPLICATES
from .settings_dialog import SettingsDialog


class ThumbnailUpdateThread(QThread):
    """썸네일 백그라운드 업데이트 스레드"""
    
    progress = Signal(int, str)  # (진행률, 메시지)
    finished = Signal(int)  # (업데이트된 수)
    error = Signal(str)
    thumbnail_updated = Signal(int, str)  # (torrent_id, thumbnail_url) - 개별 업데이트
    
    def __init__(self, db: Database, db_writer: DBWriterThread, priority_ids: list = None):
        super().__init__()
        self.db = db
        self.db_writer = db_writer
        self.priority_ids = priority_ids or []  # 현재 페이지 항목 ID 우선 (순서 유지를 위해 list 사용)
        self._stop_requested = False
        self._priority_lock = None  # 스레드 락 (run에서 초기화)
        self._torrents_to_process = []  # 처리할 항목 리스트
        self._current_index = 0  # 현재 처리 중인 인덱스
    
    def stop(self):
        """업데이트 중단 요청"""
        self._stop_requested = True
    
    def update_priority_ids(self, new_priority_ids: list):
        """페이지 변경 시 우선순위 동적 업데이트 (썸네일 없는 항목 ID만 전달받음)"""
        if not hasattr(self, '_priority_lock') or self._priority_lock is None:
            return
        
        import threading
        with self._priority_lock:
            # 새 우선순위 ID 추가 (순서 유지)
            self.priority_ids = new_priority_ids
            
            if not self.priority_ids:
                return
            
            # 이미 처리 중인 항목에서 새로운 우선순위 항목이 있는지 확인
            existing_ids = {t.id for t in self._torrents_to_process}
            existing_priority = [t for t in self._torrents_to_process if t.id in self.priority_ids]
            
            # DB에서 아직 큐에 없는 새 항목들만 가져오기
            new_ids = [id for id in self.priority_ids if id not in existing_ids]
            
            if new_ids:
                session = self.db.get_session()
                try:
                    from database.models import Torrent
                    
                    # 새 항목들만 쿼리 (이미 썸네일 없는 항목만 전달받았으므로 조건 불필요)
                    new_torrents = session.query(Torrent).filter(
                        Torrent.id.in_(new_ids)
                    ).all()
                    
                    # 전달받은 ID 순서대로 정렬 (self.priority_ids는 이미 순서가 있는 list)
                    new_torrents_sorted = sorted(
                        new_torrents,
                        key=lambda t: self.priority_ids.index(t.id) if t.id in self.priority_ids else 999999
                    )
                    
                    if new_torrents_sorted:
                        print(f"[썸네일] 새 항목 {len(new_torrents_sorted)}개 추가")
                    
                    # 아직 처리하지 않은 항목들과 함께 재정렬
                    if self._current_index < len(self._torrents_to_process):
                        remaining = self._torrents_to_process[self._current_index:]
                        
                        # 우선순위 항목과 나머지 항목 분리
                        priority_items = new_torrents_sorted.copy()  # 새 항목들
                        other_items = []
                        
                        for item in remaining:
                            if item.id in self.priority_ids:
                                priority_items.append(item)
                            else:
                                other_items.append(item)
                        
                        # 우선순위 순서대로 정렬 (self.priority_ids는 이미 순서가 있는 list)
                        priority_items_sorted = sorted(
                            priority_items,
                            key=lambda t: self.priority_ids.index(t.id) if t.id in self.priority_ids else 999999
                        )
                        
                        # 우선순위 항목을 앞으로
                        self._torrents_to_process[self._current_index:] = priority_items_sorted + other_items
                    else:
                        # 모두 처리 완료 상태면 새 항목들을 뒤에 추가
                        self._torrents_to_process.extend(new_torrents_sorted)
                    
                    
                finally:
                    session.close()
            else:
                # 기존 항목만 재정렬
                if self._current_index < len(self._torrents_to_process) and existing_priority:
                    remaining = self._torrents_to_process[self._current_index:]
                    
                    priority_items = []
                    other_items = []
                    
                    for item in remaining:
                        if item.id in self.priority_ids:
                            priority_items.append(item)
                        else:
                            other_items.append(item)
                    
                    # 우선순위 순서대로 정렬 (self.priority_ids는 이미 순서가 있는 list)
                    priority_items_sorted = sorted(
                        priority_items,
                        key=lambda t: self.priority_ids.index(t.id) if t.id in self.priority_ids else 999999
                    )
                    
                    self._torrents_to_process[self._current_index:] = priority_items_sorted + other_items
    
    def run(self):
        """썸네일 없는 항목 찾아서 업데이트"""
        try:
            self._stop_requested = False
            updated_count = 0
            
            # 스레드 락 초기화
            import threading
            self._priority_lock = threading.Lock()
            
            # 썸네일 검색기 초기화
            try:
                from scrapers.image_finder import ThumbnailEnhancer
                thumbnail_enhancer = ThumbnailEnhancer()
            except ImportError:
                print("[썸네일] 이미지 검색 기능 사용 불가")
                self.error.emit("이미지 검색 기능을 사용할 수 없습니다.")
                return
            
            session = self.db.get_session()
            try:
                from database.models import Torrent
                self._torrents_to_process = []
                
                # 1) 우선순위: 현재 페이지 항목 중 썸네일 없는 것들
                if self.priority_ids:
                    priority_torrents = session.query(Torrent).filter(
                        Torrent.id.in_(self.priority_ids),
                        (Torrent.thumbnail_url == None) | (Torrent.thumbnail_url == '')
                    ).all()
                    
                    # priority_ids 순서대로 정렬 (페이지 표시 순서 유지, self.priority_ids는 이미 list)
                    priority_torrents_sorted = sorted(
                        priority_torrents,
                        key=lambda t: self.priority_ids.index(t.id) if t.id in self.priority_ids else 999999
                    )
                    
                    self._torrents_to_process.extend(priority_torrents_sorted)
                    if priority_torrents_sorted:
                        print(f"[썸네일] 현재 페이지 우선 처리: {len(priority_torrents_sorted)}개")
                
                # 2) 나머지 썸네일 없는 항목들 (전체 처리)
                # 이미 처리할 항목 제외
                processed_ids = [t.id for t in self._torrents_to_process]
                
                query = session.query(Torrent).filter(
                    (Torrent.thumbnail_url == None) | (Torrent.thumbnail_url == '')
                )
                if processed_ids:
                    query = query.filter(~Torrent.id.in_(processed_ids))
                
                # .all()로 모든 항목 가져오기 (limit 제거)
                other_torrents = query.all()
                self._torrents_to_process.extend(other_torrents)
                
                print(f"[썸네일] 나머지 항목: {len(other_torrents)}개")
                
                total = len(self._torrents_to_process)
                if total == 0:
                    print("[썸네일] 업데이트할 항목이 없습니다.")
                    self.finished.emit(0)
                    return
                
                print(f"[썸네일] {total}개 항목 검색 시작")
                
                for idx, torrent in enumerate(self._torrents_to_process):
                    self._current_index = idx
                    
                    if self._stop_requested:
                        print(f"\n[썸네일] 사용자에 의해 중단됨 ({idx}/{total})")
                        break
                    
                    # 진행 상황 (같은 줄 업데이트)
                    print(f"\r[썸네일] 검색 중... {idx + 1}/{total} (업데이트: {updated_count})", end='', flush=True)
                    self.progress.emit(
                        int(((idx + 1) / total) * 100),
                        f"썸네일 검색 중... {idx + 1}/{total}"
                    )
                    
                    # 썸네일 검색
                    torrent_data = {
                        'title': torrent.title,
                        'thumbnail_url': torrent.thumbnail_url
                    }
                    
                    try:
                        updated_data = thumbnail_enhancer.enhance_torrent(torrent_data)
                        
                        # 썸네일이 발견되면 업데이트
                        if updated_data.get('thumbnail_url') and updated_data['thumbnail_url'] != torrent.thumbnail_url:
                            # commit 전에 필요한 값 저장 (commit 후 lazy loading 에러 방지)
                            torrent_id = torrent.id
                            is_priority = torrent_id in self.priority_ids
                            
                            torrent.thumbnail_url = updated_data['thumbnail_url']
                            if updated_data.get('snapshot_urls'):
                                torrent.snapshot_urls = updated_data['snapshot_urls']
                            
                            session.flush()  # commit 대신 flush 사용
                            updated_count += 1
                            
                            # 10개마다 중간 커밋 (프로그램 종료 시 데이터 손실 방지)
                            if updated_count % 10 == 0:
                                try:
                                    session.commit()
                                except Exception as commit_err:
                                    print(f"\n[썸네일] 중간 커밋 오류: {commit_err}")
                                    session.rollback()
                            
                            # 현재 페이지 항목이면 GUI 즉시 업데이트
                            if is_priority:
                                self.thumbnail_updated.emit(torrent_id, updated_data['thumbnail_url'])
                    except Exception as e:
                        # 오류는 조용히 처리
                        continue
                
                print(f"\n[썸네일] 백그라운드 업데이트 완료: {updated_count}개")
                
                # 모든 변경사항 커밋
                try:
                    session.commit()
                except Exception as e:
                    print(f"[썸네일] 커밋 오류: {e}")
                    session.rollback()
                
                self.finished.emit(updated_count)
            
            finally:
                session.close()
        
        except Exception as e:
            print(f"[썸네일] 백그라운드 업데이트 오류: {e}")
            self.error.emit(str(e))


class SingleThumbnailReplaceThread(QThread):
    """단일 항목 썸네일 교체 스레드"""
    updated = Signal(int, str)  # (torrent_id, new_thumbnail_url)
    error = Signal(str)

    def __init__(self, db: Database, torrent_id: int, image_finder=None):
        super().__init__()
        self.db = db
        self.torrent_id = torrent_id
        self.image_finder = image_finder  # 재사용할 ImageFinder

    def run(self):
        try:
            from urllib.parse import urlparse
            session = self.db.get_session()
            try:
                from database.models import Torrent
                try:
                    # SQLAlchemy 1.4/2.0 호환 조회
                    t = session.get(Torrent, self.torrent_id)
                except Exception:
                    t = session.query(Torrent).get(self.torrent_id)
                if not t:
                    self.error.emit("항목을 찾을 수 없습니다.")
                    return
                title = t.title or ''
                current_url = (t.thumbnail_url or '').strip()
                exclude_hosts = []
                if current_url:
                    try:
                        exclude_hosts.append(urlparse(current_url).netloc.lower())
                    except Exception:
                        pass
                
                # ImageFinder 재사용 (없으면 새로 생성)
                if self.image_finder is None:
                    from scrapers.image_finder import ImageFinder
                    self.image_finder = ImageFinder()
                
                result = self.image_finder.search_images(title, max_images=5, exclude_hosts=exclude_hosts or None)
                new_url = (result.get('thumbnail') or '').strip()
                if new_url and new_url != current_url:
                    t.thumbnail_url = new_url
                    
                    # DB 저장 재시도 (lock 방지)
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            session.commit()
                            self.updated.emit(self.torrent_id, new_url)
                            break
                        except Exception as commit_error:
                            if attempt < max_retries - 1:
                                # 재시도
                                import time
                                time.sleep(0.5)
                                session.rollback()
                                # 다시 조회
                                try:
                                    t = session.get(Torrent, self.torrent_id)
                                except Exception:
                                    t = session.query(Torrent).get(self.torrent_id)
                                if t:
                                    t.thumbnail_url = new_url
                            else:
                                # 최종 실패
                                raise commit_error
                else:
                    self.error.emit("대체 가능한 썸네일을 찾지 못했습니다.")
            finally:
                session.close()
        except Exception as e:
            self.error.emit(str(e))

class ScraperThread(QThread):
    """스크래핑 작업을 위한 스레드"""
    
    progress = Signal(int, str)  # (진행률, 메시지)
    finished = Signal(int, int, bool)  # (새로 추가된 수, 업데이트된 수, 중단 여부)
    error = Signal(str)
    
    def __init__(self, db: Database, scraper_manager: ScraperManager, source_key: str, pages: int = 5, enable_thumbnail: bool = False):
        super().__init__()
        self.db = db
        self.scraper_manager = scraper_manager
        self.source_key = source_key
        self.pages = pages
        self.enable_thumbnail = enable_thumbnail
        self._stop_requested = False
    
    def stop(self):
        """스크래핑 중단 요청"""
        self._stop_requested = True
    
    def run(self):
        """스크래핑 실행"""
        try:
            self._stop_requested = False
            total_added = 0
            total_updated = 0
            
            # 썸네일 검색 비활성화 - 백그라운드에서 별도 처리
            
            # 모든 소스에서 수집
            if self.source_key == 'all':
                sources = self.scraper_manager.get_available_sources()
                enabled_sources = [(k, v) for k, v in sources.items() if v['enabled']]
                num_sources = len(enabled_sources)
                
                for source_idx, (key, source_info) in enumerate(enabled_sources):
                    if self._stop_requested:
                        print("[스크래핑] 사용자에 의해 중단됨")
                        break
                    
                    # 진행률 콜백: 전체 소스와 페이지를 고려 (closure 문제 해결)
                    def make_progress_cb(idx, info, total):
                        def progress_cb(page, max_pages, message):
                            # 소스별 진행률 + 페이지 진행률
                            source_progress = (idx / total) * 100
                            page_progress = (page / max_pages) * (100 / total)
                            total_progress = int(source_progress + page_progress)
                            self.progress.emit(
                                total_progress,
                                f"[{info['name']}] {message}"
                            )
                        return progress_cb
                    
                    # 스마트 스크래핑 사용 (중복 최소화)
                    torrents = self.scraper_manager.scrape_source_smart(
                        key, 
                        self.db, 
                        max_pages=self.pages,
                        stop_on_duplicate=True,
                        stop_callback=lambda: self._stop_requested,
                        progress_callback=make_progress_cb(source_idx, source_info, num_sources)
                    )
                    
                    # DB 저장
                    if len(torrents) > 0:
                        source_progress_base = int((source_idx / num_sources) * 100)
                        self.progress.emit(source_progress_base + int((1 / num_sources) * 100), f"[{source_info['name']}] DB 저장 중... ({len(torrents)}개)")
                        
                        session = self.db.get_session()
                        source_added = 0
                        source_updated = 0
                        
                        try:
                            from database.models import Torrent
                            
                            for idx, torrent_data in enumerate(torrents):
                                if self._stop_requested:
                                    # 정지 요청이 있어도 지금까지 수집한 데이터는 저장
                                    pass
                                
                                # 중복 확인
                                existing = session.query(Torrent).filter_by(
                                    source_id=torrent_data.get('source_id'),
                                    source_site=torrent_data.get('source_site')
                                ).first()
                                
                                result = self.db.add_torrent(session, torrent_data)
                                if result:
                                    if existing:
                                        source_updated += 1
                                    else:
                                        source_added += 1
                                
                                # 진행 상황 업데이트 (같은 줄에서 계속 업데이트)
                                print(f"\r[{source_info['name']}] DB 저장 중... {idx + 1}/{len(torrents)} (신규: {source_added}, 업데이트: {source_updated})", end='', flush=True)
                                self.progress.emit(
                                    source_progress_base + int((1 / num_sources) * 100),
                                    f"[{source_info['name']}] DB 저장 중... ({idx + 1}/{len(torrents)})"
                                )
                            
                            total_added += source_added
                            total_updated += source_updated
                            print(f"\n[스크래핑] [{source_info['name']}] DB 저장 완료: 신규 {source_added}개, 업데이트 {source_updated}개")
                        finally:
                            session.close()
                    
                    # 정지 요청 시 루프 중단
                    if self._stop_requested:
                        break
            
            # 특정 소스에서만 수집
            else:
                # 진행률 콜백: 페이지 기준으로 진행률 표시
                def progress_cb(page, max_pages, message):
                    progress = int((page / max_pages) * 100)
                    self.progress.emit(progress, message)
                
                # 스마트 스크래핑 사용 (중복 최소화)
                torrents = self.scraper_manager.scrape_source_smart(
                    self.source_key,
                    self.db,
                    max_pages=self.pages,
                    stop_on_duplicate=True,
                    stop_callback=lambda: self._stop_requested,
                    progress_callback=progress_cb
                )
                
                # DB 저장
                if len(torrents) > 0:
                    self.progress.emit(100, f"DB 저장 중... ({len(torrents)}개)")
                    
                    session = self.db.get_session()
                    try:
                        from database.models import Torrent
                        
                        for idx, torrent_data in enumerate(torrents):
                            if self._stop_requested:
                                # 정지 요청이 있어도 지금까지 수집한 데이터는 저장
                                pass
                            
                            # 메타데이터 보강 (날짜 추정)
                            try:
                                from scrapers.metadata_enricher import enrich_torrent_metadata
                                torrent_data = enrich_torrent_metadata(torrent_data)
                            except:
                                pass
                            
                            # 중복 확인
                            existing = session.query(Torrent).filter_by(
                                source_id=torrent_data.get('source_id'),
                                source_site=torrent_data.get('source_site')
                            ).first()
                            
                            result = self.db.add_torrent(session, torrent_data)
                            if result:
                                if existing:
                                    total_updated += 1
                                else:
                                    total_added += 1
                            
                            # 진행 상황 업데이트 (같은 줄에서 계속 업데이트)
                            print(f"\rDB 저장 중... {idx + 1}/{len(torrents)} (신규: {total_added}, 업데이트: {total_updated})", end='', flush=True)
                            self.progress.emit(100, f"DB 저장 중... ({idx + 1}/{len(torrents)})")
                        
                        print(f"\n[스크래핑] DB 저장 완료: 신규 {total_added}개, 업데이트 {total_updated}개")
                    finally:
                        session.close()
            
            # 정지 여부와 관계없이 완료 시그널 발생 (지금까지 수집한 데이터 저장 완료)
            was_stopped = self._stop_requested
            self.finished.emit(total_added, total_updated, was_stopped)
        
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    """메인 윈도우"""
    
    def __init__(self):
        super().__init__()
        self.db = Database()
        self.scraper_manager = ScraperManager()
        self.scraper_thread = None
        self.thumbnail_thread = None
        # 페이지네이션 초기화 (config.py에서 설정)
        self.page_size = PAGE_SIZE
        self.current_page = 1
        self.total_pages = 1
        self.total_count = 0
        # ImageFinder 미리 생성 (교체 버튼 성능 개선 - Selenium 드라이버 재사용)
        print("[ImageFinder] 공유 인스턴스 생성 중... (Selenium 드라이버 재사용)")
        from scrapers.image_finder import ImageFinder
        self.shared_image_finder = ImageFinder()
        print("[ImageFinder] 공유 인스턴스 생성 완료")
        # 교체 요청 큐 (순차 처리)
        from queue import Queue
        self.replace_queue = Queue()
        self.replace_worker = None
        self.init_ui()
        self.load_torrents()  # load_torrents 내에서 썸네일 업데이트 자동 시작
    
    def init_ui(self):
        """UI 초기화"""
        self.setWindowTitle("토렌트 수집기")
        self.setGeometry(100, 100, 1600, 900)
        
        # 메뉴바
        self.create_menu_bar()
        
        # 중앙 위젯
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        
        # 상단 버튼 영역
        top_layout = QHBoxLayout()
        
        # 소스 선택
        source_label = QLabel("소스:")
        top_layout.addWidget(source_label)
        
        self.source_combo = QComboBox()
        
        # 모든 소스 추가
        sources = self.scraper_manager.get_available_sources()
        
        for key, info in sources.items():
            # 설명 텍스트 추가
            if 'selenium' in key.lower():
                if 'seeders' in key.lower():
                    display_text = f"{info['name']} - 시더수순 정렬"
                elif 'downloads' in key.lower():
                    display_text = f"{info['name']} - 다운로드순 정렬"
                else:
                    display_text = f"⭐ {info['name']} - 최신순 정렬 (권장)"
            else:
                display_text = info['name']
            
            self.source_combo.addItem(display_text, key)
        
        # 구분선
        self.source_combo.insertSeparator(self.source_combo.count())
        
        # 모든 소스
        self.source_combo.addItem("모든 소스 (전체 수집)", "all")
        
        self.source_combo.setMinimumWidth(400)
        top_layout.addWidget(self.source_combo)
        
        # 수집 버튼
        self.fetch_btn = QPushButton("📥 새 토렌트 수집")
        self.fetch_btn.clicked.connect(self.fetch_torrents)
        top_layout.addWidget(self.fetch_btn)
        
        # 정지 버튼
        self.stop_btn = QPushButton("⏹ 정지")
        self.stop_btn.clicked.connect(self.stop_scraping)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        top_layout.addWidget(self.stop_btn)
        
        # 진행 바
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        top_layout.addWidget(self.progress_bar)
        
        top_layout.addStretch()
        
        main_layout.addLayout(top_layout)
        
        # 스플리터 (필터 패널 + 토렌트 리스트)
        splitter = QSplitter(Qt.Horizontal)
        
        # 필터 패널 (크기 줄임)
        self.filter_panel = FilterPanel()
        self.filter_panel.filter_changed.connect(self.on_filter_changed)
        self.filter_panel.setMaximumWidth(200)
        self.filter_panel.setMinimumWidth(180)
        splitter.addWidget(self.filter_panel)
        
        # 토렌트 리스트
        self.torrent_list = TorrentListWidget()
        self.torrent_list.refresh_requested.connect(self.load_torrents)
        self.torrent_list.sort_requested.connect(self.on_sort_requested)
        splitter.addWidget(self.torrent_list)
        
        # 스플리터 비율 설정 (필터 패널 작게, 토렌트 리스트 크게)
        splitter.setSizes([220, 1380])  # 초기 크기 설정 (조금 더 넓게)
        splitter.setStretchFactor(0, 0)  # 필터 패널은 확장 안 함
        splitter.setStretchFactor(1, 1)  # 토렌트 리스트만 확장
        
        main_layout.addWidget(splitter)
        
        # 썸네일 교체 요청 연결
        self.torrent_list.replace_thumbnail_requested.connect(self.on_replace_thumbnail_requested)
        
        # 페이지네이션 컨트롤 (하단 중앙 배치)
        from PySide6.QtWidgets import QSizePolicy
        pagination_widget = QWidget()
        pagination_layout = QHBoxLayout(pagination_widget)
        pagination_layout.setContentsMargins(0, 0, 0, 0)
        
        # 왼쪽 여백
        pagination_layout.addStretch()
        
        # 이전/다음 버튼을 연달아 배치
        self.prev_btn = QPushButton("◀ 이전")
        self.prev_btn.clicked.connect(self.prev_page)
        pagination_layout.addWidget(self.prev_btn)

        self.next_btn = QPushButton("다음 ▶")
        self.next_btn.clicked.connect(self.next_page)
        pagination_layout.addWidget(self.next_btn)
        
        # 페이지 정보
        self.page_label = QLabel("페이지: 1 / 1")
        pagination_layout.addWidget(self.page_label)
        
        # 페이지 입력
        page_input_label = QLabel("이동:")
        pagination_layout.addWidget(page_input_label)
        
        self.page_input = QLineEdit()
        self.page_input.setMaximumWidth(50)
        self.page_input.returnPressed.connect(self.goto_page)
        pagination_layout.addWidget(self.page_input)
        
        # 전체 개수
        self.total_label = QLabel("전체: 0개")
        pagination_layout.addWidget(self.total_label)
        
        # 오른쪽 여백
        pagination_layout.addStretch()
        
        pagination_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        main_layout.addWidget(pagination_widget)
        
        # 상태바
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("준비됨")
    
    def create_menu_bar(self):
        """메뉴바 생성"""
        menubar = self.menuBar()
        
        # 파일 메뉴
        file_menu = menubar.addMenu("파일(&F)")
        
        exit_action = QAction("종료(&X)", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # 데이터 메뉴
        data_menu = menubar.addMenu("데이터(&D)")
        
        fetch_action = QAction("새 토렌트 수집(&F)", self)
        fetch_action.setShortcut("Ctrl+F")
        fetch_action.triggered.connect(self.fetch_torrents)
        data_menu.addAction(fetch_action)
        
        refresh_action = QAction("새로고침(&R)", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self.load_torrents)
        data_menu.addAction(refresh_action)

        # 날짜 보정
        fix_dates_action = QAction("날짜 보정(빈 항목 채우기)", self)
        fix_dates_action.triggered.connect(self.fix_missing_dates)
        data_menu.addAction(fix_dates_action)
        
        # 도움말 메뉴
        help_menu = menubar.addMenu("도움말(&H)")
        
        about_action = QAction("정보(&A)", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

        # 설정 메뉴
        settings_menu = menubar.addMenu("설정(&S)")

        ui_settings_action = QAction("환경 설정...", self)
        ui_settings_action.triggered.connect(self.open_settings)
        settings_menu.addAction(ui_settings_action)
    
    def load_torrents(self):
        """토렌트 목록 로드 (페이지네이션)"""
        filters = self.filter_panel.get_filters()
        
        session = self.db.get_session()
        try:
            # 전체 개수 가져오기
            self.total_count = self.db.get_total_count(
                session,
                period_days=filters['period_days'],
                search_query=filters['search_query']
            )
            
            # 전체 페이지 수 계산
            self.total_pages = max(1, (self.total_count + self.page_size - 1) // self.page_size)
            
            # 현재 페이지가 범위를 벗어나면 조정
            if self.current_page > self.total_pages:
                self.current_page = self.total_pages
            
            # 오프셋 계산
            offset = (self.current_page - 1) * self.page_size
            
            # 정렬 조건 (torrent_list의 정렬 상태 사용, 기본값: 날짜순 내림차순)
            sort_by = self.torrent_list.current_sort_column or 'upload_date'
            sort_order = self.torrent_list.current_sort_order or 'desc'
            
            # 토렌트 가져오기
            torrents = self.db.get_torrents(
                session,
                period_days=filters['period_days'],
                search_query=filters['search_query'],
                sort_by=sort_by,
                sort_order=sort_order,
                limit=self.page_size,
                offset=offset
            )
            
            print(f"[UI] 토렌트 로드: 전체 {self.total_count}개, 현재 페이지 {self.current_page}/{self.total_pages}, 표시 {len(torrents)}개")
            self.torrent_list.set_torrents(torrents)
            
            # UI 업데이트
            self.update_pagination_ui()
            self.status_bar.showMessage(
                f"페이지 {self.current_page}/{self.total_pages} - "
                f"{len(torrents)}개 표시 (전체 {self.total_count}개)"
            )
        
        except Exception as e:
            QMessageBox.critical(self, "오류", f"토렌트 로드 실패: {e}")
        
        finally:
            session.close()
        
        # 세션을 닫은 후에 썸네일 업데이트 시작 (세션 충돌 방지)
        # 약간의 지연을 주어 UI가 먼저 응답하도록 함
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, self.start_thumbnail_update)  # 100ms 후 시작
    
    def update_pagination_ui(self):
        """페이지네이션 UI 업데이트"""
        self.page_label.setText(f"페이지: {self.current_page} / {self.total_pages}")
        self.total_label.setText(f"전체: {self.total_count}개")
        self.page_input.setText(str(self.current_page))
        
        # 버튼 활성화/비활성화
        self.prev_btn.setEnabled(self.current_page > 1)
        self.next_btn.setEnabled(self.current_page < self.total_pages)
    
    def prev_page(self):
        """이전 페이지"""
        if self.current_page > 1:
            self.current_page -= 1
            self.load_torrents()
    
    def next_page(self):
        """다음 페이지"""
        if self.current_page < self.total_pages:
            self.current_page += 1
            self.load_torrents()
    
    def goto_page(self):
        """특정 페이지로 이동"""
        try:
            page = int(self.page_input.text())
            if 1 <= page <= self.total_pages:
                self.current_page = page
                self.load_torrents()
            else:
                QMessageBox.warning(self, "경고", f"1-{self.total_pages} 범위의 페이지를 입력하세요.")
        except ValueError:
            QMessageBox.warning(self, "경고", "올바른 페이지 번호를 입력하세요.")
    
    def on_filter_changed(self, filters: dict):
        """필터 변경 이벤트"""
        self.current_page = 1  # 필터 변경 시 첫 페이지로
        self.load_torrents()
    
    def on_sort_requested(self, column: str, order: str):
        """정렬 요청 이벤트 (테이블 헤더 클릭)"""
        # 정렬 변경 시 첫 페이지로 이동
        self.current_page = 1
        # 정렬 상태는 이미 torrent_list에 저장되어 있으므로 그냥 로드
        self.load_torrents()
    
    def fetch_torrents(self):
        """새 토렌트 수집"""
        if self.scraper_thread and self.scraper_thread.isRunning():
            QMessageBox.warning(self, "경고", "이미 수집 작업이 진행 중입니다.")
            return
        
        # 선택된 소스 가져오기
        source_key = self.source_combo.currentData()
        
        self.fetch_btn.setEnabled(False)
        self.source_combo.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.stop_btn.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        # 설정값 반영
        qsettings = QSettings()
        max_pages = int(qsettings.value('scrape/max_pages', MAX_SCRAPE_PAGES))
        enable_thumb = qsettings.value('scrape/enable_thumbnail', ENABLE_THUMBNAIL, type=bool)

        # 스크래퍼 스레드 시작
        self.scraper_thread = ScraperThread(
            self.db, 
            self.scraper_manager, 
            source_key, 
            pages=max_pages,
            enable_thumbnail=enable_thumb
        )
        self.scraper_thread.progress.connect(self.on_scrape_progress)
        self.scraper_thread.finished.connect(self.on_scrape_finished)
        self.scraper_thread.error.connect(self.on_scrape_error)
        self.scraper_thread.start()
    
    def stop_scraping(self):
        """스크래핑 중단"""
        if self.scraper_thread and self.scraper_thread.isRunning():
            self.scraper_thread.stop()
            self.stop_btn.setEnabled(False)
            self.status_bar.showMessage("수집 중단 중...")
    
    def on_scrape_progress(self, value: int, message: str):
        """스크래핑 진행 상황 업데이트"""
        self.progress_bar.setValue(value)
        self.status_bar.showMessage(message)
    
    def on_scrape_finished(self, added_count: int, updated_count: int, was_stopped: bool = False):
        """스크래핑 완료"""
        self.fetch_btn.setEnabled(True)
        self.source_combo.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        self.progress_bar.setVisible(False)
        
        total = added_count + updated_count
        
        if was_stopped:
            message = f"수집 중단: 신규 {added_count}개, 업데이트 {updated_count}개 (총 {total}개) 저장됨"
            self.status_bar.showMessage(message)
            
            QMessageBox.information(
                self, 
                "수집 중단", 
                f"수집이 중단되었습니다.\n\n"
                f"지금까지 수집한 데이터:\n"
                f"신규 추가: {added_count}개\n"
                f"기존 업데이트: {updated_count}개\n"
                f"총 처리: {total}개"
            )
        else:
            message = f"수집 완료: 신규 {added_count}개, 업데이트 {updated_count}개 (총 {total}개)"
            self.status_bar.showMessage(message)
            
            QMessageBox.information(
                self, 
                "완료", 
                f"신규 추가: {added_count}개\n"
                f"기존 업데이트: {updated_count}개\n"
                f"총 처리: {total}개"
            )
        
        # 목록 새로고침 (썸네일 업데이트 자동 시작)
        self.load_torrents()
    
    def on_scrape_error(self, error_msg: str):
        """스크래핑 오류"""
        self.fetch_btn.setEnabled(True)
        self.source_combo.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        self.progress_bar.setVisible(False)
        self.status_bar.showMessage("수집 실패")
        
        QMessageBox.critical(self, "오류", f"토렌트 수집 실패:\n{error_msg}")
    
    def show_about(self):
        """정보 다이얼로그 표시"""
        sources = self.scraper_manager.get_available_sources()
        sources_list = "<br>".join([f"• {info['name']}: {info['description']}" 
                                    for info in sources.values()])
        
        QMessageBox.about(
            self,
            "토렌트 수집기 정보",
            "<h3>토렌트 수집기</h3>"
            "<p>성인 토렌트 사이트에서 데이터를 수집하고 관리하는 애플리케이션입니다.</p>"
            "<p><b>버전:</b> 2.0.0</p>"
            "<p><b>개발:</b> Python + PySide6</p>"
            "<p><b>지원 소스:</b></p>"
            f"<p style='margin-left: 20px;'>{sources_list}</p>"
        )

    def fix_missing_dates(self):
        """업로드 날짜가 비어있는 항목을 원본에서 보정"""
        session = self.db.get_session()
        try:
            fixed = self.db.backfill_missing_dates(session, limit=1000)
            QMessageBox.information(self, "날짜 보정", f"보정된 항목: {fixed}개")
            if fixed:
                self.load_torrents()
        except Exception as e:
            QMessageBox.critical(self, "오류", f"날짜 보정 실패: {e}")
        finally:
            session.close()

    def start_thumbnail_update(self):
        """썸네일 백그라운드 업데이트 시작"""
        # 현재 페이지에 표시된 항목들 중 썸네일 없는 항목만 필터링 (이미 메모리에 있는 데이터 사용)
        priority_ids = []
        try:
            torrents = self.torrent_list.torrents
            for idx, t in enumerate(torrents):
                # 썸네일이 없는 항목만
                has_thumbnail = bool(t.thumbnail_url and t.thumbnail_url.strip())
                if not has_thumbnail:
                    priority_ids.append(t.id)
        except Exception as e:
            print(f"[썸네일] 현재 페이지 필터링 실패: {e}")
            priority_ids = []
        
        # 이미 실행 중이면 우선순위만 업데이트
        if self.thumbnail_thread and self.thumbnail_thread.isRunning():
            if priority_ids:
                print(f"[썸네일] 페이지 변경 - 우선순위 업데이트: {len(priority_ids)}개 (ID: {priority_ids})")
                self.thumbnail_thread.update_priority_ids(priority_ids)
            else:
                print(f"[썸네일] 페이지 변경 - 현재 페이지에 썸네일 없는 항목 없음")
            return
        
        # 새로 시작
        if priority_ids:
            print(f"[썸네일] 백그라운드 업데이트 시작 (우선: {len(priority_ids)}개, ID: {priority_ids})")
        else:
            print(f"[썸네일] 백그라운드 업데이트 시작 (우선 항목 없음)")
        self.thumbnail_thread = ThumbnailUpdateThread(self.db, priority_ids)
        self.thumbnail_thread.progress.connect(self.on_thumbnail_progress)
        self.thumbnail_thread.finished.connect(self.on_thumbnail_finished)
        self.thumbnail_thread.error.connect(self.on_thumbnail_error)
        self.thumbnail_thread.thumbnail_updated.connect(self.on_thumbnail_item_updated)
        self.thumbnail_thread.start()
    
    def on_thumbnail_progress(self, value: int, message: str):
        """썸네일 업데이트 진행 상황"""
        # 상태바에만 표시 (조용하게)
        self.status_bar.showMessage(f"[백그라운드] {message}")
    
    def on_thumbnail_finished(self, updated_count: int):
        """썸네일 업데이트 완료"""
        if updated_count > 0:
            print(f"[썸네일] 백그라운드 업데이트 완료: {updated_count}개")
            self.status_bar.showMessage(f"썸네일 {updated_count}개 업데이트 완료", 3000)
            # 목록 새로고침 (썸네일이 보이도록, 단 썸네일 업데이트는 다시 시작하지 않음)
            self.torrent_list.refresh_thumbnails()  # 썸네일만 새로고침
        else:
            print("[썸네일] 업데이트할 항목이 없습니다.")
            self.status_bar.showMessage("썸네일 업데이트 완료 (모든 항목 최신)", 2000)
    
    def on_thumbnail_error(self, error_msg: str):
        """썸네일 업데이트 오류"""
        print(f"[썸네일] 오류: {error_msg}")
        self.status_bar.showMessage(f"썸네일 업데이트 오류: {error_msg}", 3000)
    
    def on_thumbnail_item_updated(self, torrent_id: int, thumbnail_url: str):
        """개별 썸네일 업데이트 (현재 페이지 항목)"""
        # 현재 표시된 리스트에서 해당 항목 찾아서 GUI 업데이트
        self.torrent_list.update_thumbnail_by_id(torrent_id, thumbnail_url)

    def on_replace_thumbnail_requested(self, torrent_id: int):
        """교체 버튼 클릭 처리: 큐에 추가하여 순차 처리"""
        try:
            # 큐에 추가
            self.replace_queue.put(torrent_id)
            queue_size = self.replace_queue.qsize()
            
            if queue_size == 1:
                self.status_bar.showMessage(f"썸네일 교체 시작 (ID: {torrent_id})", 0)
            else:
                self.status_bar.showMessage(f"썸네일 교체 대기 중... ({queue_size}개 대기)", 0)
            
            # 현재 작업 중이 아니면 큐 처리 시작
            if self.replace_worker is None or not self.replace_worker.isRunning():
                self._process_replace_queue()
        except Exception as e:
            self.status_bar.showMessage(f"썸네일 교체 오류: {e}", 3000)
            self.torrent_list.enable_replace_button(torrent_id)
    
    def _process_replace_queue(self):
        """교체 큐에서 다음 작업 처리"""
        try:
            if self.replace_queue.empty():
                return
            
            # 큐에서 다음 ID 가져오기
            torrent_id = self.replace_queue.get()
            
            # 작업 스레드 생성
            self.replace_worker = SingleThumbnailReplaceThread(self.db, torrent_id, self.shared_image_finder)
            
            # 완료/오류 시 다음 큐 항목 처리
            def _on_completed(tid, url):
                self.on_thumbnail_item_updated(tid, url)
                remaining = self.replace_queue.qsize()
                if remaining > 0:
                    self.status_bar.showMessage(f"✅ 교체 완료! 남은 작업: {remaining}개", 2000)
                else:
                    self.status_bar.showMessage("✅ 모든 썸네일 교체 완료!", 2000)
                self.torrent_list.enable_replace_button(tid)
                # 다음 큐 항목 처리
                self._process_replace_queue()
            
            def _on_error(e):
                remaining = self.replace_queue.qsize()
                self.status_bar.showMessage(f"❌ 교체 실패: {e} (남은 작업: {remaining}개)", 3000)
                self.torrent_list.enable_replace_button(torrent_id)
                # 다음 큐 항목 처리
                self._process_replace_queue()
            
            self.replace_worker.updated.connect(_on_completed)
            self.replace_worker.error.connect(_on_error)
            self.replace_worker.start()
            
        except Exception as e:
            self.status_bar.showMessage(f"큐 처리 오류: {e}", 3000)
    
    def get_current_page_ids(self) -> list:
        """현재 페이지에 표시된 항목들의 ID 반환 (이미 로드된 데이터 사용)"""
        try:
            # torrent_list에 이미 로드된 torrents에서 ID 가져오기
            # 세션 충돌 방지를 위해 새 쿼리 대신 기존 데이터 사용
            torrents = self.torrent_list.torrents
            return [t.id for t in torrents if hasattr(t, 'id')]
        
        except Exception as e:
            print(f"[썸네일] 현재 페이지 ID 가져오기 실패: {e}")
            return []

    def open_settings(self):
        """설정 다이얼로그 열기"""
        settings = QSettings()
        current_width = int(settings.value('ui/thumbnail_width', 120))
        current_height = int(settings.value('ui/row_height', 80))
        current_hover = settings.value('ui/hover_preview', True, type=bool)
        max_pages = int(settings.value('scrape/max_pages', 100))
        enable_thumb = settings.value('scrape/enable_thumbnail', True, type=bool)
        enable_javdb = settings.value('images/enable_javdb_fallback', False, type=bool)
        enable_selenium = settings.value('images/enable_selenium_for_images', True, type=bool)
        image_timeout = int(settings.value('images/image_http_timeout', 10))
        image_retries = int(settings.value('images/image_http_retries', 2))

        dlg = SettingsDialog(
            self,
            current_width, current_height, current_hover,
            max_pages, enable_thumb,
            enable_javdb, enable_selenium,
            image_timeout, image_retries
        )
        if dlg.exec():
            values = dlg.get_values()
            # UI 적용
            ui = values['ui']
            self.torrent_list.apply_settings(ui['thumbnail_width'], ui['row_height'], ui['hover_preview'])
            # 설정 저장
            settings.setValue('scrape/max_pages', int(values['scrape']['max_pages']))
            settings.setValue('scrape/enable_thumbnail', bool(values['scrape']['enable_thumbnail']))
            settings.setValue('images/enable_javdb_fallback', bool(values['images']['enable_javdb_fallback']))
            settings.setValue('images/enable_selenium_for_images', bool(values['images']['enable_selenium_for_images']))
            settings.setValue('images/image_http_timeout', int(values['images']['image_http_timeout']))
            settings.setValue('images/image_http_retries', int(values['images']['image_http_retries']))
    
    def closeEvent(self, event):
        """윈도우 닫기 이벤트 (스레드 정리)"""
        print("[종료] 앱 종료 중... 스레드 정리")
        
        # 교체 작업 큐 비우기
        if self.replace_worker and self.replace_worker.isRunning():
            print("[종료] 교체 작업 스레드 중지 중...")
            self.replace_worker.wait(2000)
            if self.replace_worker.isRunning():
                print("[종료] 교체 작업 스레드 강제 종료")
                self.replace_worker.terminate()
        
        # ImageFinder의 Selenium 드라이버 정리
        if self.shared_image_finder:
            print("[종료] ImageFinder Selenium 드라이버 종료 중...")
            try:
                if hasattr(self.shared_image_finder, 'selenium_driver') and self.shared_image_finder.selenium_driver:
                    self.shared_image_finder.selenium_driver.quit()
            except Exception as e:
                print(f"[종료] ImageFinder 정리 오류: {e}")
        
        # 이미지 다운로더 스레드 중지 (먼저 정리)
        if hasattr(self, 'torrent_list') and hasattr(self.torrent_list, 'image_downloader'):
            print("[종료] 이미지 다운로더 스레드 중지 중...")
            self.torrent_list.image_downloader.stop_all()
        
        # 스크래핑 스레드 중지
        if self.scraper_thread and self.scraper_thread.isRunning():
            print("[종료] 스크래핑 스레드 중지 중...")
            self.scraper_thread.stop()
            self.scraper_thread.wait(3000)  # 최대 3초 대기
            if self.scraper_thread.isRunning():
                print("[종료] 스크래핑 스레드 강제 종료")
                self.scraper_thread.terminate()
        
        # 썸네일 업데이트 스레드 중지
        if self.thumbnail_thread and self.thumbnail_thread.isRunning():
            print("[종료] 썸네일 스레드 중지 중...")
            self.thumbnail_thread.stop()
            self.thumbnail_thread.wait(3000)  # 최대 3초 대기
            if self.thumbnail_thread.isRunning():
                print("[종료] 썸네일 스레드 강제 종료")
                self.thumbnail_thread.terminate()
        
        print("[종료] 스레드 정리 완료")
        event.accept()
