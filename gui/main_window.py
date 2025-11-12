"""메인 윈도우"""
import re
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
from config import PAGE_SIZE, MAX_SCRAPE_PAGES, ENABLE_THUMBNAIL, MAX_CONSECUTIVE_DUPLICATES, THUMBNAIL_SERVER_BLOCK_THRESHOLD
from .settings_dialog import SettingsDialog


class ThumbnailUpdateThread(QThread):
    """썸네일 백그라운드 업데이트 스레드"""
    
    progress = Signal(int, str)  # (진행률, 메시지)
    finished = Signal(int)  # (업데이트된 수)
    error = Signal(str)
    thumbnail_updated = Signal(int, str)  # (torrent_id, thumbnail_url) - 개별 업데이트
    
    def __init__(self, db: Database, priority_ids: list = None, db_writer: DBWriterThread = None):
        super().__init__()
        self.db = db
        self.db_writer = db_writer  # 현재 사용되지 않지만 호환성을 위해 유지
        self.priority_ids = priority_ids or []  # 현재 페이지 항목 ID 우선 (순서 유지를 위해 list 사용)
        self._stop_requested = False
        self._priority_lock = None  # 스레드 락 (run에서 초기화)
        self._torrents_to_process = []  # 처리할 항목 리스트
        self._current_index = 0  # 현재 처리 중인 인덱스
    
    def stop(self):
        """업데이트 중단 요청"""
        self._stop_requested = True
    
    def update_priority_ids(self, new_priority_ids: list, force_first: bool = False):
        """페이지 변경 시 우선순위 동적 업데이트 (썸네일 없는 항목 ID만 전달받음)
        
        Args:
            new_priority_ids: 우선순위 ID 리스트
            force_first: True이면 맨 앞에 강제로 추가 (썸네일 교체 버튼 클릭 시)
        """
        if not hasattr(self, '_priority_lock') or self._priority_lock is None:
            print(f"[우선순위 업데이트] 스킵: _priority_lock이 없음")
            return
        
        # 빈 리스트는 무시
        if not new_priority_ids:
            print(f"[우선순위 업데이트] 스킵: new_priority_ids가 비어있음")
            return
        
        # 중복 호출 방지: 같은 ID 리스트가 연속으로 들어오면 무시
        if hasattr(self, '_last_priority_ids'):
            if set(new_priority_ids) == set(self._last_priority_ids) and not force_first:
                # 같은 ID 리스트이고 force_first가 아니면 무시 (너무 자주 호출되는 것 방지)
                print(f"[우선순위 업데이트] 스킵: 중복 호출 (같은 ID 리스트: {len(new_priority_ids)}개)")
                return
        
        print(f"[우선순위 업데이트] 시작: {len(new_priority_ids)}개 항목, force_first={force_first}")
        self._last_priority_ids = new_priority_ids.copy()
        
        # 별도 스레드에서 실행하여 UI 블로킹 방지
        import threading
        def update_priority_worker():
            """별도 스레드에서 실행되는 우선순위 업데이트 작업"""
            import queue
            with self._priority_lock:
                # 새 우선순위 ID 설정
                self.priority_ids = new_priority_ids
                
                if not self.priority_ids:
                    return
                
                # 병렬 처리 중이면 우선순위 리스트에 직접 추가
                if hasattr(self, 'priority_list') and hasattr(self, 'priority_lock') and hasattr(self, 'main_list') and hasattr(self, 'main_lock') and hasattr(self, 'server_queues'):
                    # force_first=True이면 기존 우선순위 리스트를 완전히 비우고 새 항목만 추가
                    existing_priority_items = []
                    with self.priority_lock:
                        unprocessed_priority = [x for x in self.priority_list if not x['processed']]
                        for entry in unprocessed_priority[:]:  # 복사본으로 순회
                            item = entry['item']
                            # 새 우선순위에 포함되지 않은 것만 보관 (나중에 뒤에 추가)
                            if item['id'] not in self.priority_ids:
                                existing_priority_items.append(item)
                            # 새 우선순위에 포함된 항목은 제거 (나중에 새로 추가됨)
                            if force_first or item['id'] in self.priority_ids:
                                entry['processed'] = True  # 마킹하여 제거 효과
                    
                    # 현재 리스트에 있는 항목 ID 확인 (중복 방지)
                    existing_ids = set()
                    
                    # main_list에서 기존 항목 ID 확인
                    with self.main_lock:
                        unprocessed_main = [x for x in self.main_list if not x['processed']]
                        for entry in unprocessed_main:
                            item = entry['item']
                            existing_ids.add(item['id'])
                    
                    # server_queues에서도 확인
                    for q in self.server_queues.values():
                        temp_items = []
                        while not q.empty():
                            try:
                                item = q.get_nowait()
                                existing_ids.add(item['id'])
                                temp_items.append(item)
                            except queue.Empty:
                                break
                        for item in temp_items:
                            q.put(item)
                    
                    # 현재 페이지의 모든 항목을 우선순위 큐에 추가 (이미 큐에 있어도 우선순위로)
                    # 1단계: 기존 리스트에서 현재 페이지 항목 찾아서 우선순위 리스트로 이동
                    priority_items_from_queue = []
                    
                    # main_list에서 현재 페이지 항목 찾기
                    with self.main_lock:
                        unprocessed_main = [x for x in self.main_list if not x['processed']]
                        for entry in unprocessed_main[:]:  # 복사본으로 순회
                            item = entry['item']
                            if item['id'] in self.priority_ids:
                                # 현재 페이지 항목이면 우선순위 리스트로 이동
                                item['is_priority'] = True
                                # force_first일 때는 force_replace 플래그 추가
                                if force_first:
                                    item['force_replace'] = True
                                priority_items_from_queue.append(item)
                                entry['processed'] = True  # 마킹하여 제거 효과
                    
                    # server_queues에서도 현재 페이지 항목 찾기
                    for q in self.server_queues.values():
                        temp_items = []
                        while not q.empty():
                            try:
                                item = q.get_nowait()
                                if item['id'] in self.priority_ids:
                                    # 현재 페이지 항목이면 우선순위 큐로 이동
                                    item['is_priority'] = True
                                    # force_first일 때는 force_replace 플래그 추가
                                    if force_first:
                                        item['force_replace'] = True
                                    priority_items_from_queue.append(item)
                                else:
                                    temp_items.append(item)
                            except queue.Empty:
                                break
                        # 일반 항목은 다시 큐에 넣기
                        for item in temp_items:
                            q.put(item)
                    
                    # 2단계: DB에서 새 항목 가져오기 (큐에 없는 항목만)
                    new_ids = [id for id in self.priority_ids if id not in existing_ids]
                
                    priority_items_from_db = []
                    if new_ids:
                        session = self.db.get_session()
                        try:
                            from database.models import Torrent
                            
                            # 새 항목들만 쿼리
                            # force_first=True이면 썸네일이 있어도 검색 (교체 기능용)
                            if force_first:
                                new_torrents = session.query(Torrent).filter(
                                    Torrent.id.in_(new_ids)
                                ).all()
                            else:
                                # 일반 페이지 변경 시에는 썸네일 없는 항목만
                                new_torrents = session.query(Torrent).filter(
                                    Torrent.id.in_(new_ids),
                                    (Torrent.thumbnail_url == None) | (Torrent.thumbnail_url == '')
                                ).all()
                            
                            # priority_ids 순서대로 정렬
                            def safe_sort_key(t):
                                try:
                                    torrent_id = t.id
                                    if torrent_id in self.priority_ids:
                                        return self.priority_ids.index(torrent_id)
                                    else:
                                        return 999999
                                except Exception:
                                    return 999999
                            
                            new_torrents_sorted = sorted(new_torrents, key=safe_sort_key)
                            
                            # 우선순위 항목을 딕셔너리로 변환
                            for t in new_torrents_sorted:
                                try:
                                    priority_items_from_db.append({
                                        'id': t.id,
                                        'title': t.title or '',
                                        'thumbnail_url': t.thumbnail_url or '',
                                        'is_priority': True,
                                        'force_replace': force_first  # force_first일 때 썸네일이 있어도 검색
                                    })
                                except Exception:
                                    continue
                        finally:
                            session.close()
                    
                    # 3단계: 모든 우선순위 항목을 priority_ids 순서대로 정렬하여 우선순위 큐에 추가
                    all_priority_items = priority_items_from_queue + priority_items_from_db
                    
                    # priority_ids 순서대로 정렬
                    def priority_sort_key(item):
                        try:
                            item_id = item['id']
                            if item_id in self.priority_ids:
                                return self.priority_ids.index(item_id)
                            else:
                                return 999999
                        except Exception:
                            return 999999
                    
                    all_priority_items_sorted = sorted(all_priority_items, key=priority_sort_key)
                    
                    # 우선순위 리스트에 추가 (중복 제거)
                    # force_first=True이면 새 항목만 추가, 아니면 기존 항목 뒤에 추가
                    added_ids = set()
                    with self.priority_lock:
                        before_list_size = len([x for x in self.priority_list if not x['processed']])
                        
                    if all_priority_items_sorted:
                        # force_first일 때 디버그 출력
                        if force_first and all_priority_items_sorted:
                            print(f"[썸네일 교체] 우선순위 리스트 최우선 추가: {len(all_priority_items_sorted)}개 항목")
                            for item in all_priority_items_sorted[:3]:  # 처음 3개만 출력
                                print(f"  - ID: {item['id']}, 제목: {item.get('title', '')[:50]}")
                        
                        # 새 우선순위 항목들을 추가 (priority_ids 순서대로)
                        with self.priority_lock:
                            added_count = 0
                            # force_first=True이면 맨 앞에 추가, 아니면 뒤에 추가
                            if force_first:
                                # force_first일 때는 역순으로 insert(0, ...)하여 순서 유지
                                for item in reversed(all_priority_items_sorted):
                                    if item['id'] not in added_ids:
                                        self.priority_list.insert(0, {'item': item, 'processed': False, 'processing_by': None})
                                        added_ids.add(item['id'])
                                        added_count += 1
                            else:
                                # 일반적인 경우 뒤에 추가
                                for item in all_priority_items_sorted:
                                    if item['id'] not in added_ids:
                                        self.priority_list.append({'item': item, 'processed': False, 'processing_by': None})
                                        added_ids.add(item['id'])
                                        added_count += 1
                            
                            # force_first가 아니면 기존 우선순위 항목들을 뒤에 추가
                            if not force_first and existing_priority_items:
                                for existing_item in existing_priority_items:
                                    if existing_item['id'] not in added_ids:
                                        self.priority_list.append({'item': existing_item, 'processed': False, 'processing_by': None})
                                        added_ids.add(existing_item['id'])
                                        added_count += 1
                            elif force_first and existing_priority_items:
                                # force_first일 때는 기존 항목을 뒤에 추가 (새 항목이 먼저 처리되도록)
                                for existing_item in existing_priority_items:
                                    if existing_item['id'] not in added_ids:
                                        self.priority_list.append({'item': existing_item, 'processed': False, 'processing_by': None})
                                        added_ids.add(existing_item['id'])
                                        added_count += 1
                            
                            after_list_size = len([x for x in self.priority_list if not x['processed']])
                            print(f"[우선순위 업데이트] 리스트에 추가 완료: {added_count}개 추가 (리스트 크기: {before_list_size} → {after_list_size})")
                            print(f"  - 리스트에서 이동: {len(priority_items_from_queue)}개, DB에서 새로 추가: {len(priority_items_from_db)}개")
                    else:
                        print(f"[우선순위 업데이트] 추가할 항목 없음 (리스트에서 이동: {len(priority_items_from_queue)}개, DB에서 새로 추가: {len(priority_items_from_db)}개)")
                    
                    if all_priority_items_sorted:
                        moved_count = len(priority_items_from_queue)
                        new_count = len(priority_items_from_db)
        
        # 별도 스레드에서 실행
        update_thread = threading.Thread(target=update_priority_worker, daemon=True)
        update_thread.start()
    
    def run(self):
        """썸네일 없는 항목 찾아서 업데이트 (서버별 스레드 1개씩)"""
        try:
            self._stop_requested = False
            updated_count = 0
            
            # 스레드 락 초기화
            import threading
            import queue
            self._priority_lock = threading.Lock()
            self._update_lock = threading.Lock()  # updated_count 동기화용
            
            # 썸네일 검색 기능 확인
            try:
                from scrapers.image_finder import ThumbnailEnhancer
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
                    def safe_sort_key(t):
                        try:
                            torrent_id = t.id
                            if torrent_id in self.priority_ids:
                                return self.priority_ids.index(torrent_id)
                            else:
                                return 999999
                        except Exception:
                            return 999999
                    
                    priority_torrents_sorted = sorted(priority_torrents, key=safe_sort_key)
                    
                    self._torrents_to_process.extend(priority_torrents_sorted)
                
                # 2) 나머지 썸네일 없는 항목들 (또는 .ico 포함된 것들) (전체 처리)
                # 이미 처리할 항목 제외
                processed_ids = [t.id for t in self._torrents_to_process]
                
                from sqlalchemy import or_
                query = session.query(Torrent).filter(
                    or_(
                        (Torrent.thumbnail_url == None) | (Torrent.thumbnail_url == ''),
                        Torrent.thumbnail_url.like('%.ico%'),
                        Torrent.thumbnail_url.like('%favicon%')
                    )
                )
                
                # .ico가 포함된 썸네일 및 javbee.vip/storage/ 경로의 썸네일은 빈 값으로 초기화 (DB_writer 사용)
                ico_torrents = session.query(Torrent).filter(
                    or_(
                        Torrent.thumbnail_url.like('%.ico%'),
                        Torrent.thumbnail_url.like('%favicon%'),
                        Torrent.thumbnail_url.like('%javbee.vip/storage/%'),
                        Torrent.thumbnail_url.like('%39466ce5e12977f09eddf35bf06aa721.jpg%')
                    )
                ).all()
                if ico_torrents:
                    ico_found = False
                    for t in ico_torrents:
                        if t.id not in processed_ids:
                            thumb_url_lower = (t.thumbnail_url or '').lower()
                            if 'javbee.vip/storage/' in thumb_url_lower or '39466ce5e12977f09eddf35bf06aa721.jpg' in thumb_url_lower:
                                print(f"[썸네일] javbee.vip/storage/ 이미지 감지, 초기화: {t.title[:50]}... ({t.thumbnail_url[:60]}...)")
                            elif 'javbee.vip/images/loading' in thumb_url_lower:
                                print(f"[썸네일] javbee.vip loading.gif 이미지 감지, 초기화: {t.title[:50]}... ({t.thumbnail_url[:60]}...)")
                            else:
                                print(f"[썸네일] .ico 파일 감지, 초기화: {t.title[:50]}... ({t.thumbnail_url[:60]}...)")
                            # DB_writer를 통해 비동기 저장
                            if self.db_writer:
                                self.db_writer.update_thumbnail(t.id, '')
                            else:
                                # DB_writer가 없으면 직접 저장
                                t.thumbnail_url = ''
                                ico_found = True
                    if ico_found and not self.db_writer:
                        # DB_writer가 없을 때만 직접 커밋
                        session.commit()
                    elif self.db_writer:
                        # DB_writer를 사용하면 세션은 닫기만 (커밋은 DB_writer가 처리)
                        pass
                if processed_ids:
                    query = query.filter(~Torrent.id.in_(processed_ids))
                
                # .all()로 모든 항목 가져오기 (limit 제거)
                other_torrents = query.all()
                self._torrents_to_process.extend(other_torrents)
                
                
                total = len(self._torrents_to_process)
                if total == 0:
                    print("[썸네일] 업데이트할 항목이 없습니다.")
                    self.finished.emit(0)
                    return
                
                # ID와 제목만 저장 (세션 객체는 스레드 안전하지 않음)
                torrent_items = []
                for t in self._torrents_to_process:
                    try:
                        torrent_items.append({
                            'id': t.id,
                            'title': t.title or '',
                            'thumbnail_url': t.thumbnail_url or '',
                            'is_priority': t.id in self.priority_ids
                        })
                    except Exception:
                        continue
                
                session.close()  # 메인 세션 종료
                
                # 병렬 처리 중임을 표시 (update_priority_ids에서 사용)
                self._current_index = -1
                
                print(f"[썸네일] {total}개 항목 검색 시작 (서버별 병렬 처리)")
                
                # 우선순위 리스트 생성 (현재 페이지 항목 우선) - 큐 대신 리스트 사용
                priority_list = []
                priority_lock = threading.Lock()
                # 리스트를 즉시 설정 (다른 스레드가 접근할 수 있도록)
                self.priority_list = priority_list
                self.priority_lock = priority_lock
                
                priority_items = [item for item in torrent_items if item.get('is_priority', False)]
                if priority_items:
                    # 우선순위 항목을 priority_ids 순서대로 정렬
                    priority_items_sorted = sorted(
                        priority_items,
                        key=lambda x: self.priority_ids.index(x['id']) if x['id'] in self.priority_ids else 999999
                    )
                    for item in priority_items_sorted:
                        priority_list.append({'item': item, 'processed': False, 'processing_by': None})
                
                # 공통 대기열 생성 (나머지 작업)
                # 일반 리스트 생성 (FC2 포함 모든 항목) - 큐 대신 리스트 사용
                main_list = []
                main_lock = threading.Lock()
                # 리스트를 즉시 설정 (다른 스레드가 접근할 수 있도록)
                self.main_list = main_list
                self.main_lock = main_lock
                
                # DB 세션 생성 (검색 여부 확인용)
                check_session = self.db.get_session()
                import json
                from database.models import Torrent
                
                added_count = 0
                skipped_has_thumbnail = 0
                skipped_all_searched = 0
                skipped_error = 0
                
                for item in torrent_items:
                    if item.get('is_priority', False):
                        continue  # 우선순위 항목은 이미 priority_list에 있음
                    
                    # 썸네일이 없고, 모든 서버에서 검색이 끝나지 않은 항목만 메인 큐에 추가
                    try:
                        torrent = check_session.get(Torrent, item['id'])
                        if not torrent:
                            skipped_error += 1
                            continue
                        
                        # 이미 썸네일이 있으면 스킵
                        if torrent.thumbnail_url and torrent.thumbnail_url.strip():
                            skipped_has_thumbnail += 1
                            continue
                        
                        # DB에서 이미 탐색한 서버 목록 확인
                        searched_servers = []
                        if torrent.thumbnail_searched_servers:
                            try:
                                searched_servers = json.loads(torrent.thumbnail_searched_servers)
                            except (json.JSONDecodeError, TypeError):
                                searched_servers = []
                        
                        # 제목 형태에 따라 처리 가능한 서버 확인
                        title_text = item.get('title', '') or torrent.title or ''
                        # FC2 패턴 확인
                        fc2_patterns = [
                            r'FC2[-\s]?PPV[-\s]?(\d{6,8})',  # FC2-PPV-숫자
                            r'FC2[-\s]?PPV',                  # FC2-PPV
                            r'FC2PPV',                        # FC2PPV
                            r'FC2\s+PPV'                      # FC2 PPV
                        ]
                        is_fc2 = False
                        if title_text:
                            title_upper = title_text.upper()
                            for pattern in fc2_patterns:
                                if re.search(pattern, title_upper):
                                    is_fc2 = True
                                    break
                        
                        # FC2 항목: FC2PPV, JAVDB, JAVGURU, JAVMOST만 처리 가능
                        # FC2가 아닌 항목: JAVDB, JAVBEE, JAVGURU, JAVMOST만 처리 가능
                        # JAVGURU, JAVMOST는 모든 형태의 제목 검색 가능
                        if is_fc2:
                            all_servers = {'fc2ppv', 'javdb', 'javguru', 'javmost'}  # FC2 항목: FC2PPV, JAVDB, JAVGURU, JAVMOST
                        else:
                            all_servers = {'javdb', 'javbee', 'javguru', 'javmost'}  # FC2가 아닌 항목: JAVDB, JAVBEE, JAVGURU, JAVMOST
                        
                        # 모든 서버에서 검색했으면 스킵
                        # searched_servers를 set으로 변환하여 비교 (대소문자 구분 없이)
                        searched_servers_set = {s.lower() for s in searched_servers} if searched_servers else set()
                        all_servers_lower = {s.lower() for s in all_servers}
                        
                        # 미검색 서버 확인
                        remaining_servers = all_servers_lower - searched_servers_set
                        
                        if not remaining_servers:
                            # 모든 서버에서 검색했는데 썸네일이 없으면 이미지 없음 처리
                            if not torrent.thumbnail_url:
                                if self.db_writer:
                                    self.db_writer.update_thumbnail(item['id'], '')
                                else:
                                    torrent.thumbnail_url = ''
                                    check_session.commit()
                            skipped_all_searched += 1
                            continue
                        
                        # 썸네일이 없고, 모든 서버에서 검색이 끝나지 않은 항목만 메인 리스트에 추가
                        main_list.append({'item': item, 'processed': False, 'processing_by': None})
                        added_count += 1
                    except Exception as e:
                        # 오류 발생 시 스킵 (메인 큐에 추가하지 않음)
                        skipped_error += 1
                        if skipped_error <= 10:  # 처음 10개만 출력
                            print(f"[썸네일] 초기화 시 DB 확인 오류 (ID: {item.get('id')}): {e}")
                
                check_session.close()
                
                print(f"[썸네일] 메인 리스트 초기화 완료: 추가 {added_count}개, 스킵(썸네일 있음) {skipped_has_thumbnail}개, 스킵(모든 서버 검색 완료) {skipped_all_searched}개, 스킵(오류) {skipped_error}개")
                
                # 디버깅: 메인 리스트의 첫 번째 항목과 DB 상태 출력
                unprocessed_main = [x for x in main_list if not x['processed']]
                if unprocessed_main:
                    try:
                        first_entry = unprocessed_main[0]
                        first_item = first_entry['item']
                        
                        # 첫 번째 항목의 DB 상태 확인
                        debug_session = self.db.get_session()
                        try:
                            debug_torrent = debug_session.get(Torrent, first_item['id'])
                            if debug_torrent:
                                searched_servers_debug = []
                                if debug_torrent.thumbnail_searched_servers:
                                    try:
                                        searched_servers_debug = json.loads(debug_torrent.thumbnail_searched_servers)
                                    except (json.JSONDecodeError, TypeError):
                                        searched_servers_debug = []
                                
                                # 제목 형태에 따라 처리 가능한 서버 확인
                                title_text_debug = first_item.get('title', '') or debug_torrent.title or ''
                                fc2_patterns_debug = [
                                    r'FC2[-\s]?PPV[-\s]?(\d{6,8})',  # FC2-PPV-숫자
                                    r'FC2[-\s]?PPV',                  # FC2-PPV
                                    r'FC2PPV',                        # FC2PPV
                                    r'FC2\s+PPV'                      # FC2 PPV
                                ]
                                is_fc2_debug = False
                                if title_text_debug:
                                    title_upper_debug = title_text_debug.upper()
                                    for pattern in fc2_patterns_debug:
                                        if re.search(pattern, title_upper_debug):
                                            is_fc2_debug = True
                                            break
                                
                                # FC2 항목: FC2PPV, JAVDB, JAVGURU만 처리 가능
                                # FC2가 아닌 항목: JAVDB, JAVBEE, JAVGURU만 처리 가능
                                # JAVGURU, JAVMOST는 모든 형태의 제목 검색 가능
                                if is_fc2_debug:
                                    all_servers_debug = {'fc2ppv', 'javdb', 'javguru', 'javmost'}  # FC2 항목: FC2PPV, JAVDB, JAVGURU, JAVMOST
                                else:
                                    all_servers_debug = {'javdb', 'javbee', 'javguru', 'javmost'}  # FC2가 아닌 항목: JAVDB, JAVBEE, JAVGURU, JAVMOST
                                
                                remaining_servers_debug = all_servers_debug - set(searched_servers_debug)
                                
                                print("=" * 80)
                                print(f"[디버깅] 일반 대기열 첫 번째 항목 정보:")
                                print(f"  ID: {first_item['id']}")
                                print(f"  제목: {first_item.get('title', '') or debug_torrent.title or '(제목 없음)'}")
                                print(f"  FC2 항목 여부: {is_fc2_debug}")
                                print(f"  처리 가능한 검색 서버: {sorted(all_servers_debug)}")
                                print(f"  DB에서 이미 검색한 서버: {sorted(searched_servers_debug) if searched_servers_debug else '(없음)'}")
                                print(f"  아직 검색하지 않은 서버: {sorted(remaining_servers_debug) if remaining_servers_debug else '(없음)'}")
                                print(f"  모든 서버 검색 완료 여부: {set(searched_servers_debug) == all_servers_debug}")
                                print(f"  DB 썸네일 URL: {debug_torrent.thumbnail_url or '(없음)'}")
                                unprocessed_main_count = len([x for x in main_list if not x['processed']])
                                unprocessed_priority_count = len([x for x in priority_list if not x['processed']])
                                print(f"  일반 대기열 크기: {len(main_list)} (미처리: {unprocessed_main_count}개)")
                                print(f"  우선순위 대기열 크기: {len(priority_list)} (미처리: {unprocessed_priority_count}개)")
                                print(f"  thumbnail_searched_servers 원본: {debug_torrent.thumbnail_searched_servers or '(없음)'}")
                                print("=" * 80)
                            else:
                                print(f"[디버깅] 일반 대기열 첫 번째 항목 (ID: {first_item['id']}) - DB에서 찾을 수 없음")
                        finally:
                            debug_session.close()
                    except Exception as e:
                        print(f"[디버깅] 일반 대기열 첫 번째 항목 확인 오류: {e}")
                
                # 서버별 재시도 큐 생성 (한 서버에서 못 찾으면 다른 서버로)
                # 현재 활성화된 서버: fc2ppv, javbee
                # 각 서버 큐는 최대 2개까지만 유지 (나머지는 메인 큐에서 대기)
                server_queues = {
                    # 'missav': queue.Queue(),  # 비활성화
                    # 'javlibrary': queue.Queue(),  # 비활성화
                    'javdb': queue.Queue(),  # JAVDB 서버 큐
                    'fc2ppv': queue.Queue(),  # FC2 재시도 큐
                    'javbee': queue.Queue(),  # JAVBee 서버 큐
                    'javguru': queue.Queue(),  # JAV.GURU 서버 큐 (모든 형태의 제목 검색 가능)
                    'javmost': queue.Queue(),  # JAVMOST 서버 큐 (모든 형태의 제목 검색 가능)
                    # 'nyaa': queue.Queue()  # Sukebei Nyaa 서버 큐 (비활성화)
                }
                
                # 서버 큐에 항목 추가 (최대 2개까지만)
                def put_to_server_queue(server_name: str, item: dict):
                    """서버 큐에 항목 추가 (최대 2개까지만 유지)"""
                    if server_name not in server_queues:
                        return False
                    q = server_queues[server_name]
                    if q.qsize() < 2:
                        q.put(item)
                        return True
                    else:
                        # 큐가 가득 차면 스킵 (메인 큐로 보내지 않음 - 무한 루프 방지)
                        # 메인 큐에서 항목을 가져올 때 이미 서버 큐로 보낼 수 있는 항목은 보내고,
                        # 서버 큐가 가득 차면 그냥 스킵하면 됨
                        return False
                
                # update_priority_ids에서 접근할 수 있도록 저장
                # 리스트는 이미 위에서 설정했으므로 여기서는 server_queues만 설정
                self.server_queues = server_queues
                
                # 토렌트별 처리 상태 추적
                torrent_status = {}  # {torrent_id: {'found': bool, 'thumbnail_url': str, 'tried_servers': set}}
                status_lock = threading.Lock()
                
                # 스레드별 상태 추적 (모든 스레드 상태 모니터링용)
                thread_status = {}  # {server_name: {'processed': int, 'found': int, 'blocked': bool, 'thread_id': int}}
                thread_status_lock = threading.Lock()
                
                # 완료된 토렌트 추적 (진행 상황 계산용)
                completed_torrents = set()  # 완료된 torrent_id 집합
                completed_lock = threading.Lock()
                
                # ImageFinder 공유 인스턴스 생성 (서버별로 하나씩)
                from scrapers.image_finder import ImageFinder
                shared_finder = ImageFinder()
                
                # 서버별 검색 함수 (각 서버 전용)
                def search_server_specific(title: str, server: str, exclude_hosts: list = None, finder_instance=None):
                    """특정 서버에서만 검색"""
                    import re
                    finder = finder_instance if finder_instance else ImageFinder()
                    
                    codes = finder._extract_codes(title)
                    
                    # FC2 코드 추출 (FC2 관련 제목인지 확인)
                    fc2_codes = []
                    is_fc2_title = False
                    pattern_fc2 = r'FC2[-\s]?PPV[-\s]?(\d{6,8})'
                    matches = re.findall(pattern_fc2, title.upper())
                    if matches:
                        fc2_codes = matches[:1]
                        is_fc2_title = True
                    
                    # FC2 관련 제목인지 추가 확인 (FC2PPV, FC2 PPV 등)
                    if not is_fc2_title:
                        fc2_patterns = [
                            r'FC2[-\s]?PPV',
                            r'FC2PPV',
                            r'FC2\s+PPV'
                        ]
                        for pattern in fc2_patterns:
                            if re.search(pattern, title.upper()):
                                is_fc2_title = True
                                # 숫자만 추출
                                num_match = re.search(r'(\d{6,8})', title)
                                if num_match:
                                    fc2_codes = [num_match.group(1)]
                                break
                    
                    image_urls = []
                    
                    # FC2 공식 사이트 검색 (FC2 관련 제목에서만, 최우선)
                    if is_fc2_title and fc2_codes and server == 'fc2ppv':
                        for fc2_code in fc2_codes:
                            fc2_result = finder._search_fc2_adult_contents(fc2_code)
                            if fc2_result.get('thumbnail'):
                                image_urls.append(fc2_result['thumbnail'])
                                # 스냅샷도 저장할 수 있지만 현재는 썸네일만
                                if image_urls:
                                    break
                    
                    # 1단계: 작품번호로 검색
                    # JAVDB는 FC2 항목도 처리 가능, JAVBEE는 FC2가 아닌 경우만
                    if server == 'javdb':
                        # JAVDB는 FC2 항목도 처리 가능
                        if codes:
                            for code in codes:
                                urls = finder._search_javdb(code)
                                image_urls.extend(urls)
                                if image_urls:
                                    break
                        else:
                            # 코드가 없으면 제목으로 검색 (로그 출력 안 함 - 결과가 없으면 반복 검색 방지)
                            urls = finder._search_javdb(title)
                            image_urls.extend(urls)
                    elif not is_fc2_title:
                        # JAVBEE는 FC2가 아닌 경우만
                        if server == 'javbee':
                            if codes:
                                for code in codes:
                                    urls = finder._search_javbee(code)
                                    image_urls.extend(urls)
                                    if image_urls:
                                        break
                            else:
                                # 코드가 없으면 제목으로 검색
                                urls = finder._search_javbee(title)
                                image_urls.extend(urls)
                    
                    # JAV.GURU 검색 (모든 형태의 제목 검색 가능)
                    if server == 'javguru':
                        if codes:
                            for code in codes:
                                urls = finder._search_javguru(code)
                                image_urls.extend(urls)
                                if image_urls:
                                    break
                        else:
                            # 코드가 없으면 전체 제목으로 검색
                            urls = finder._search_javguru(title)
                            image_urls.extend(urls)
                    
                    # JAVMOST 검색 (모든 형태의 제목 검색 가능)
                    if server == 'javmost':
                        if codes:
                            for code in codes:
                                urls = finder._search_javmost(code)
                                image_urls.extend(urls)
                                if image_urls:
                                    break
                        else:
                            # 코드가 없으면 전체 제목으로 검색
                            urls = finder._search_javmost(title)
                            image_urls.extend(urls)
                    
                    # Sukebei Nyaa 검색 (비활성화)
                    # if server == 'nyaa':
                    #     if codes:
                    #         for code in codes:
                    #             urls = finder._search_nyaa(code)
                    #             image_urls.extend(urls)
                    #             if image_urls:
                    #                 break
                    #     else:
                    #         # 코드가 없으면 전체 제목으로 검색
                    #         urls = finder._search_nyaa(title)
                    #         image_urls.extend(urls)
                    
                    # FC2PPV.stream 검색 (백업용, FC2 공식 사이트에서 못 찾은 경우)
                    if is_fc2_title and fc2_codes and server == 'fc2ppv' and not image_urls:
                        for fc2_code in fc2_codes:
                            urls = finder._search_fc2ppv_stream(fc2_code)
                            image_urls.extend(urls)
                            if image_urls:
                                break
                    
                    # 2단계: 작품번호로 못 찾았으면 (또는 작품번호가 없으면) 전체 제목으로 검색
                    if not image_urls:
                        # missav, javlibrary, javdb 비활성화
                        # if server == 'missav':
                        #     from config import ENABLE_SELENIUM_FOR_IMAGES
                        #     if ENABLE_SELENIUM_FOR_IMAGES:
                        #         urls = finder._search_missav_selenium(title)
                        #         image_urls.extend(urls)
                        # elif server == 'javlibrary':
                        #     from config import ENABLE_SELENIUM_FOR_IMAGES
                        #     if ENABLE_SELENIUM_FOR_IMAGES:
                        #         urls = finder._search_javlibrary_selenium(title)
                        #         image_urls.extend(urls)
                        #     if not image_urls:
                        #         urls = finder._search_javdatabase(title)
                        #         image_urls.extend(urls)
                        # elif server == 'javdb':
                        #     from config import ENABLE_SELENIUM_FOR_IMAGES
                        #     if ENABLE_SELENIUM_FOR_IMAGES:
                        #         urls = finder._search_javdb_selenium(title)
                        #         image_urls.extend(urls)
                        #     else:
                        #         urls = finder._search_javdb(title)
                        #         image_urls.extend(urls)
                        if server == 'javbee':
                            urls = finder._search_javbee(title)
                            image_urls.extend(urls)
                        # FC2는 작품번호로만 검색 (전체 제목 검색 안 함)
                    
                    # 필터링
                    def _filter_urls(urls):
                        filtered = []
                        for u in urls:
                            if not u or finder._is_blocked_thumbnail(u):
                                continue
                            if exclude_hosts:
                                try:
                                    from urllib.parse import urlparse
                                    host = urlparse(u).netloc.lower()
                                    if any((ex or '').lower() in host for ex in exclude_hosts):
                                        continue
                                except:
                                    pass
                            if u not in filtered:
                                filtered.append(u)
                        return filtered
                    
                    # 필터링 전 URL 개수
                    before_filter_count = len(image_urls)
                    
                    image_urls = _filter_urls(image_urls)
                    # 필터링 후 URL 개수
                    after_filter_count = len(image_urls)
                    
                    # 디버그: JAVLibrary에서 이미지를 찾았다고 로그가 나왔는데 필터링 후 비어있는 경우
                    if server == 'javlibrary' and before_filter_count > 0 and after_filter_count == 0:
                        # 필터링된 URL 중 하나를 상세 분석
                        if before_filter_count > 0:
                            sample_url = image_urls[0] if len(image_urls) > 0 else (list(image_urls)[0] if hasattr(image_urls, '__iter__') else None)
                            if sample_url:
                                print(f"[{server.upper()}] 필터링된 URL 샘플: {sample_url[:150]}")
                                print(f"[{server.upper()}] _is_blocked_thumbnail 결과: {finder._is_blocked_thumbnail(sample_url) if hasattr(finder, '_is_blocked_thumbnail') else 'N/A'}")
                    
                    return image_urls[0] if image_urls else ''
                
                # 서버별 스레드 실행 함수
                def server_worker(priority_list: list, priority_lock: threading.Lock, main_list: list, main_lock: threading.Lock, server_name: str, other_queues: list):
                    """서버별 워커 스레드 (각 서버당 1개씩만 실행, 병렬 처리)
                    리스트 기반: 항목을 가져올 때는 삭제, 서버 큐로 옮길 때도 삭제, 추가할 때는 append
                    """
                    nonlocal updated_count  # 외부 스코프의 updated_count에 접근
                    import time
                    from scrapers.image_finder import ImageFinder
                    thread_id = threading.current_thread().ident
                    print(f"[{server_name.upper()}] 워커 스레드 시작됨 (Thread ID: {thread_id})")
                    
                    # 각 스레드마다 독립적인 ImageFinder 인스턴스 생성 (javdb_available 상태 추적용)
                    thread_finder = ImageFinder()
                    
                    # 스레드 상태 초기화
                    with thread_status_lock:
                        thread_status[server_name] = {
                            'processed': 0,
                            'found': 0,
                            'blocked': False,
                            'thread_id': thread_id
                        }
                    
                    # 스레드별 처리 통계
                    processed_count = 0  # 처리한 항목 수
                    found_count = 0  # 찾은 항목 수
                    last_status_time = time.time()
                    
                    # 서버 차단 감지용 변수
                    consecutive_no_found = 0  # 연속으로 찾지 못한 횟수
                    server_blocked = False  # 서버가 차단되었는지 여부 (403 에러만 차단으로 처리)
                    
                    # DB 세션 (항목 가져오기 전에 검색 여부 확인용)
                    check_session = self.db.get_session()
                    from database.models import Torrent
                    import json
                    
                    # FC2 패턴 (FC2-PPV-숫자, FC2-PPV, FC2PPV, FC2 PPV 모두 포함)
                    fc2_patterns = [
                        r'FC2[-\s]?PPV[-\s]?(\d{6,8})',  # FC2-PPV-숫자
                        r'FC2[-\s]?PPV',                  # FC2-PPV
                        r'FC2PPV',                        # FC2PPV
                        r'FC2\s+PPV'                      # FC2 PPV
                    ]
                    
                    def is_fc2_title(title: str) -> bool:
                        """제목이 FC2 관련인지 확인 (FC2-PPV-숫자, FC2-PPV, FC2PPV, FC2 PPV 모두 포함)"""
                        if not title:
                            return False
                        title_upper = title.upper()
                        for pattern in fc2_patterns:
                            if re.search(pattern, title_upper):
                                return True
                        return False
                    
                    def check_item_before_process(temp_item):
                        """항목을 가져오기 전에 DB에서 검색 여부 확인"""
                        if not temp_item:
                            return None, None
                        
                        temp_item_id = temp_item.get('id')
                        if not temp_item_id:
                            return None, None
                        
                        try:
                            torrent = check_session.get(Torrent, temp_item_id)
                            if not torrent:
                                return None, None
                            
                            # 이미 썸네일이 있으면 스킵 (단, force_replace 플래그가 있으면 검색)
                            if torrent.thumbnail_url and torrent.thumbnail_url.strip():
                                # force_replace 플래그가 있으면 썸네일이 있어도 검색 (교체 기능)
                                if not temp_item.get('force_replace', False):
                                    return None, 'has_thumbnail'
                            
                            # DB에서 이미 탐색한 서버 목록 확인
                            searched_servers = []
                            if torrent.thumbnail_searched_servers:
                                try:
                                    searched_servers = json.loads(torrent.thumbnail_searched_servers)
                                except (json.JSONDecodeError, TypeError):
                                    searched_servers = []
                            
                            # 제목 형태에 따라 처리 가능한 서버 확인
                            title_text = temp_item.get('title', '') or torrent.title or ''
                            is_fc2 = is_fc2_title(title_text)
                            
                            # FC2 항목: FC2PPV, JAVDB, JAVGURU, JAVMOST만 처리 가능
                            # FC2가 아닌 항목: JAVDB, JAVBEE, JAVGURU, JAVMOST만 처리 가능
                            # JAVGURU, JAVMOST는 모든 형태의 제목 검색 가능
                            if is_fc2:
                                all_servers = {'fc2ppv', 'javdb', 'javguru', 'javmost'}  # FC2 항목: FC2PPV, JAVDB, JAVGURU, JAVMOST
                            else:
                                all_servers = {'javdb', 'javbee', 'javguru', 'javmost'}  # FC2가 아닌 항목: JAVDB, JAVBEE, JAVGURU, JAVMOST
                            
                            # 이미 이 서버에서 탐색했으면 다른 서버 확인
                            if server_name in searched_servers:
                                # 이미 이 서버에서 검색했지만, 다른 서버에서 검색하지 않았으면 다른 서버로 보내기
                                remaining_servers = all_servers - set(searched_servers)
                                if remaining_servers:
                                    # 아직 검색하지 않은 서버가 있으면 해당 서버 큐로 보내기
                                    sent_to_queue = False
                                    for other_server in remaining_servers:
                                        if other_server in server_queues:
                                            if put_to_server_queue(other_server, temp_item):
                                                sent_to_queue = True
                                    # 실제로 서버 큐로 보냈으면 None 반환, 아니면 항목 반환 (서버 큐가 가득 찬 경우)
                                    if sent_to_queue:
                                        return None, 'sent_to_other_servers'
                                    else:
                                        # 서버 큐가 가득 차서 보내지 못한 경우, 현재 서버에서도 처리 불가하므로
                                        # 항목을 유지하되 다른 상태로 반환 (다른 서버가 처리할 수 있도록)
                                        return temp_item, 'queue_full_keep_item'
                                else:
                                    # 모든 서버에서 검색했는데 썸네일이 없으면 이미지 없음 처리
                                    if not torrent.thumbnail_url:
                                        if self.db_writer:
                                            self.db_writer.update_thumbnail(temp_item_id, '')
                                        else:
                                            torrent.thumbnail_url = ''
                                            check_session.commit()
                                        return None, 'all_servers_searched'
                                    # 모든 서버에서 검색했고 썸네일이 있으면 스킵
                                    return None, 'already_searched'
                            
                            # 모든 서버에서 검색했는데 썸네일이 없으면 이미지 없음 처리
                            if set(searched_servers) == all_servers and not torrent.thumbnail_url:
                                # 모든 서버에서 검색했는데 찾지 못했으면 이미지 없음 처리
                                if self.db_writer:
                                    self.db_writer.update_thumbnail(temp_item_id, '')
                                else:
                                    torrent.thumbnail_url = ''
                                    check_session.commit()
                                return None, 'all_servers_searched'
                            
                            # 현재 서버가 아직 안 본 항목이면 현재 서버에서 먼저 처리
                            if server_name not in searched_servers:
                                # 이 서버에서 검색 가능
                                return temp_item, 'ok'
                            
                            # 현재 서버가 이미 본 항목이고, 다른 서버에서 검색 안 했으면 해당 서버 큐로 보내기
                            remaining_servers = all_servers - set(searched_servers) - {server_name}
                            if remaining_servers:
                                # 아직 검색하지 않은 서버가 있으면 해당 서버 큐로 보내기
                                sent_to_queue = False
                                for other_server in remaining_servers:
                                    if other_server in server_queues:
                                        if put_to_server_queue(other_server, temp_item):
                                            sent_to_queue = True
                                # 서버 큐로 보냈으면 스킵 (다시 메인 큐에 넣지 않음)
                                if sent_to_queue:
                                    return None, 'sent_to_other_servers'
                                # 서버 큐가 가득 차서 메인 큐에 남아있으면 queue_full_keep_item으로 반환하여 마킹되지 않도록 함
                                return temp_item, 'queue_full_keep_item'
                            
                            # 이 서버에서 검색 가능 (이미 위에서 처리되지만 안전을 위해)
                            return temp_item, 'ok'
                        except Exception as e:
                            print(f"[{server_name.upper()}] DB 확인 오류: {e}")
                            return temp_item, 'ok'  # 오류 발생 시 그대로 처리
                    
                    # 디버그: check_item_before_process에서 스킵되는 항목 로그 (처음 몇 개만)
                    _check_skip_debug_count = {}
                    
                    while not self._stop_requested:
                        item = None
                        used_queue = None
                        
                        # 서버 큐가 2개보다 작으면 우선순위 리스트 먼저 확인, 그 다음 메인 리스트에서 항목을 가져와서 채우기 (최대 2개까지)
                        if server_name in server_queues:
                            queue_size = server_queues[server_name].qsize()
                            max_queue_size = 2
                            
                            if queue_size < max_queue_size:
                                found_count = 0
                                max_find = max_queue_size - queue_size  # 최대 2개까지 채우기
                                
                                # 우선순위 리스트 먼저 확인 (우선순위가 높으므로)
                                with priority_lock:
                                    unprocessed_priority = [x for x in priority_list if not x['processed']]
                                    for entry in unprocessed_priority[:]:  # 복사본으로 순회
                                        if found_count >= max_find:
                                            break
                                        temp_item = entry['item']
                                        # DB에서 검색 여부 확인
                                        checked_item, status = check_item_before_process(temp_item)
                                        
                                        if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                            entry['processed'] = True
                                            continue
                                        elif status == 'sent_to_other_servers':
                                            # 실제로 큐에 들어갔는지 확인 (checked_item이 None이면 큐에 들어감)
                                            if checked_item is None:
                                                entry['processed'] = True
                                            continue
                                        elif status == 'queue_full_keep_item':
                                            # 큐가 가득 찬 경우 스킵 (마킹하지 않음)
                                            continue
                                        elif checked_item is None:
                                            entry['processed'] = True
                                            continue
                                        
                                        # status == 'ok'인 경우만 처리
                                        if status != 'ok':
                                            continue
                                        
                                        temp_item = checked_item
                                        # 현재 서버에서 처리 가능한 항목인지 확인
                                        is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                        
                                        if server_name == 'javdb':
                                            # JAVDB는 모든 항목 처리 가능
                                            if put_to_server_queue(server_name, temp_item):
                                                entry['processed'] = True
                                                found_count += 1
                                        elif server_name == 'javbee' and not is_fc2:
                                            # JAVBEE는 FC2가 아닌 항목만 처리 가능
                                            if put_to_server_queue(server_name, temp_item):
                                                entry['processed'] = True
                                                found_count += 1
                                        elif server_name == 'fc2ppv' and is_fc2:
                                            # FC2PPV는 FC2 항목만 처리 가능
                                            if put_to_server_queue(server_name, temp_item):
                                                entry['processed'] = True
                                                found_count += 1
                                        elif server_name == 'javguru':
                                            # JAVGURU는 모든 형태의 제목 검색 가능
                                            if put_to_server_queue(server_name, temp_item):
                                                entry['processed'] = True
                                                found_count += 1
                                        elif server_name == 'javmost':
                                            # JAVMOST는 모든 형태의 제목 검색 가능
                                            if put_to_server_queue(server_name, temp_item):
                                                entry['processed'] = True
                                                found_count += 1
                                        # elif server_name == 'nyaa':  # NYAA 서버 비활성화
                                        #     # NYAA는 모든 형태의 품번 검색 가능
                                        #     if put_to_server_queue(server_name, temp_item):
                                        #         entry['processed'] = True
                                        #         found_count += 1
                                
                                # 우선순위 리스트에서 못 찾았으면 메인 리스트에서 확인 (리스트 전체 확인)
                                if found_count < max_find:
                                    with main_lock:
                                        unprocessed_main = [x for x in main_list if not x['processed']]
                                        for entry in unprocessed_main[:]:  # 복사본으로 순회
                                            if found_count >= max_find:
                                                break
                                            temp_item = entry['item']
                                            # DB에서 검색 여부 확인
                                            checked_item, status = check_item_before_process(temp_item)
                                            
                                            if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                                entry['processed'] = True
                                                continue
                                            elif status == 'sent_to_other_servers':
                                                # 실제로 큐에 들어갔는지 확인 (checked_item이 None이면 큐에 들어감)
                                                if checked_item is None:
                                                    entry['processed'] = True
                                                continue
                                            elif status == 'queue_full_keep_item':
                                                # 큐가 가득 찬 경우 스킵 (마킹하지 않음)
                                                continue
                                            elif checked_item is None:
                                                entry['processed'] = True
                                                continue
                                            
                                            # status == 'ok'인 경우만 처리
                                            if status != 'ok':
                                                continue
                                            
                                            temp_item = checked_item
                                            # 현재 서버에서 처리 가능한 항목인지 확인
                                            is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                            
                                            if server_name == 'javdb':
                                                # JAVDB는 모든 항목 처리 가능
                                                if put_to_server_queue(server_name, temp_item):
                                                    entry['processed'] = True
                                                    found_count += 1
                                            elif server_name == 'javbee' and not is_fc2:
                                                # JAVBEE는 FC2가 아닌 항목만 처리 가능
                                                if put_to_server_queue(server_name, temp_item):
                                                    entry['processed'] = True
                                                    found_count += 1
                                            elif server_name == 'fc2ppv' and is_fc2:
                                                # FC2PPV는 FC2 항목만 처리 가능
                                                if put_to_server_queue(server_name, temp_item):
                                                    entry['processed'] = True
                                                    found_count += 1
                                            elif server_name == 'javguru':
                                                # JAVGURU는 모든 형태의 제목 검색 가능
                                                if put_to_server_queue(server_name, temp_item):
                                                    entry['processed'] = True
                                                    found_count += 1
                                            elif server_name == 'javmost':
                                                # JAVMOST는 모든 형태의 제목 검색 가능
                                                if put_to_server_queue(server_name, temp_item):
                                                    entry['processed'] = True
                                                    found_count += 1
                                            elif server_name == 'nyaa':
                                                # NYAA는 모든 형태의 품번 검색 가능
                                                if put_to_server_queue(server_name, temp_item):
                                                    entry['processed'] = True
                                                    found_count += 1
                                
                                # 디버그: 서버 큐를 채운 경우 로그 출력
                                if found_count > 0:
                                    print(f"[{server_name.upper()}] 서버 큐에 {found_count}개 항목 추가 (큐 크기: {queue_size} → {server_queues[server_name].qsize()})")
                        
                        # FC2 서버는 우선순위/일반 큐에서 FC2 항목만 가져오기
                        if server_name == 'fc2ppv':
                            # 재시도 큐 먼저 확인
                            try:
                                temp_item = server_queues['fc2ppv'].get_nowait()
                                # DB에서 검색 여부 확인
                                checked_item, status = check_item_before_process(temp_item)
                                
                                if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                    # 스킵 (이미 처리됨)
                                    item = None
                                elif status == 'sent_to_other_servers':
                                    # 다른 서버로 보냄
                                    item = None
                                elif status == 'queue_full_keep_item':
                                    # 서버 큐가 가득 차서 보내지 못했지만, 다른 서버가 처리할 수 있으므로 스킵
                                    item = None
                                elif checked_item is None:
                                    # 처리 불가
                                    item = None
                                else:
                                    item = checked_item
                                used_queue = server_queues['fc2ppv']
                                # print(f"[{server_name.upper()}] 재시도 큐에서 항목 가져옴: {item.get('title', '')[:50]}")
                            except queue.Empty:
                                item = None
                            
                            if item is None:
                                # 우선순위 리스트에서 FC2 항목 찾기
                                temp_items = []
                                found_fc2 = False
                                with priority_lock:
                                    unprocessed_priority = [x for x in priority_list if not x['processed']]
                                    for entry in unprocessed_priority[:]:  # 복사본으로 순회
                                        temp_item = entry['item']
                                        # DB에서 검색 여부 확인
                                        checked_item, status = check_item_before_process(temp_item)
                                        
                                        if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                            # 스킵 (이미 처리됨) - 마킹
                                            entry['processed'] = True
                                            continue
                                        elif status == 'sent_to_other_servers':
                                            # 다른 서버로 보냄 - 마킹
                                            entry['processed'] = True
                                            continue
                                        elif status == 'queue_full_keep_item':
                                            # 서버 큐가 가득 차서 보내지 못했지만, 다른 서버가 처리할 수 있으므로 항목 유지 (마킹하지 않음)
                                            # 큐 용량이 가득 찬 경우 0.1초 쉬고 다시 검토
                                            time.sleep(0.1)
                                            continue
                                        elif checked_item is None:
                                            # 처리 불가 (torrent가 없거나 예외 발생) - 마킹
                                            entry['processed'] = True
                                            continue
                                        
                                        temp_item = checked_item
                                        if is_fc2_title(temp_item.get('title', '')):
                                            item = temp_item
                                            entry['processed'] = True
                                            found_fc2 = True
                                            break
                                        else:
                                            temp_items.append(temp_item)
                                
                                # FC2가 아닌 항목은 다시 우선순위 리스트로 (이미 리스트에 있으므로 마킹만 해제)
                                # temp_items는 이미 리스트에 있으므로 별도 처리 불필요
                                
                                # 우선순위 리스트에서 못 찾았으면 메인 리스트에서 찾기
                                if not found_fc2:
                                    temp_items = []
                                    # 메인 리스트에서 FC2 항목 찾기
                                    with main_lock:
                                        unprocessed_main = [x for x in main_list if not x['processed']]
                                        for entry in unprocessed_main[:]:  # 복사본으로 순회
                                            temp_item = entry['item']
                                            # DB에서 검색 여부 확인
                                            checked_item, status = check_item_before_process(temp_item)
                                            
                                            if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                                # 스킵 (이미 처리됨) - 마킹
                                                entry['processed'] = True
                                                continue
                                            elif status == 'sent_to_other_servers':
                                                # 다른 서버로 보냄 - 마킹
                                                entry['processed'] = True
                                                continue
                                            elif status == 'queue_full_keep_item':
                                                # 서버 큐가 가득 차서 보내지 못했지만, 다른 서버가 처리할 수 있으므로 항목 유지 (마킹하지 않음)
                                                continue
                                            elif checked_item is None:
                                                # 처리 불가 (torrent가 없거나 예외 발생) - 마킹
                                                entry['processed'] = True
                                                continue
                                            
                                            temp_item = checked_item
                                            if is_fc2_title(temp_item.get('title', '')):
                                                item = temp_item
                                                entry['processed'] = True
                                                found_fc2 = True
                                                break
                                            else:
                                                temp_items.append((None, temp_item))  # source_queue는 None으로 표시
                                    
                                    # 다른 서버 큐에서도 확인
                                    for source_queue in other_queues:
                                        while not source_queue.empty():
                                            try:
                                                temp_item = source_queue.get_nowait()
                                                # DB에서 검색 여부 확인
                                                checked_item, status = check_item_before_process(temp_item)
                                                
                                                if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                                    # 스킵 (이미 처리됨) - 다시 큐에 넣지 않음
                                                    continue
                                                elif status == 'sent_to_other_servers':
                                                    # 다른 서버로 보냄 - 다시 큐에 넣지 않음
                                                    continue
                                                elif status == 'queue_full_keep_item':
                                                    # 서버 큐가 가득 차서 보내지 못했지만, 다른 서버가 처리할 수 있으므로 다시 큐에 넣기
                                                    # 큐 용량이 가득 찬 경우 0.1초 쉬고 다시 검토
                                                    time.sleep(0.1)
                                                    source_queue.put(temp_item)
                                                    continue
                                                elif checked_item is None:
                                                    # 처리 불가 (torrent가 없거나 예외 발생) - 다시 큐에 넣지 않음
                                                    continue
                                                
                                                temp_item = checked_item
                                                if is_fc2_title(temp_item.get('title', '')):
                                                    item = temp_item
                                                    used_queue = source_queue
                                                    found_fc2 = True
                                                    break
                                                else:
                                                    temp_items.append((source_queue, temp_item))
                                            except queue.Empty:
                                                break
                                        if found_fc2:
                                            break
                                    
                                    # FC2가 아닌 항목은 스킵 (다시 메인 큐에 넣지 않음)
                                    # 메인 큐는 처리 대기 큐이지 확인 대기 큐가 아님
                            
                            if item is None:
                                # FC2PPV 서버 큐가 비어있고 처리할 항목이 없으면 메인 큐에서 FC2 관련 항목 찾기
                                if server_queues['fc2ppv'].empty():
                                    # 메인 리스트에서 PPV가 포함된 항목 찾기 (예: 4092-PPV461, 4017-PPV147)
                                    found_count_for_queue = 0
                                    max_items_to_find = 2  # 최대 2개까지 큐에 추가
                                    # 메인 리스트에서 확인
                                    with main_lock:
                                        unprocessed_main = [x for x in main_list if not x['processed']]
                                        checked_count = 0
                                        max_check = 200  # 더 많이 확인
                                        for entry in unprocessed_main[:]:  # 복사본으로 순회
                                            if checked_count >= max_check or found_count_for_queue >= max_items_to_find:
                                                break
                                            temp_item = entry['item']
                                            checked_count += 1
                                            
                                            # DB에서 검색 여부 확인
                                            checked_item, status = check_item_before_process(temp_item)
                                            
                                            if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                                entry['processed'] = True
                                                continue
                                            elif status == 'sent_to_other_servers':
                                                entry['processed'] = True
                                                continue
                                            elif checked_item is None:
                                                entry['processed'] = True
                                                continue
                                            
                                            temp_item = checked_item
                                            # PPV가 포함된 항목인지 확인
                                            if 'PPV' in temp_item.get('title', '').upper():
                                                if put_to_server_queue('fc2ppv', temp_item):
                                                    entry['processed'] = True
                                                    found_count_for_queue += 1
                                    
                                    # 다른 서버 큐에서도 확인
                                    for source_queue in other_queues:
                                        temp_items_checked = []
                                        checked_count = 0
                                        max_check = 200  # 더 많이 확인
                                        while checked_count < max_check and not source_queue.empty() and found_count_for_queue < max_items_to_find:
                                            try:
                                                temp_item = source_queue.get_nowait()
                                                checked_count += 1
                                                
                                                # DB에서 검색 여부 확인
                                                checked_item, status = check_item_before_process(temp_item)
                                                
                                                if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                                    continue
                                                elif status == 'sent_to_other_servers':
                                                    continue
                                                elif checked_item is None:
                                                    continue
                                                
                                                temp_item = checked_item
                                                title_text = temp_item.get('title', '') or ''
                                                
                                                # PPV가 포함된 항목인지 확인 (예: 4092-PPV461, 4017-PPV147, FC2-PPV-1234567)
                                                if title_text:
                                                    title_upper = title_text.upper()
                                                    # PPV 패턴 확인: 숫자-PPV숫자 또는 FC2-PPV 또는 PPV가 포함된 항목
                                                    ppv_pattern = re.compile(r'\d{3,5}[-\s]?PPV\d{3,6}|FC2[-\s]?PPV|PPV', re.I)
                                                    if ppv_pattern.search(title_upper):
                                                        # FC2PPV 큐에 추가 (최대 2개)
                                                        if put_to_server_queue('fc2ppv', temp_item):
                                                            found_count_for_queue += 1
                                                            if found_count_for_queue >= max_items_to_find:
                                                                break
                                                        # 서버 큐로 보냈거나 메인 큐로 보냈으므로 다시 넣지 않음
                                                        continue
                                                
                                                temp_items_checked.append((source_queue, temp_item))
                                            except queue.Empty:
                                                break
                                        
                                        # 확인한 항목 중 처리하지 않은 것들은 스킵 (다시 메인 큐에 넣지 않음)
                                        # 메인 큐는 처리 대기 큐이지 확인 대기 큐가 아님
                                        
                                        if found_count_for_queue >= max_items_to_find:
                                            break
                                    
                                    # 항목을 찾았으면 바로 다시 루프 시작하여 처리
                                    if found_count_for_queue > 0:
                                        continue
                                
                                # FC2 항목을 찾지 못했으면 잠시 대기 후 재시도
                                time.sleep(0.1)
                                # 주기적으로 상태 출력 (10초마다)
                                current_time = time.time()
                                if current_time - last_status_time >= 10.0:
                                    print(f"[{server_name.upper()}] 스레드 대기 중: 처리 {processed_count}개, 발견 {found_count}개 (Thread ID: {thread_id})")
                                    last_status_time = current_time
                                continue
                        else:
                            # 다른 서버는 자신의 큐를 먼저 확인
                            # JAVDB는 FC2 항목도 처리 가능, JAVBEE는 FC2가 아닌 항목만
                            item = None
                            temp_items = []  # 처리 가능한 항목 임시 보관 (초기화)
                            
                            # 자신의 큐 먼저 확인
                            if server_name in server_queues:
                                try:
                                    temp_item = server_queues[server_name].get_nowait()
                                    # DB에서 검색 여부 확인
                                    checked_item, status = check_item_before_process(temp_item)
                                    
                                    if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                        # 스킵 (이미 처리됨)
                                        item = None
                                    elif status == 'sent_to_other_servers':
                                        # 다른 서버로 보냄
                                        item = None
                                    elif status == 'queue_full_keep_item':
                                        # 서버 큐가 가득 차서 보내지 못했지만, 다른 서버가 처리할 수 있으므로 스킵
                                        item = None
                                    elif checked_item is None:
                                        # 처리 불가
                                        item = None
                                    else:
                                        # status == 'ok'인 경우 처리
                                        if status == 'ok':
                                            temp_item = checked_item
                                            is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                            
                                            # 현재 서버에서 처리 가능한지 확인
                                            if server_name == 'javdb':
                                                # JAVDB는 모든 항목 처리 가능
                                                item = temp_item
                                                used_queue = server_queues[server_name]
                                            elif server_name == 'javbee' and not is_fc2:
                                                # JAVBEE는 FC2가 아닌 항목만 처리 가능
                                                item = temp_item
                                                used_queue = server_queues[server_name]
                                            elif server_name == 'javguru':
                                                # JAVGURU는 모든 형태의 제목 검색 가능
                                                item = temp_item
                                                used_queue = server_queues[server_name]
                                            elif server_name == 'javmost':
                                                # JAVMOST는 모든 형태의 제목 검색 가능
                                                item = temp_item
                                                used_queue = server_queues[server_name]
                                            # elif server_name == 'nyaa':  # NYAA 서버 비활성화
                                            #     # NYAA는 모든 형태의 품번 검색 가능
                                            #     item = temp_item
                                            #     used_queue = server_queues[server_name]
                                            else:
                                                # 현재 서버에서 처리 불가
                                                item = None
                                        else:
                                            item = None
                                except queue.Empty:
                                    item = None
                            
                            # 자신의 큐에서 항목을 찾지 못했으면 우선순위 리스트와 메인 리스트 확인
                            if item is None:
                                temp_items = []  # 처리 가능한 항목 임시 보관
                                
                                # 우선순위 리스트에서 항목 찾기
                                with priority_lock:
                                    # 주기적으로 processed 항목 제거 (100개 이상일 때만, 성능 최적화)
                                    if len(priority_list) > 100:
                                        priority_list[:] = [x for x in priority_list if not x['processed']]
                                    unprocessed_priority = [x for x in priority_list if not x['processed']]
                                    for entry in unprocessed_priority[:]:  # 복사본으로 순회
                                        temp_item = entry['item']
                                        # DB에서 검색 여부 확인
                                        checked_item, status = check_item_before_process(temp_item)
                                        
                                        if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                            # 스킵 (이미 처리됨) - 마킹
                                            entry['processed'] = True
                                            continue
                                        elif status == 'sent_to_other_servers':
                                            # 다른 서버로 보냈지만 서버 큐가 가득 차서 우선순위 리스트에 남아있을 수 있음
                                            # checked_item이 None이면 서버 큐로 보냈으므로 마킹
                                            if checked_item is None:
                                                entry['processed'] = True
                                                continue
                                            # checked_item이 있으면 서버 큐가 가득 차서 우선순위 리스트에 남아있는 경우
                                            # 현재 서버에서도 처리 가능하면 즉시 처리 (우선순위 리스트이므로)
                                            temp_item = checked_item
                                            is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                            
                                            if server_name == 'javdb':
                                                # JAVDB는 모든 항목 처리 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'priority_list'
                                                # 디버그: 우선순위 큐에서 항목을 가져왔을 때 로그
                                                if not hasattr(server_worker, '_priority_debug_count'):
                                                    server_worker._priority_debug_count = {}
                                                if server_name not in server_worker._priority_debug_count:
                                                    server_worker._priority_debug_count[server_name] = 0
                                                if server_worker._priority_debug_count[server_name] < 5:
                                                    priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                    print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴 (sent_to_other_servers): {item.get('title', '')[:50]}")
                                                    server_worker._priority_debug_count[server_name] += 1
                                                break  # 우선순위 리스트에서 찾았으면 즉시 처리
                                            elif server_name == 'javbee' and not is_fc2:
                                                # JAVBEE는 FC2가 아닌 항목만 처리 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'priority_list'
                                                # 디버그: 우선순위 큐에서 항목을 가져왔을 때 로그
                                                if not hasattr(server_worker, '_priority_debug_count'):
                                                    server_worker._priority_debug_count = {}
                                                if server_name not in server_worker._priority_debug_count:
                                                    server_worker._priority_debug_count[server_name] = 0
                                                if server_worker._priority_debug_count[server_name] < 5:
                                                    priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                    print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴 (sent_to_other_servers): {item.get('title', '')[:50]}")
                                                    server_worker._priority_debug_count[server_name] += 1
                                                break  # 우선순위 리스트에서 찾았으면 즉시 처리
                                            elif server_name == 'fc2ppv' and is_fc2:
                                                # FC2PPV는 FC2 항목만 처리 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'priority_list'
                                                # 디버그: 우선순위 큐에서 항목을 가져왔을 때 로그
                                                if not hasattr(server_worker, '_priority_debug_count'):
                                                    server_worker._priority_debug_count = {}
                                                if server_name not in server_worker._priority_debug_count:
                                                    server_worker._priority_debug_count[server_name] = 0
                                                if server_worker._priority_debug_count[server_name] < 5:
                                                    priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                    print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴 (sent_to_other_servers): {item.get('title', '')[:50]}")
                                                    server_worker._priority_debug_count[server_name] += 1
                                                break  # 우선순위 리스트에서 찾았으면 즉시 처리
                                            elif server_name == 'javguru':
                                                # JAVGURU는 모든 형태의 제목 검색 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'priority_list'
                                                # 디버그: 우선순위 큐에서 항목을 가져왔을 때 로그
                                                if not hasattr(server_worker, '_priority_debug_count'):
                                                    server_worker._priority_debug_count = {}
                                                if server_name not in server_worker._priority_debug_count:
                                                    server_worker._priority_debug_count[server_name] = 0
                                                if server_worker._priority_debug_count[server_name] < 5:
                                                    priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                    print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴 (sent_to_other_servers): {item.get('title', '')[:50]}")
                                                    server_worker._priority_debug_count[server_name] += 1
                                                break  # 우선순위 리스트에서 찾았으면 즉시 처리
                                            elif server_name == 'javmost':
                                                # JAVMOST는 모든 형태의 제목 검색 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'priority_list'
                                                # 디버그: 우선순위 큐에서 항목을 가져왔을 때 로그
                                                if not hasattr(server_worker, '_priority_debug_count'):
                                                    server_worker._priority_debug_count = {}
                                                if server_name not in server_worker._priority_debug_count:
                                                    server_worker._priority_debug_count[server_name] = 0
                                                if server_worker._priority_debug_count[server_name] < 5:
                                                    priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                    print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴 (sent_to_other_servers): {item.get('title', '')[:50]}")
                                                    server_worker._priority_debug_count[server_name] += 1
                                                break  # 우선순위 리스트에서 찾았으면 즉시 처리
                                            # elif server_name == 'nyaa':  # NYAA 서버 비활성화
                                            #     # NYAA는 모든 형태의 품번 검색 가능
                                            #     item = temp_item
                                            #     entry['processed'] = True
                                            #     used_queue = 'priority_list'
                                                # 디버그: 우선순위 큐에서 항목을 가져왔을 때 로그
                                                if not hasattr(server_worker, '_priority_debug_count'):
                                                    server_worker._priority_debug_count = {}
                                                if server_name not in server_worker._priority_debug_count:
                                                    server_worker._priority_debug_count[server_name] = 0
                                                if server_worker._priority_debug_count[server_name] < 5:
                                                    priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                    print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴 (sent_to_other_servers): {item.get('title', '')[:50]}")
                                                    server_worker._priority_debug_count[server_name] += 1
                                                break  # 우선순위 리스트에서 찾았으면 즉시 처리
                                            # 현재 서버에서 처리 불가하면 마킹하지 않고 다음 항목 확인 (다른 서버가 처리할 수 있음)
                                            continue
                                        elif status == 'queue_full_keep_item':
                                            # 서버 큐가 가득 차서 보내지 못했지만, 다른 서버가 처리할 수 있으므로 항목 유지 (마킹하지 않음)
                                            # 큐 용량이 가득 찬 경우 0.1초 쉬고 다시 검토
                                            time.sleep(0.1)
                                            continue
                                        elif checked_item is None:
                                            # 처리 불가 (torrent가 없거나 예외 발생) - 마킹하지 않고 다음 항목 확인
                                            continue
                                        
                                        # status == 'ok'인 경우만 처리 (queue_full_keep_item은 이미 위에서 처리됨)
                                        if status == 'ok':
                                            temp_item = checked_item
                                            temp_item_id = temp_item.get('id')
                                            is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                            
                                            # 우선순위 리스트에서 항목을 찾으면 즉시 처리 (메인 리스트보다 우선)
                                            # JAVDB는 FC2 항목도 처리 가능
                                            if server_name == 'javdb':
                                                # JAVDB는 모든 항목 처리 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'priority_list'
                                                # 디버그: 우선순위 큐에서 항목을 가져왔을 때 로그
                                                if not hasattr(server_worker, '_priority_debug_count'):
                                                    server_worker._priority_debug_count = {}
                                                if server_name not in server_worker._priority_debug_count:
                                                    server_worker._priority_debug_count[server_name] = 0
                                                if server_worker._priority_debug_count[server_name] < 5:
                                                    priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                    print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴: {item.get('title', '')[:50]}")
                                                    server_worker._priority_debug_count[server_name] += 1
                                                break  # 우선순위 리스트에서 찾았으면 즉시 처리
                                            elif is_fc2:
                                                # JAVBEE는 FC2 항목 처리 불가
                                                # FC2PPV 서버 큐로 넘기기 (최대 2개)
                                                if not put_to_server_queue('fc2ppv', temp_item):
                                                    # 서버 큐가 가득 차서 메인 큐로 보내졌으면 다시 메인 큐에 넣지 않음
                                                    pass
                                                entry['processed'] = True
                                            elif server_name == 'javbee' and not is_fc2:
                                                # JAVBEE는 FC2가 아닌 항목만 처리 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'priority_list'
                                                # 디버그: 우선순위 큐에서 항목을 가져왔을 때 로그
                                                if not hasattr(server_worker, '_priority_debug_count'):
                                                    server_worker._priority_debug_count = {}
                                                if server_name not in server_worker._priority_debug_count:
                                                    server_worker._priority_debug_count[server_name] = 0
                                                if server_worker._priority_debug_count[server_name] < 5:
                                                    priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                    print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴: {item.get('title', '')[:50]}")
                                                    server_worker._priority_debug_count[server_name] += 1
                                                break  # 우선순위 리스트에서 찾았으면 즉시 처리
                                            elif server_name == 'javguru':
                                                # JAVGURU는 모든 형태의 제목 검색 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'priority_list'
                                                # 디버그: 우선순위 큐에서 항목을 가져왔을 때 로그
                                                if not hasattr(server_worker, '_priority_debug_count'):
                                                    server_worker._priority_debug_count = {}
                                                if server_name not in server_worker._priority_debug_count:
                                                    server_worker._priority_debug_count[server_name] = 0
                                                if server_worker._priority_debug_count[server_name] < 5:
                                                    priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                    print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴: {item.get('title', '')[:50]}")
                                                    server_worker._priority_debug_count[server_name] += 1
                                                break  # 우선순위 리스트에서 찾았으면 즉시 처리
                                            elif server_name == 'javmost':
                                                # JAVMOST는 모든 형태의 제목 검색 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'priority_list'
                                                # 디버그: 우선순위 큐에서 항목을 가져왔을 때 로그
                                                if not hasattr(server_worker, '_priority_debug_count'):
                                                    server_worker._priority_debug_count = {}
                                                if server_name not in server_worker._priority_debug_count:
                                                    server_worker._priority_debug_count[server_name] = 0
                                                if server_worker._priority_debug_count[server_name] < 5:
                                                    priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                    print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴: {item.get('title', '')[:50]}")
                                                    server_worker._priority_debug_count[server_name] += 1
                                                break  # 우선순위 리스트에서 찾았으면 즉시 처리
                                            # elif server_name == 'nyaa':  # NYAA 서버 비활성화
                                            #     # NYAA는 모든 형태의 품번 검색 가능
                                            #     item = temp_item
                                            #     entry['processed'] = True
                                            #     used_queue = 'priority_list'
                                                # 디버그: 우선순위 큐에서 항목을 가져왔을 때 로그
                                                if not hasattr(server_worker, '_priority_debug_count'):
                                                    server_worker._priority_debug_count = {}
                                                if server_name not in server_worker._priority_debug_count:
                                                    server_worker._priority_debug_count[server_name] = 0
                                                if server_worker._priority_debug_count[server_name] < 5:
                                                    priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                    print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴: {item.get('title', '')[:50]}")
                                                    server_worker._priority_debug_count[server_name] += 1
                                                break  # 우선순위 리스트에서 찾았으면 즉시 처리
                                            elif server_name == 'fc2ppv' and is_fc2:
                                                # FC2PPV는 FC2 항목만 처리 가능
                                                # FC2PPV 서버 큐로 넘기기 (최대 2개)
                                                if not put_to_server_queue('fc2ppv', temp_item):
                                                    # 서버 큐가 가득 차서 메인 큐로 보내졌으면 다시 메인 큐에 넣지 않음
                                                    pass
                                                entry['processed'] = True
                            
                            # 우선순위 리스트에서 못 찾았으면 메인 리스트 확인
                            if item is None:
                                # 메인 리스트를 처음부터 검색하며 가장 먼저 나오는 처리 가능한 항목 가져오기
                                with main_lock:
                                    unprocessed_main = [x for x in main_list if not x['processed']]
                                    for entry in unprocessed_main:  # 처음부터 순회
                                        temp_item = entry['item']
                                        
                                        # DB에서 검색 여부 확인
                                        checked_item, status = check_item_before_process(temp_item)
                                        
                                        # 디버그: 처음 몇 개만 로그 출력
                                        if not hasattr(server_worker, '_main_list_debug_count'):
                                            server_worker._main_list_debug_count = {}
                                        if server_name not in server_worker._main_list_debug_count:
                                            server_worker._main_list_debug_count[server_name] = 0
                                        if server_worker._main_list_debug_count[server_name] < 5:
                                            # print(f"[{server_name.upper()}] 메인 리스트 항목 확인: ID={temp_item.get('id')}, status={status}, checked_item={'있음' if checked_item else 'None'}")
                                            server_worker._main_list_debug_count[server_name] += 1
                                        
                                        if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                            # 스킵 (이미 처리됨) - 마킹
                                            entry['processed'] = True
                                            continue
                                        elif status is None:
                                            # status가 None이면 처리 불가 - 마킹하지 않고 다음 항목 확인
                                            continue
                                        elif status == 'sent_to_other_servers':
                                            # 다른 서버로 보냈지만 서버 큐가 가득 차서 메인 리스트에 남아있을 수 있음
                                            # checked_item이 None이면 서버 큐로 보냈으므로 마킹
                                            if checked_item is None:
                                                entry['processed'] = True
                                                continue
                                            # checked_item이 있으면 서버 큐가 가득 차서 메인 리스트에 남아있는 경우
                                            # 현재 서버에서도 처리 가능하면 처리, 아니면 마킹하지 않고 다음 항목 확인
                                            temp_item = checked_item
                                            is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                            
                                            if server_name == 'javdb':
                                                # JAVDB는 모든 항목 처리 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'main_list'
                                                break
                                            elif server_name == 'javbee' and not is_fc2:
                                                # JAVBEE는 FC2가 아닌 항목만 처리 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'main_list'
                                                break
                                            elif server_name == 'javguru':
                                                # JAVGURU는 모든 형태의 제목 검색 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'main_list'
                                                break
                                            elif server_name == 'javmost':
                                                # JAVMOST는 모든 형태의 제목 검색 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'main_list'
                                                break
                                            # elif server_name == 'nyaa':  # NYAA 서버 비활성화
                                            #     # NYAA는 모든 형태의 품번 검색 가능
                                            #     item = temp_item
                                            #     entry['processed'] = True
                                            #     used_queue = 'main_list'
                                            #     break
                                            # 현재 서버에서 처리 불가하면 마킹하지 않고 다음 항목 확인 (다른 서버가 처리할 수 있음)
                                            continue
                                        elif status == 'queue_full_keep_item':
                                            # 서버 큐가 가득 차서 보내지 못했지만, 다른 서버가 처리할 수 있으므로 항목 유지 (마킹하지 않음)
                                            # 이 상태는 이미 검색한 서버가 다른 서버 큐로 보내려고 했는데 큐가 가득 찬 경우이므로,
                                            # 현재 서버는 이미 검색했으므로 처리할 수 없음
                                            continue
                                        elif checked_item is None:
                                            # 처리 불가 (torrent가 없거나 예외 발생) - 마킹하지 않고 다음 항목 확인
                                            continue
                                        
                                        # status == 'ok'인 경우만 처리 (queue_full_keep_item은 이미 위에서 처리됨)
                                        if status == 'ok':
                                            temp_item = checked_item
                                            is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                            
                                            # JAVDB는 FC2 항목도 처리 가능
                                            if server_name == 'javdb':
                                                # JAVDB는 모든 항목 처리 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'main_list'
                                                break
                                            elif server_name == 'javbee' and not is_fc2:
                                                # JAVBEE는 FC2가 아닌 항목만 처리 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'main_list'
                                                break
                                            elif server_name == 'fc2ppv' and is_fc2:
                                                # FC2PPV는 FC2 항목만 처리 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'main_list'
                                                break
                                            elif server_name == 'javguru':
                                                # JAVGURU는 모든 형태의 제목 검색 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'main_list'
                                                break
                                            elif server_name == 'javmost':
                                                # JAVMOST는 모든 형태의 제목 검색 가능
                                                item = temp_item
                                                entry['processed'] = True
                                                used_queue = 'main_list'
                                                break
                                            # 현재 서버에서 처리 불가하면 마킹하지 않고 다음 항목 확인 (다른 서버가 처리할 수 있음)
                                            continue
                                
                                # 다른 서버 큐에서도 확인 (메인 리스트에서 못 찾은 경우만)
                                if item is None:
                                    for source_queue in other_queues:
                                        while not source_queue.empty():
                                            try:
                                                temp_item = source_queue.get_nowait()
                                                
                                                # DB에서 검색 여부 확인
                                                checked_item, status = check_item_before_process(temp_item)
                                                
                                                if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                                    # 스킵 (이미 처리됨) - 다시 큐에 넣지 않음
                                                    continue
                                                elif status == 'sent_to_other_servers':
                                                    # 다른 서버로 보냈지만 서버 큐가 가득 차서 메인 큐에 남아있을 수 있음
                                                    # checked_item이 None이면 서버 큐로 보냈으므로 스킵
                                                    if checked_item is None:
                                                        continue
                                                    # checked_item이 있으면 서버 큐가 가득 차서 메인 큐에 남아있는 경우
                                                    # 현재 서버에서도 처리 가능하면 처리
                                                    temp_item = checked_item
                                                    is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                                    
                                                    if server_name == 'javdb':
                                                        # JAVDB는 모든 항목 처리 가능
                                                        item = temp_item
                                                        used_queue = source_queue
                                                        break
                                                    elif server_name == 'javbee' and not is_fc2:
                                                        # JAVBEE는 FC2가 아닌 항목만 처리 가능
                                                        item = temp_item
                                                        used_queue = source_queue
                                                        break
                                                    # 현재 서버에서 처리 불가하면 다시 큐에 넣기
                                                    source_queue.put(temp_item)
                                                    continue
                                                elif status == 'queue_full_keep_item':
                                                    # 서버 큐가 가득 차서 보내지 못했지만, 다른 서버가 처리할 수 있으므로 다시 큐에 넣기
                                                    # 큐 용량이 가득 찬 경우 0.1초 쉬고 다시 검토
                                                    time.sleep(0.1)
                                                    source_queue.put(temp_item)
                                                    continue
                                                elif checked_item is None:
                                                    # 처리 불가 (torrent가 없거나 예외 발생) - 스킵
                                                    continue
                                                
                                                temp_item = checked_item
                                                is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                                
                                                # JAVDB는 FC2 항목도 처리 가능
                                                if server_name == 'javdb':
                                                    # JAVDB는 모든 항목 처리 가능
                                                    item = temp_item
                                                    used_queue = source_queue
                                                    break  # 항목을 찾았으면 중단
                                                elif server_name == 'javbee':
                                                    if is_fc2:
                                                        # JAVBEE는 FC2 항목 처리 불가
                                                        # FC2PPV 서버 큐로 넘기기 (최대 2개)
                                                        if not put_to_server_queue('fc2ppv', temp_item):
                                                            # 서버 큐가 가득 차면 다시 큐에 넣기
                                                            source_queue.put(temp_item)
                                                    else:
                                                        # FC2가 아닌 항목은 JAVBEE도 처리 가능
                                                        item = temp_item
                                                        used_queue = source_queue
                                                        break  # 항목을 찾았으면 중단
                                                elif server_name == 'fc2ppv':
                                                    if is_fc2:
                                                        # FC2PPV는 FC2 항목만 처리 가능
                                                        item = temp_item
                                                        used_queue = source_queue
                                                        break  # 항목을 찾았으면 중단
                                                    else:
                                                        # FC2가 아닌 항목은 FC2PPV가 처리 불가
                                                        # 다시 큐에 넣기
                                                        source_queue.put(temp_item)
                                                elif server_name == 'javguru':
                                                    # JAVGURU는 모든 형태의 제목 검색 가능
                                                    item = temp_item
                                                    used_queue = source_queue
                                                    break  # 항목을 찾았으면 중단
                                                elif server_name == 'javmost':
                                                    # JAVMOST는 모든 형태의 제목 검색 가능
                                                    item = temp_item
                                                    used_queue = source_queue
                                                    break  # 항목을 찾았으면 중단
                                                else:
                                                    # 알 수 없는 서버 - 다시 큐에 넣기
                                                    source_queue.put(temp_item)
                                            except queue.Empty:
                                                break
                                    
                                        if item is not None:
                                            break  # 항목을 찾았으면 다른 큐 확인 중단
                                
                                # 소스 큐도 비어있으면 우선순위 리스트를 다시 확인 (짧은 대기 후)
                                if item is None:
                                    # JAVDB는 모든 항목 처리 가능, JAVBEE는 FC2가 아닌 항목만
                                    max_attempts = 100  # 최대 100번 시도
                                    for attempt in range(max_attempts):
                                        time.sleep(0.1)  # timeout 대신 sleep
                                        with priority_lock:
                                            unprocessed_priority = [x for x in priority_list if not x['processed']]
                                            if unprocessed_priority:
                                                entry = unprocessed_priority[0]
                                                temp_item = entry['item']
                                                # DB에서 검색 여부 확인
                                                checked_item, status = check_item_before_process(temp_item)
                                                
                                                if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                                    # 스킵 (이미 처리됨) - 마킹
                                                    entry['processed'] = True
                                                    continue
                                                elif status == 'sent_to_other_servers':
                                                    # 다른 서버로 보냄 - 마킹
                                                    entry['processed'] = True
                                                    continue
                                                elif status == 'queue_full_keep_item':
                                                    # 서버 큐가 가득 차서 보내지 못했지만, 다른 서버가 처리할 수 있으므로 항목 유지 (마킹하지 않음)
                                                    continue
                                                elif checked_item is None:
                                                    # 처리 불가 - 마킹
                                                    entry['processed'] = True
                                                    continue
                                                
                                                # status == 'ok'인 경우만 처리
                                                if status != 'ok':
                                                    continue
                                                
                                                temp_item = checked_item
                                                is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                                
                                                # JAVDB는 FC2 항목도 처리 가능
                                                if server_name == 'javdb':
                                                    # JAVDB는 모든 항목 처리 가능
                                                    item = temp_item
                                                    entry['processed'] = True
                                                    used_queue = 'priority_list'
                                                    if item:
                                                        priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                        print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴 (대기 후): {item.get('title', '')[:50]}")
                                                    break
                                                elif server_name == 'javguru':
                                                    # JAVGURU는 모든 형태의 제목 검색 가능
                                                    item = temp_item
                                                    entry['processed'] = True
                                                    used_queue = 'priority_list'
                                                    if item:
                                                        priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                        print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴 (대기 후): {item.get('title', '')[:50]}")
                                                    break
                                                elif server_name == 'javmost':
                                                    # JAVMOST는 모든 형태의 제목 검색 가능
                                                    item = temp_item
                                                    entry['processed'] = True
                                                    used_queue = 'priority_list'
                                                    if item:
                                                        priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                        print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴 (대기 후): {item.get('title', '')[:50]}")
                                                    break
                                                elif is_fc2:
                                                    # JAVBEE는 FC2 항목 처리 불가
                                                    # FC2PPV 서버 큐로 넘기기 (최대 2개)
                                                    put_to_server_queue('fc2ppv', temp_item)
                                                    entry['processed'] = True
                                                else:
                                                    # FC2가 아닌 항목은 JAVBEE도 처리 가능
                                                    item = temp_item
                                                    entry['processed'] = True
                                                    used_queue = 'priority_list'
                                                    if item:
                                                        priority_mark = "[우선순위] " if item.get('is_priority', False) else ""
                                                        print(f"[{server_name.upper()}] {priority_mark}우선순위 큐에서 항목 가져옴 (대기 후): {item.get('title', '')[:50]}")
                                                    break
                                    
                                    # 우선순위 리스트에서 못 찾았으면 메인 리스트에서 항목 찾기
                                    if item is None:
                                        temp_items = []
                                        # 메인 리스트에서 JAVDB는 모든 항목, JAVBEE는 FC2가 아닌 항목만 찾기 (최대 200개 확인)
                                        with main_lock:
                                            unprocessed_main = [x for x in main_list if not x['processed']]
                                            checked_count = 0
                                            max_check = 200  # FC2 항목이 많을 수 있으므로 더 많이 확인
                                            for entry in unprocessed_main[:]:  # 복사본으로 순회
                                                if checked_count >= max_check:
                                                    break
                                                temp_item = entry['item']
                                                checked_count += 1
                                                
                                                # DB에서 검색 여부 확인
                                                checked_item, status = check_item_before_process(temp_item)
                                                
                                                if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                                    # 스킵 (이미 처리됨) - 마킹
                                                    entry['processed'] = True
                                                    continue
                                                elif status == 'sent_to_other_servers':
                                                    # 다른 서버로 보냈지만 서버 큐가 가득 차서 메인 리스트에 남아있을 수 있음
                                                    if checked_item is None:
                                                        entry['processed'] = True
                                                        continue
                                                    temp_item = checked_item
                                                    is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                                    
                                                    if server_name == 'javdb':
                                                        # JAVDB는 모든 항목 처리 가능
                                                        temp_items.append((None, temp_item))
                                                        entry['processed'] = True
                                                        break
                                                    elif server_name == 'javbee' and not is_fc2:
                                                        # JAVBEE는 FC2가 아닌 항목만 처리 가능
                                                        temp_items.append((None, temp_item))
                                                        entry['processed'] = True
                                                        break
                                                    elif server_name == 'javguru':
                                                        # JAVGURU는 모든 형태의 제목 검색 가능
                                                        temp_items.append((None, temp_item))
                                                        entry['processed'] = True
                                                        break
                                                    elif server_name == 'javmost':
                                                        # JAVMOST는 모든 형태의 제목 검색 가능
                                                        temp_items.append((None, temp_item))
                                                        entry['processed'] = True
                                                        break
                                                    entry['processed'] = True
                                                    continue
                                                elif status == 'queue_full_keep_item':
                                                    # 서버 큐가 가득 차서 보내지 못했지만, 다른 서버가 처리할 수 있으므로 항목 유지 (마킹하지 않음)
                                                    time.sleep(0.1)
                                                    continue
                                                elif checked_item is None:
                                                    # 처리 불가 (torrent가 없거나 예외 발생) - 마킹
                                                    entry['processed'] = True
                                                    continue
                                                
                                                # status == 'ok'인 경우만 처리
                                                if status != 'ok':
                                                    continue
                                                
                                                temp_item = checked_item
                                                is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                                
                                                # JAVDB는 FC2 항목도 처리 가능
                                                if server_name == 'javdb':
                                                    # JAVDB는 모든 항목 처리 가능
                                                    temp_items.append((None, temp_item))
                                                    entry['processed'] = True
                                                    break
                                                elif server_name == 'javbee' and not is_fc2:
                                                    # JAVBEE는 FC2가 아닌 항목만 처리 가능
                                                    temp_items.append((None, temp_item))
                                                    entry['processed'] = True
                                                    break
                                                elif server_name == 'javguru':
                                                    # JAVGURU는 모든 형태의 제목 검색 가능
                                                    temp_items.append((None, temp_item))
                                                    entry['processed'] = True
                                                    break
                                                elif server_name == 'javmost':
                                                    # JAVMOST는 모든 형태의 제목 검색 가능
                                                    temp_items.append((None, temp_item))
                                                    entry['processed'] = True
                                                    break
                                        
                                        # 다른 서버 큐에서도 확인
                                        for source_queue in other_queues:
                                            checked_count = 0
                                            max_check = 200  # FC2 항목이 많을 수 있으므로 더 많이 확인
                                            while checked_count < max_check and not source_queue.empty():
                                                try:
                                                    temp_item = source_queue.get_nowait()
                                                    checked_count += 1
                                                    
                                                    # DB에서 검색 여부 확인
                                                    checked_item, status = check_item_before_process(temp_item)
                                                    
                                                    if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                                        # 스킵 (이미 처리됨) - 다시 큐에 넣지 않음
                                                        continue
                                                    elif status == 'sent_to_other_servers':
                                                        # check_item_before_process에서 이미 다른 서버로 보냄
                                                        # checked_item이 None이면 서버 큐로 보냈으므로 스킵
                                                        if checked_item is None:
                                                            continue
                                                        # checked_item이 있으면 서버 큐가 가득 차서 메인 큐에 남아있는 경우
                                                        # 현재 서버에서도 처리 가능하면 처리
                                                        temp_item = checked_item
                                                        is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                                        
                                                        # 현재 서버에서 처리 가능한지 확인
                                                        if server_name == 'javdb':
                                                            # JAVDB는 모든 항목 처리 가능
                                                            item = temp_item
                                                            used_queue = source_queue
                                                            break
                                                        elif server_name == 'javbee' and not is_fc2:
                                                            # JAVBEE는 FC2가 아닌 항목만 처리 가능
                                                            item = temp_item
                                                            used_queue = source_queue
                                                            break
                                                        elif server_name == 'fc2ppv' and is_fc2:
                                                            # FC2PPV는 FC2 항목만 처리 가능
                                                            item = temp_item
                                                            used_queue = source_queue
                                                            break
                                                        elif server_name == 'javguru':
                                                            # JAVGURU는 모든 형태의 제목 검색 가능
                                                            item = temp_item
                                                            used_queue = source_queue
                                                            break
                                                        elif server_name == 'javmost':
                                                            # JAVMOST는 모든 형태의 제목 검색 가능
                                                            item = temp_item
                                                            used_queue = source_queue
                                                            break
                                                        # 현재 서버에서 처리 불가하면 다시 메인 큐에 넣기
                                                        source_queue.put(temp_item)
                                                        continue
                                                    elif checked_item is None:
                                                        # 처리 불가 - 스킵
                                                        continue
                                                    
                                                    # status == 'ok'인 경우
                                                    temp_item = checked_item
                                                    is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                                    
                                                    # JAVDB는 FC2 항목도 처리 가능
                                                    if server_name == 'javdb':
                                                        # JAVDB는 모든 항목 처리 가능
                                                        item = temp_item
                                                        used_queue = source_queue
                                                        break
                                                    elif server_name == 'javbee':
                                                        if not is_fc2:
                                                            # FC2가 아닌 항목은 JAVBEE도 처리 가능
                                                            item = temp_item
                                                            used_queue = source_queue
                                                            break
                                                        else:
                                                            # JAVBEE는 FC2 항목 처리 불가 - 다시 메인 큐에 넣기
                                                            source_queue.put(temp_item)
                                                            continue
                                                    elif server_name == 'fc2ppv':
                                                        if is_fc2:
                                                            # FC2 항목은 FC2PPV가 처리 가능
                                                            item = temp_item
                                                            used_queue = source_queue
                                                            break
                                                        else:
                                                            # FC2PPV는 FC2가 아닌 항목 처리 불가 - 다시 메인 큐에 넣기
                                                            source_queue.put(temp_item)
                                                            continue
                                                    elif server_name == 'javguru':
                                                        # JAVGURU는 모든 형태의 제목 검색 가능
                                                        item = temp_item
                                                        used_queue = source_queue
                                                        break
                                                    elif server_name == 'javmost':
                                                        # JAVMOST는 모든 형태의 제목 검색 가능
                                                        item = temp_item
                                                        used_queue = source_queue
                                                        break
                                                    # elif server_name == 'nyaa':  # NYAA 서버 비활성화
                                                    #     # NYAA는 모든 형태의 품번 검색 가능
                                                    #     item = temp_item
                                                    #     used_queue = source_queue
                                                    #     break
                                                    else:
                                                        # 알 수 없는 서버 - 다시 메인 큐에 넣기
                                                        source_queue.put(temp_item)
                                                        continue
                                                except queue.Empty:
                                                    break
                                            if item:
                                                break
                            
                            if item is None:
                                # 처리할 항목이 없고 자신의 큐가 2개보다 작으면 메인 큐에서 처리 가능한 항목 찾아서 자신의 큐에 넣기 (최대 2개까지)
                                if server_name in server_queues:
                                    queue_size = server_queues[server_name].qsize()
                                    max_queue_size = 2
                                    
                                    if queue_size < max_queue_size:
                                        found_count = 0
                                        max_find = max_queue_size - queue_size  # 최대 2개까지 채우기
                                        
                                        # 메인 리스트에서 확인
                                        with main_lock:
                                            unprocessed_main = [x for x in main_list if not x['processed']]
                                            for entry in unprocessed_main[:]:  # 복사본으로 순회
                                                if found_count >= max_find:
                                                    break
                                                    
                                                temp_item = entry['item']
                                                # DB에서 검색 여부 확인
                                                checked_item, status = check_item_before_process(temp_item)
                                                
                                                if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                                    entry['processed'] = True
                                                    continue
                                                elif status == 'sent_to_other_servers':
                                                    # 실제로 큐에 들어갔는지 확인 (checked_item이 None이면 큐에 들어감)
                                                    if checked_item is None:
                                                        entry['processed'] = True
                                                    continue
                                                elif status == 'queue_full_keep_item':
                                                    # 큐가 가득 찬 경우 스킵 (마킹하지 않음)
                                                    continue
                                                elif checked_item is None:
                                                    entry['processed'] = True
                                                    continue
                                                
                                                # status == 'ok'인 경우만 처리
                                                if status != 'ok':
                                                    continue
                                                
                                                temp_item = checked_item
                                                # 현재 서버에서 처리 가능한 항목인지 확인
                                                is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                                
                                                if server_name == 'javdb':
                                                    # JAVDB는 모든 항목 처리 가능
                                                    if put_to_server_queue(server_name, temp_item):
                                                        entry['processed'] = True
                                                        found_count += 1
                                                elif server_name == 'javbee' and not is_fc2:
                                                    # JAVBEE는 FC2가 아닌 항목만 처리 가능
                                                    if put_to_server_queue(server_name, temp_item):
                                                        entry['processed'] = True
                                                        found_count += 1
                                                elif server_name == 'fc2ppv' and is_fc2:
                                                    # FC2PPV는 FC2 항목만 처리 가능
                                                    if put_to_server_queue(server_name, temp_item):
                                                        entry['processed'] = True
                                                        found_count += 1
                                                elif server_name == 'javguru':
                                                    # JAVGURU는 모든 형태의 제목 검색 가능
                                                    if put_to_server_queue(server_name, temp_item):
                                                        entry['processed'] = True
                                                        found_count += 1
                                                elif server_name == 'javmost':
                                                    # JAVMOST는 모든 형태의 제목 검색 가능
                                                    if put_to_server_queue(server_name, temp_item):
                                                        entry['processed'] = True
                                                        found_count += 1
                                    
                                    # 다른 서버 큐에서도 확인 (서버 큐가 아직 2개 미만인 경우만)
                                    if queue_size < max_queue_size:
                                        for source_queue in other_queues:
                                            temp_items_checked = []
                                            checked_count = 0
                                            max_check = 100  # 최대 100개 확인
                                            while checked_count < max_check and not source_queue.empty() and found_count < max_find:
                                                try:
                                                    temp_item = source_queue.get_nowait()
                                                    checked_count += 1
                                                    
                                                    # DB에서 검색 여부 확인
                                                    checked_item, status = check_item_before_process(temp_item)
                                                    
                                                    if status == 'has_thumbnail' or status == 'already_searched' or status == 'all_servers_searched':
                                                        continue
                                                    elif status == 'sent_to_other_servers':
                                                        continue
                                                    elif status == 'queue_full_keep_item':
                                                        # 큐가 가득 찬 경우 다시 큐에 넣기
                                                        source_queue.put(temp_item)
                                                        continue
                                                    elif checked_item is None:
                                                        continue
                                                    
                                                    # status == 'ok'인 경우만 처리
                                                    if status != 'ok':
                                                        source_queue.put(temp_item)
                                                        continue
                                                    
                                                    temp_item = checked_item
                                                    is_fc2 = is_fc2_title(temp_item.get('title', ''))
                                                    
                                                    # JAVDB는 모든 항목 처리 가능
                                                    if server_name == 'javdb':
                                                        # JAVDB 큐에 추가 (최대 2개)
                                                        if put_to_server_queue('javdb', temp_item):
                                                            found_count += 1
                                                        # 서버 큐가 가득 차면 더 이상 추가하지 않음
                                                        if found_count >= max_find:
                                                            break
                                                    # JAVBEE는 FC2가 아닌 항목만 처리 가능
                                                    elif server_name == 'javbee' and not is_fc2:
                                                        # JAVBEE 큐에 추가 (최대 2개)
                                                        if put_to_server_queue('javbee', temp_item):
                                                            found_count += 1
                                                        # 서버 큐가 가득 차면 더 이상 추가하지 않음
                                                        if found_count >= max_find:
                                                            break
                                                    # FC2PPV는 FC2 항목만 처리 가능
                                                    elif server_name == 'fc2ppv' and is_fc2:
                                                        # FC2PPV 큐에 추가 (최대 2개)
                                                        if put_to_server_queue('fc2ppv', temp_item):
                                                            found_count += 1
                                                        # 서버 큐가 가득 차면 더 이상 추가하지 않음
                                                        if found_count >= max_find:
                                                            break
                                                    # JAVGURU는 모든 형태의 제목 검색 가능
                                                    elif server_name == 'javguru':
                                                        # JAVGURU 큐에 추가 (최대 2개)
                                                        if put_to_server_queue('javguru', temp_item):
                                                            found_count += 1
                                                        # 서버 큐가 가득 차면 더 이상 추가하지 않음
                                                        if found_count >= max_find:
                                                            break
                                                    # JAVMOST는 모든 형태의 제목 검색 가능
                                                    elif server_name == 'javmost':
                                                        # JAVMOST 큐에 추가 (최대 2개)
                                                        if put_to_server_queue('javmost', temp_item):
                                                            found_count += 1
                                                        # 서버 큐가 가득 차면 더 이상 추가하지 않음
                                                        if found_count >= max_find:
                                                            break
                                                    else:
                                                        # 처리하지 않은 항목만 다시 큐로
                                                        source_queue.put(temp_item)
                                                except queue.Empty:
                                                    break
                                            
                                            # 서버 큐가 가득 차면 더 이상 확인하지 않음
                                            if found_count >= max_find:
                                                break
                                    
                                    # 모든 큐가 비어있으면 잠시 대기 후 재시도
                                    time.sleep(0.1)
                                    # 주기적으로 상태 출력 (10초마다)
                                    current_time = time.time()
                                    if current_time - last_status_time >= 10.0:
                                        print(f"[{server_name.upper()}] 스레드 대기 중: 처리 {processed_count}개, 발견 {found_count}개 (Thread ID: {thread_id})")
                                        last_status_time = current_time
                                    continue  # 다시 while 루프 시작
                            
                            # 디버그 로그 제거 (찾은 경우만 출력)
                        
                        if item is None:
                            # 모든 큐가 비어있으면 종료
                            print(f"[{server_name.upper()}] 스레드 종료: 총 처리 {processed_count}개, 발견 {found_count}개 (Thread ID: {thread_id})")
                            break
                        
                        torrent_id = item['id']
                        title = item['title']
                        is_priority = item['is_priority']
                        exclude_hosts = item.get('exclude_hosts', [])
                        
                        # 403 차단 감지: 각 서버별 403 플래그 확인 (실제로 검색을 시도했을 때만)
                        if processed_count > 0:
                            if server_name == 'javdb' and not thread_finder.javdb_available:
                                if not server_blocked:
                                    server_blocked = True
                                    print(f"[{server_name.upper()}] ⚠️ 403 차단 감지: JAVDB 서버 차단으로 인해 스레드 즉시 종료 (Thread ID: {thread_id})")
                                    # 스레드 상태 업데이트
                                    with thread_status_lock:
                                        if server_name in thread_status:
                                            thread_status[server_name]['blocked'] = True
                            elif server_name == 'javbee' and thread_finder.javbee_blocked:
                                if not server_blocked:
                                    server_blocked = True
                                    print(f"[{server_name.upper()}] ⚠️ 403 차단 감지: JAVBEE 서버 차단으로 인해 스레드 즉시 종료 (Thread ID: {thread_id})")
                                    # 스레드 상태 업데이트
                                    with thread_status_lock:
                                        if server_name in thread_status:
                                            thread_status[server_name]['blocked'] = True
                            elif server_name == 'javguru' and thread_finder.javguru_blocked:
                                if not server_blocked:
                                    server_blocked = True
                                    print(f"[{server_name.upper()}] ⚠️ 403 차단 감지: JAVGURU 서버 차단으로 인해 스레드 즉시 종료 (Thread ID: {thread_id})")
                                    # 스레드 상태 업데이트
                                    with thread_status_lock:
                                        if server_name in thread_status:
                                            thread_status[server_name]['blocked'] = True
                            elif server_name == 'javmost' and thread_finder.javmost_blocked:
                                if not server_blocked:
                                    server_blocked = True
                                    print(f"[{server_name.upper()}] ⚠️ 403 차단 감지: JAVMOST 서버 차단으로 인해 스레드 즉시 종료 (Thread ID: {thread_id})")
                                    # 스레드 상태 업데이트
                                    with thread_status_lock:
                                        if server_name in thread_status:
                                            thread_status[server_name]['blocked'] = True
                        
                        # 차단된 서버는 작업 완전 종료
                        if server_blocked:
                            print(f"[{server_name.upper()}] ⚠️ 서버 차단으로 인한 스레드 종료 (처리 {processed_count}개, 발견 {found_count}개) (Thread ID: {thread_id})")
                            # 스레드 상태 최종 업데이트
                            with thread_status_lock:
                                if server_name in thread_status:
                                    thread_status[server_name]['blocked'] = True
                                    thread_status[server_name]['processed'] = processed_count
                                    thread_status[server_name]['found'] = found_count
                            break  # 스레드 완전 종료
                        
                        # 주기적으로 상태 출력은 모니터링 스레드에서 처리 (개별 출력 제거)
                        
                        # check_item_before_process에서 이미 확인했으므로, status_lock은 검색 전에만 빠르게 확인
                        # (다른 스레드에서 방금 찾았을 수 있으므로)
                        skip_reason = None
                        with status_lock:
                            if torrent_id in torrent_status and torrent_status[torrent_id].get('found'):
                                # 이미 찾았으면 스킵
                                skip_reason = 'already_found'
                                with completed_lock:
                                    completed_torrents.add(torrent_id)
                                if used_queue not in ('priority_list', 'main_list') and hasattr(used_queue, 'task_done'):
                                    used_queue.task_done()
                        
                        if skip_reason:
                            # 디버그: 왜 스킵되는지 로그 (처음 몇 개만)
                            if not hasattr(server_worker, '_skip_debug_count'):
                                server_worker._skip_debug_count = {}
                            if server_name not in server_worker._skip_debug_count:
                                server_worker._skip_debug_count[server_name] = 0
                            if server_worker._skip_debug_count[server_name] < 5:
                                print(f"[{server_name.upper()}] 항목 스킵 (ID: {torrent_id}, 이유: {skip_reason}): {title[:50]}")
                                server_worker._skip_debug_count[server_name] += 1
                            continue
                        
                        # status_lock에서 tried_servers 체크는 제거
                        # check_item_before_process에서 이미 DB의 thumbnail_searched_servers를 확인했으므로
                        # 중복 체크는 불필요함
                        
                        try:
                            # 독립적인 세션 생성
                            work_session = self.db.get_session()
                            try:
                                import json
                                from database.models import Torrent
                                torrent = work_session.get(Torrent, torrent_id)
                                if not torrent:
                                    if used_queue != 'priority_list' and hasattr(used_queue, 'task_done'):
                                        used_queue.task_done()
                                    continue
                                
                                # 이미 check_item_before_process에서 확인했으므로 여기서는 바로 검색 진행
                                
                                # 현재 서버에서 검색 (스레드별 ImageFinder 인스턴스 사용)
                                thumbnail_url = search_server_specific(title, server_name, exclude_hosts, finder_instance=thread_finder)
                                
                                # 실제 검색을 시도했으므로 처리 카운트 증가
                                processed_count += 1
                                
                                # 스레드 상태 업데이트 (처리 카운트만 업데이트, 발견 카운트는 썸네일 발견 시 업데이트)
                                with thread_status_lock:
                                    if server_name in thread_status:
                                        thread_status[server_name]['processed'] = processed_count
                                
                                # 탐색한 서버 목록에 추가 (성공/실패 관계없이) - DB에서 최신 상태 가져오기
                                # DB에서 최신 searched_servers 가져오기
                                current_searched_servers = []
                                if torrent.thumbnail_searched_servers:
                                    try:
                                        current_searched_servers = json.loads(torrent.thumbnail_searched_servers)
                                    except (json.JSONDecodeError, TypeError):
                                        current_searched_servers = []
                                
                                # 현재 서버가 아직 추가되지 않았으면 추가
                                if server_name not in current_searched_servers:
                                    current_searched_servers.append(server_name)
                                    if self.db_writer:
                                        # DB_writer를 통해 비동기 저장 (썸네일 URL은 그대로 유지)
                                        self.db_writer.update_thumbnail(torrent_id, torrent.thumbnail_url or '', server_name=server_name)
                                    else:
                                        # DB_writer가 없으면 직접 저장
                                        torrent.thumbnail_searched_servers = json.dumps(current_searched_servers)
                                        work_session.commit()
                                
                                # thumbnail_url이 있으면 카운트해야 하므로, 이미 다른 서버에서 찾았는지 확인은 thumbnail_url 체크 후에
                                if thumbnail_url:
                                    thumbnail_url_lower = thumbnail_url.lower()
                                    
                                    # .ico 파일이 포함되어 있으면 이미지 없음으로 처리하고 다시 큐에 넣기
                                    if '.ico' in thumbnail_url_lower or 'favicon' in thumbnail_url_lower:
                                        print(f"[{server_name.upper()}] ⚠️ .ico 파일 감지, 이미지 없음으로 처리 후 재검색: {title[:50]}... ({thumbnail_url[:60]}...)")
                                        
                                        # 썸네일을 빈 값으로 처리 (DB_writer 사용)
                                        try:
                                            if self.db_writer:
                                                # DB_writer를 통해 비동기 저장
                                                self.db_writer.update_thumbnail(torrent_id, '')
                                                work_session.close()
                                            else:
                                                # DB_writer가 없으면 직접 저장
                                                torrent.thumbnail_url = ''
                                                work_session.commit()
                                            
                                            # 다시 우선순위 리스트에 넣기 (다른 서버에서 찾을 수 있도록)
                                            if hasattr(self, 'priority_list') and hasattr(self, 'priority_lock'):
                                                with self.priority_lock:
                                                    # exclude_hosts에 현재 호스트 추가
                                                    current_host = None
                                                    try:
                                                        from urllib.parse import urlparse
                                                        current_host = urlparse(thumbnail_url).netloc.lower()
                                                    except:
                                                        pass
                                                    
                                                    new_exclude_hosts = list(exclude_hosts)
                                                    if current_host:
                                                        new_exclude_hosts.append(current_host)
                                                    
                                                    # 이미 우선순위 리스트에 있는지 확인 (중복 방지)
                                                    existing_entry = None
                                                    for entry in self.priority_list:
                                                        if entry['item']['id'] == torrent_id and not entry['processed']:
                                                            existing_entry = entry
                                                            break
                                                    
                                                    if existing_entry is None:
                                                        # 다시 리스트에 추가
                                                        self.priority_list.append({
                                                            'item': {
                                                            'id': torrent_id,
                                                            'title': title,
                                                            'thumbnail_url': '',
                                                            'is_priority': is_priority,
                                                            'exclude_hosts': new_exclude_hosts
                                                            },
                                                            'processed': False,
                                                            'processing_by': None
                                                        })
                                                        print(f"[{server_name.upper()}] 재검색 리스트에 추가: {title[:50]}...")
                                        except Exception as e:
                                            print(f"[{server_name.upper()}] .ico 처리 중 오류: {e}")
                                            work_session.rollback()
                                        
                                        if used_queue != 'priority_list' and hasattr(used_queue, 'task_done'):
                                            used_queue.task_done()
                                        continue
                                    
                                    # 썸네일 발견 - 상태 업데이트 및 DB 저장
                                    try:
                                        priority_mark = "[우선순위] " if is_priority else ""
                                        
                                        # already_found 변수를 status_lock 밖에서도 사용할 수 있도록 미리 선언
                                        already_found = False
                                        
                                        with status_lock:
                                            # 이미 다른 서버에서 찾았는지 재확인
                                            already_found = torrent_id in torrent_status and torrent_status[torrent_id].get('found')
                                            
                                            # 썸네일을 찾았으므로 항상 카운트 증가 (각 서버별 발견 횟수 추적)
                                            found_count += 1
                                            consecutive_no_found = 0  # 연속 실패 카운트 리셋
                                            
                                            if not already_found:
                                                # 실제로 새로 찾은 경우
                                                torrent_status[torrent_id] = {'found': True, 'thumbnail_url': thumbnail_url, 'tried_servers': set()}
                                        
                                        # 스레드 상태 업데이트 (status_lock 밖에서, thread_status_lock으로 보호)
                                        with thread_status_lock:
                                            if server_name in thread_status:
                                                # thread_status의 found를 직접 증가시켜서 동기화 문제 방지
                                                thread_status[server_name]['found'] = thread_status[server_name].get('found', 0) + 1
                                                # 로컬 found_count도 동기화
                                                found_count = thread_status[server_name]['found']
                                        
                                        # 출력은 status_lock 밖에서 (카운트 증가 후)
                                        # 품번 추출
                                        codes = thread_finder._extract_codes(title)
                                        code_str = codes[0] if codes else "(품번 없음)"
                                        print(f"[{server_name.upper()}] {priority_mark}썸네일 발견 [{code_str}]: {title[:50]}... ({thumbnail_url})")
                                        
                                        # already_found가 True일 때만 continue (이미 thread_status 업데이트 완료)
                                        if already_found:
                                            if used_queue != 'priority_list' and hasattr(used_queue, 'task_done'):
                                                used_queue.task_done()
                                            continue
                                        
                                        # DB에 저장 (DB_writer 사용)
                                        if self.db_writer:
                                            # DB_writer를 통해 비동기 저장 (서버 이름 포함)
                                            self.db_writer.update_thumbnail(torrent_id, thumbnail_url, server_name=server_name)
                                            # 세션은 닫기만 (커밋은 DB_writer가 처리)
                                            # torrent 객체는 더 이상 사용하지 않으므로 세션 닫기
                                            
                                            # 업데이트 카운트 증가
                                            with self._update_lock:
                                                updated_count += 1
                                            
                                            # 완료된 토렌트로 표시
                                            with completed_lock:
                                                completed_torrents.add(torrent_id)
                                            
                                            # 현재 페이지 항목이면 GUI 즉시 업데이트 (우선순위 리스트 또는 현재 페이지 항목)
                                            if is_priority or (hasattr(self, 'priority_ids') and torrent_id in self.priority_ids):
                                                self.thumbnail_updated.emit(torrent_id, thumbnail_url)
                                            
                                            # 세션 닫기 (DB_writer가 별도 세션에서 처리)
                                            work_session.close()
                                        else:
                                            # DB_writer가 없으면 직접 저장 (기존 방식)
                                            torrent.thumbnail_url = thumbnail_url
                                            work_session.commit()
                                            
                                            # 업데이트 카운트 증가
                                            with self._update_lock:
                                                updated_count += 1
                                            
                                            # 완료된 토렌트로 표시
                                            with completed_lock:
                                                completed_torrents.add(torrent_id)
                                            
                                            # 현재 페이지 항목이면 GUI 즉시 업데이트 (우선순위 리스트 또는 현재 페이지 항목)
                                            if is_priority or (hasattr(self, 'priority_ids') and torrent_id in self.priority_ids):
                                                self.thumbnail_updated.emit(torrent_id, thumbnail_url)
                                            
                                            # 세션 닫기
                                            work_session.close()
                                        
                                        if used_queue != 'priority_list' and hasattr(used_queue, 'task_done'):
                                            used_queue.task_done()
                                    except Exception as commit_error:
                                        # DB 저장 실패 시 로그 출력 및 다른 서버로 넘기기
                                        print(f"[{server_name.upper()}] DB 저장 실패 (ID: {torrent_id}): {commit_error}")
                                        try:
                                            work_session.rollback()
                                        except:
                                            pass
                                        finally:
                                            try:
                                                work_session.close()
                                            except:
                                                pass
                                        # 다른 서버로 넘기기
                                        with status_lock:
                                            if torrent_id not in torrent_status:
                                                torrent_status[torrent_id] = {'found': False, 'tried_servers': {server_name}}
                                            else:
                                                torrent_status[torrent_id]['tried_servers'].add(server_name)
                                            
                                            tried_servers = torrent_status[torrent_id].get('tried_servers', set())
                                            # JAVDB는 FC2 항목도 처리 가능, JAVBEE는 FC2가 아닌 항목만, JAVGURU, JAVMOST는 모든 형태 검색 가능
                                            if is_fc2_title(title):
                                                all_servers = {'fc2ppv', 'javdb', 'javguru', 'javmost'}  # FC2 항목: FC2PPV, JAVDB, JAVGURU, JAVMOST
                                            else:
                                                all_servers = {'javdb', 'javbee', 'javguru', 'javmost'}  # FC2가 아닌 항목: JAVDB, JAVBEE, JAVGURU, JAVMOST
                                            remaining_servers = all_servers - tried_servers
                                            
                                            if remaining_servers:
                                                for other_server in remaining_servers:
                                                    if other_server in server_queues:
                                                        item_copy = item.copy()
                                                        item_copy['exclude_hosts'] = exclude_hosts
                                                        if put_to_server_queue(other_server, item_copy):
                                                            break
                                        
                                        if used_queue != 'priority_list' and hasattr(used_queue, 'task_done'):
                                            used_queue.task_done()
                                else:
                                    # 현재 서버에서 못 찾음
                                    consecutive_no_found += 1
                                    
                                    # DB에서 최신 searched_servers 가져오기
                                    current_searched_servers_after = []
                                    if torrent.thumbnail_searched_servers:
                                        try:
                                            current_searched_servers_after = json.loads(torrent.thumbnail_searched_servers)
                                        except (json.JSONDecodeError, TypeError):
                                            current_searched_servers_after = []
                                    
                                    # 제목 형태에 따라 처리 가능한 서버 확인
                                    if is_fc2_title(title):
                                        all_servers_after = {'fc2ppv', 'javdb', 'javguru', 'javmost'}  # FC2 항목: FC2PPV, JAVDB, JAVGURU, JAVMOST
                                    else:
                                        all_servers_after = {'javdb', 'javbee', 'javguru', 'javmost'}  # FC2가 아닌 항목: JAVDB, JAVBEE, JAVGURU, JAVMOST
                                    
                                    remaining_servers_after = all_servers_after - set(current_searched_servers_after)
                                    
                                    if remaining_servers_after:
                                        # 아직 다른 서버에서 검색할 기회가 있으면 우선순위 대기열로 이동
                                        if hasattr(self, 'priority_list') and hasattr(self, 'priority_lock'):
                                            with self.priority_lock:
                                                # 이미 우선순위 리스트에 있는지 확인 (중복 방지)
                                                existing_entry = None
                                                for entry in self.priority_list:
                                                    if entry['item']['id'] == torrent_id and not entry['processed']:
                                                        existing_entry = entry
                                                        break
                                                
                                                if existing_entry is None:
                                                    # 우선순위 리스트에 추가 (다른 서버에서 먼저 처리하도록)
                                                    self.priority_list.append({
                                                        'item': {
                                                            'id': torrent_id,
                                                            'title': title,
                                                            'thumbnail_url': '',
                                                            'is_priority': True,  # 우선순위로 설정
                                                            'exclude_hosts': exclude_hosts
                                                        },
                                                        'processed': False,
                                                        'processing_by': None
                                                    })
                                                # 품번 추출
                                                codes = thread_finder._extract_codes(title)
                                                code_str = codes[0] if codes else "(품번 없음)"
                                                print(f"[{server_name.upper()}] 썸네일 미발견 [{code_str}]: {title[:50]}... (남은 서버: {sorted(remaining_servers_after)})")
                                        
                                        # 다른 서버 큐에도 추가 (서버 큐가 비어있을 때 빠르게 처리)
                                        with status_lock:
                                            if torrent_id not in torrent_status:
                                                torrent_status[torrent_id] = {'found': False, 'tried_servers': {server_name}}
                                            else:
                                                torrent_status[torrent_id]['tried_servers'].add(server_name)
                                            
                                            tried_servers = torrent_status[torrent_id].get('tried_servers', set())
                                            remaining_servers = all_servers_after - tried_servers
                                            
                                            if remaining_servers:
                                                # 다른 서버 큐에 추가
                                                for other_server in remaining_servers:
                                                    if other_server in server_queues:
                                                        item_copy = item.copy()
                                                        item_copy['exclude_hosts'] = exclude_hosts
                                                        put_to_server_queue(other_server, item_copy)
                                    else:
                                        # 모든 서버에서 시도했지만 못 찾음 - 완료로 표시
                                        # 품번 추출
                                        codes = thread_finder._extract_codes(title)
                                        code_str = codes[0] if codes else "(품번 없음)"
                                        print(f"[{server_name.upper()}] 썸네일 미발견 [{code_str}]: {title[:50]}... (모든 서버 시도 완료)")
                                        with status_lock:
                                            if torrent_id not in torrent_status:
                                                torrent_status[torrent_id] = {'found': False, 'tried_servers': {server_name}}
                                            else:
                                                torrent_status[torrent_id]['tried_servers'].add(server_name)
                                            torrent_status[torrent_id]['found'] = False
                                            with completed_lock:
                                                completed_torrents.add(torrent_id)
                                    
                                    if used_queue != 'priority_list' and hasattr(used_queue, 'task_done'):
                                        used_queue.task_done()
                            finally:
                                work_session.close()
                        except Exception as e:
                            # 오류 발생 시 다른 큐로 넘기기
                            with status_lock:
                                if torrent_id not in torrent_status:
                                    torrent_status[torrent_id] = {'found': False, 'tried_servers': {server_name}}
                                else:
                                    torrent_status[torrent_id]['tried_servers'].add(server_name)
                                
                                tried_servers = torrent_status[torrent_id].get('tried_servers', set())
                                # JAVDB는 FC2 항목도 처리 가능, JAVBEE는 FC2가 아닌 항목만, JAVGURU, JAVMOST는 모든 형태 검색 가능
                                if is_fc2_title(title):
                                    all_servers = {'fc2ppv', 'javdb', 'javguru', 'javmost'}  # FC2 항목: FC2PPV, JAVDB, JAVGURU, JAVMOST
                                else:
                                    all_servers = {'javdb', 'javbee', 'javguru', 'javmost'}  # FC2가 아닌 항목: JAVDB, JAVBEE, JAVGURU, JAVMOST
                                remaining_servers = all_servers - tried_servers
                                
                                if remaining_servers:
                                    for other_server in remaining_servers:
                                        if other_server in server_queues:
                                            item_copy = item.copy()
                                            item_copy['exclude_hosts'] = exclude_hosts
                                            put_to_server_queue(other_server, item_copy)
                            
                            if used_queue and hasattr(used_queue, 'task_done') and used_queue != 'priority_list':
                                used_queue.task_done()
                    
                    # while 루프 종료 시 최종 통계 출력
                    print(f"[{server_name.upper()}] 스레드 최종 종료: 총 처리 {processed_count}개, 발견 {found_count}개 (Thread ID: {thread_id})")
                
                # 서버별 스레드 시작 (각 서버당 1개씩만, 병렬 처리)
                # 각 스레드는 공통 큐와 자신의 재시도 큐를 확인
                worker_threads = []
                
                # MissAV 스레드 (비활성화)
                # thread = threading.Thread(
                #     target=server_worker,
                #     args=(priority_queue, 'missav', [main_queue, server_queues['missav']], 
                #             [server_queues['javlibrary'], server_queues['javdb'], server_queues['fc2ppv'], server_queues['javbee']]),
                #     daemon=True
                # )
                # thread.start()
                # worker_threads.append(thread)
                
                # JAVLibrary 스레드 (비활성화)
                # thread = threading.Thread(
                #     target=server_worker,
                #     args=(priority_queue, 'javlibrary', [main_queue, server_queues['javlibrary']],
                #             [server_queues['missav'], server_queues['javdb'], server_queues['fc2ppv'], server_queues['javbee']]),
                #     daemon=True
                # )
                # thread.start()
                # worker_threads.append(thread)
                
                # JAVDB 스레드
                thread = threading.Thread(
                    target=server_worker,
                    args=(priority_list, priority_lock, main_list, main_lock, 'javdb',
                          [server_queues['fc2ppv'], server_queues['javbee']]),  # missav, javlibrary 비활성화
                    daemon=True
                )
                thread.start()
                worker_threads.append(thread)
                
                # FC2PPV 스레드
                thread = threading.Thread(
                    target=server_worker,
                    args=(priority_list, priority_lock, main_list, main_lock, 'fc2ppv',
                          [server_queues['javdb'], server_queues['javbee'], server_queues['javguru']]),  # missav, javlibrary 비활성화
                    daemon=True
                )
                thread.start()
                worker_threads.append(thread)
                
                # JAVBee 스레드
                thread = threading.Thread(
                    target=server_worker,
                    args=(priority_list, priority_lock, main_list, main_lock, 'javbee',
                          [server_queues['javdb'], server_queues['fc2ppv']]),  # javdb, fc2ppv
                    daemon=True
                )
                thread.start()
                worker_threads.append(thread)
                
                # JAV.GURU 스레드 (모든 형태의 제목 검색 가능)
                thread = threading.Thread(
                    target=server_worker,
                    args=(priority_list, priority_lock, main_list, main_lock, 'javguru',
                          [server_queues['javdb'], server_queues['javbee'], server_queues['fc2ppv']]),  # javdb, javbee, fc2ppv
                    daemon=True
                )
                thread.start()
                worker_threads.append(thread)
                
                # JAVMOST 스레드 (모든 형태의 제목 검색 가능)
                thread = threading.Thread(
                    target=server_worker,
                    args=(priority_list, priority_lock, main_list, main_lock, 'javmost',
                          [server_queues['javdb'], server_queues['javbee'], server_queues['fc2ppv'], server_queues['javguru']]),  # javdb, javbee, fc2ppv, javguru
                    daemon=True
                )
                thread.start()
                worker_threads.append(thread)
                
                # Sukebei Nyaa 스레드 (비활성화)
                # thread = threading.Thread(
                #     target=server_worker,
                #     args=(priority_list, priority_lock, main_list, main_lock, 'nyaa',
                #           [server_queues['javdb'], server_queues['javbee'], server_queues['fc2ppv']]),  # javdb, javbee, fc2ppv
                #     daemon=True
                # )
                # thread.start()
                # worker_threads.append(thread)
                
                # 모든 스레드 상태 모니터링 스레드 시작
                def monitor_threads():
                    """모든 스레드 상태를 주기적으로 출력"""
                    import time
                    import json
                    import queue
                    from database.models import Torrent
                    last_print_time = time.time()
                    while not self._stop_requested:
                        time.sleep(5.0)  # 5초마다 확인
                        current_time = time.time()
                        
                        # 10초마다 모든 스레드 상태 출력
                        if current_time - last_print_time >= 10.0:
                            with thread_status_lock:
                                if thread_status:
                                    print(f"\n[썸네일] === 모든 스레드 상태 (10초마다 업데이트) ===")
                                    for server_name, status in sorted(thread_status.items()):
                                        processed = status.get('processed', 0)
                                        found = status.get('found', 0)
                                        blocked = status.get('blocked', False)
                                        thread_id = status.get('thread_id', 0)
                                        
                                        if blocked:
                                            print(f"  [{server_name.upper()}] ⚠️ 차단됨 - 처리 {processed}개, 발견 {found}개")
                                        else:
                                            found_rate = (found / processed * 100) if processed > 0 else 0
                                            print(f"  [{server_name.upper()}] 실행 중 - 처리 {processed}개, 발견 {found}개 (발견률: {found_rate:.1f}%)")
                                    
                                    # 대기열 정보 출력
                                    try:
                                        # 우선순위 리스트의 첫 번째 항목 정보 출력
                                        if not hasattr(self, 'priority_list') or not hasattr(self, 'priority_lock'):
                                            print(f"  [대기열] 우선순위 대기열: 0개, 일반 대기열: 0개")
                                            return
                                        
                                        with self.priority_lock:
                                            unprocessed_priority = [x for x in self.priority_list if not x['processed']]
                                            priority_list_size = len(unprocessed_priority)
                                            
                                            # 우선순위 리스트의 첫 번째 항목 정보 출력
                                            if unprocessed_priority:
                                                try:
                                                    first_priority_entry = unprocessed_priority[0]
                                                    first_priority_item = first_priority_entry['item']
                                                    
                                                    # 첫 번째 항목의 DB 상태 확인
                                                    debug_session_priority = self.db.get_session()
                                                    try:
                                                        debug_torrent_priority = debug_session_priority.get(Torrent, first_priority_item['id'])
                                                        if debug_torrent_priority:
                                                            searched_servers_priority_debug = []
                                                            if debug_torrent_priority.thumbnail_searched_servers:
                                                                try:
                                                                    searched_servers_priority_debug = json.loads(debug_torrent_priority.thumbnail_searched_servers)
                                                                except (json.JSONDecodeError, TypeError):
                                                                    searched_servers_priority_debug = []
                                                            
                                                            # 제목 형태에 따라 처리 가능한 서버 확인
                                                            title_text_priority_debug = first_priority_item.get('title', '') or debug_torrent_priority.title or ''
                                                            fc2_patterns_priority_debug = [
                                                                r'FC2[-\s]?PPV[-\s]?(\d{6,8})',  # FC2-PPV-숫자
                                                                r'FC2[-\s]?PPV',                  # FC2-PPV
                                                                r'FC2PPV',                        # FC2PPV
                                                                r'FC2\s+PPV'                      # FC2 PPV
                                                            ]
                                                            is_fc2_priority_debug = False
                                                            if title_text_priority_debug:
                                                                title_upper_priority_debug = title_text_priority_debug.upper()
                                                                for pattern in fc2_patterns_priority_debug:
                                                                    if re.search(pattern, title_upper_priority_debug):
                                                                        is_fc2_priority_debug = True
                                                                        break
                                                            
                                                            # FC2 항목: FC2PPV, JAVDB, NYAA만 처리 가능
                                                            # FC2가 아닌 항목: JAVDB, JAVBEE, NYAA만 처리 가능
                                                            # NYAA는 모든 형태의 품번 검색 가능
                                                            if is_fc2_priority_debug:
                                                                all_servers_priority_debug = {'fc2ppv', 'javdb', 'javguru'}  # FC2 항목: FC2PPV, JAVDB, JAVGURU
                                                            else:
                                                                all_servers_priority_debug = {'javdb', 'javbee', 'javguru'}  # FC2가 아닌 항목: JAVDB, JAVBEE, JAVGURU
                                                            
                                                            remaining_servers_priority_debug = all_servers_priority_debug - set(searched_servers_priority_debug)
                                                            
                                                            print(f"  [우선순위 대기열 첫 항목] ID: {first_priority_item['id']}, 제목: {(title_text_priority_debug or '(제목 없음)')[:60]}")
                                                            print(f"    FC2 항목: {is_fc2_priority_debug}, 처리 가능 서버: {sorted(all_servers_priority_debug)}, 검색한 서버: {sorted(searched_servers_priority_debug) if searched_servers_priority_debug else '(없음)'}, 미검색 서버: {sorted(remaining_servers_priority_debug) if remaining_servers_priority_debug else '(없음)'}")
                                                        else:
                                                            print(f"  [우선순위 대기열 첫 항목] ID: {first_priority_item['id']} - DB에서 찾을 수 없음")
                                                    finally:
                                                        debug_session_priority.close()
                                                except Exception as e:
                                                    print(f"  [우선순위 대기열 첫 항목] 확인 오류: {e}")
                                            else:
                                                # 우선순위 리스트가 비어있을 때
                                                print(f"  [우선순위 대기열 첫 항목] (없음)")
                                        
                                        # 메인 리스트의 첫 번째 항목 정보 출력
                                        if not hasattr(self, 'main_list') or not hasattr(self, 'main_lock'):
                                            print(f"  [대기열] 우선순위 대기열: {priority_list_size}개, 일반 대기열: 0개")
                                            return
                                        
                                        with self.main_lock:
                                            unprocessed_main = [x for x in self.main_list if not x['processed']]
                                            main_list_size = len(unprocessed_main)
                                            
                                            # 메인 리스트의 첫 번째 항목 정보 출력
                                            if unprocessed_main:
                                                try:
                                                    first_main_entry = unprocessed_main[0]
                                                    first_main_item = first_main_entry['item']
                                                
                                                    # 첫 번째 항목의 DB 상태 확인
                                                    debug_session = self.db.get_session()
                                                    try:
                                                        debug_torrent = debug_session.get(Torrent, first_main_item['id'])
                                                        if debug_torrent:
                                                            searched_servers_debug = []
                                                            if debug_torrent.thumbnail_searched_servers:
                                                                try:
                                                                    searched_servers_debug = json.loads(debug_torrent.thumbnail_searched_servers)
                                                                except (json.JSONDecodeError, TypeError):
                                                                    searched_servers_debug = []
                                                            
                                                            # 제목 형태에 따라 처리 가능한 서버 확인
                                                            title_text_debug = first_main_item.get('title', '') or debug_torrent.title or ''
                                                            fc2_patterns_debug = [
                                                                r'FC2[-\s]?PPV[-\s]?(\d{6,8})',  # FC2-PPV-숫자
                                                                r'FC2[-\s]?PPV',                  # FC2-PPV
                                                                r'FC2PPV',                        # FC2PPV
                                                                r'FC2\s+PPV'                      # FC2 PPV
                                                            ]
                                                            is_fc2_debug = False
                                                            if title_text_debug:
                                                                title_upper_debug = title_text_debug.upper()
                                                                for pattern in fc2_patterns_debug:
                                                                    if re.search(pattern, title_upper_debug):
                                                                        is_fc2_debug = True
                                                                        break
                                                            
                                                            # FC2 항목: FC2PPV, JAVDB, JAVGURU만 처리 가능
                                                            # FC2가 아닌 항목: JAVDB, JAVBEE, JAVGURU만 처리 가능
                                                            if is_fc2_debug:
                                                                all_servers_debug = {'fc2ppv', 'javdb', 'javguru'}  # FC2 항목: FC2PPV, JAVDB, JAVGURU
                                                            else:
                                                                all_servers_debug = {'javdb', 'javbee', 'javguru'}  # FC2가 아닌 항목: JAVDB, JAVBEE, JAVGURU
                                                            
                                                            remaining_servers_debug = all_servers_debug - set(searched_servers_debug)
                                                            
                                                            print(f"  [일반 대기열 첫 항목] ID: {first_main_item['id']}, 제목: {(title_text_debug or '(제목 없음)')[:60]}")
                                                            print(f"    FC2 항목: {is_fc2_debug}, 처리 가능 서버: {sorted(all_servers_debug)}, 검색한 서버: {sorted(searched_servers_debug) if searched_servers_debug else '(없음)'}, 미검색 서버: {sorted(remaining_servers_debug) if remaining_servers_debug else '(없음)'}")
                                                        else:
                                                            print(f"  [일반 대기열 첫 항목] ID: {first_main_item['id']} - DB에서 찾을 수 없음")
                                                    finally:
                                                        debug_session.close()
                                                except Exception as e:
                                                    print(f"  [일반 대기열 첫 항목] 확인 오류: {e}")
                                            else:
                                                # 메인 리스트가 비어있을 때
                                                print(f"  [일반 대기열 첫 항목] (없음)")
                                        
                                        # 대기열 크기 출력
                                        print(f"  [대기열] 우선순위 대기열: {priority_list_size}개, 일반 대기열: {main_list_size}개")
                                        
                                        if hasattr(self, 'server_queues'):
                                            for server_name, server_queue in self.server_queues.items():
                                                server_q_size = server_queue.qsize() if hasattr(server_queue, 'qsize') else 0
                                                print(f"  [대기열] {server_name.upper()} 큐: {server_q_size}개")
                                    except Exception as e:
                                        print(f"  [대기열] 큐 정보 확인 오류: {e}")
                                    
                                    # DB Writer 상태 출력
                                    if hasattr(self, 'db_writer') and self.db_writer:
                                        queue_size = self.db_writer.queue.qsize()
                                        is_running = self.db_writer.isRunning()
                                        print(f"  [DBWriter] 실행 중: {is_running}, 큐 크기: {queue_size}")
                                    
                                    print(f"[썸네일] ===========================================\n")
                            last_print_time = current_time
                        
                        # 모든 스레드가 종료되었는지 확인
                        all_finished = True
                        for thread in worker_threads:
                            if thread.is_alive():
                                all_finished = False
                                break
                        if all_finished:
                            break
                
                monitor_thread = threading.Thread(target=monitor_threads, daemon=True)
                monitor_thread.start()
                
                # 모든 작업 완료 대기 및 진행 상황 업데이트
                completed_count = 0
                
                while any(t.is_alive() for t in worker_threads):
                    if self._stop_requested:
                        break
                    
                    # 진행 상황 업데이트 (완료된 토렌트 수로 계산)
                    with completed_lock:
                        current_completed = len(completed_torrents)
                        # 새로 완료된 항목 중 썸네일 교체 요청 항목 확인
                        newly_completed = completed_torrents - (hasattr(self, '_last_completed_set') and self._last_completed_set or set())
                        if not hasattr(self, '_last_completed_set'):
                            self._last_completed_set = set()
                        self._last_completed_set = completed_torrents.copy()
                    
                    if current_completed != completed_count:
                        completed_count = current_completed
                        print(f"\r[썸네일] 검색 중... {completed_count}/{total} (업데이트: {updated_count})", end='', flush=True)
                        self.progress.emit(
                            int((completed_count / total) * 100) if total > 0 else 0,
                            f"썸네일 검색 중... {completed_count}/{total} (업데이트: {updated_count})"
                        )
                        
                        # 새로 완료된 항목 중 썸네일 교체 요청 항목이 있으면 시그널 발생 (버튼 활성화를 위해)
                        # 하지만 pending_replace_ids는 MainWindow에 있으므로 직접 접근 불가
                        # 대신 thumbnail_updated 시그널을 빈 URL로 발생시켜 버튼 활성화
                        # (하지만 이 방법은 복잡하므로, on_thumbnail_finished에서 처리하는 것이 더 나음)
                    
                    import time
                    time.sleep(0.5)  # 진행 상황 확인 간격
                
                # 모든 작업 완료 대기 (리스트는 join()이 없으므로 스레드 종료만 대기)
                for q in server_queues.values():
                    q.join()
                
                print(f"\n[썸네일] 백그라운드 업데이트 완료: {updated_count}개")
                self.finished.emit(updated_count)
            
            finally:
                if 'session' in locals():
                    try:
                        session.close()
                    except:
                        pass
        
        except Exception as e:
            print(f"[썸네일] 백그라운드 업데이트 오류: {e}")
            self.error.emit(str(e))


class SingleThumbnailReplaceThread(QThread):
    """단일 항목 썸네일 교체 스레드"""
    updated = Signal(int, str)  # (torrent_id, new_thumbnail_url)
    error = Signal(str)

    def __init__(self, db: Database, torrent_id: int, image_finder=None, db_writer=None):
        super().__init__()
        self.db = db
        self.torrent_id = torrent_id
        self.image_finder = image_finder  # 재사용할 ImageFinder
        self.db_writer = db_writer  # DB Writer Thread

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
                
                # DB에서 이미 탐색한 서버 목록 확인
                import json
                searched_servers = []
                if t.thumbnail_searched_servers:
                    try:
                        searched_servers = json.loads(t.thumbnail_searched_servers)
                    except (json.JSONDecodeError, TypeError):
                        searched_servers = []
                
                # 탐색하지 않은 서버만 검색 (교체 시 우선 탐색)
                exclude_servers = searched_servers.copy()  # 이미 탐색한 서버는 제외
                
                # ImageFinder 재사용 (없으면 새로 생성)
                if self.image_finder is None:
                    from scrapers.image_finder import ImageFinder
                    self.image_finder = ImageFinder()
                
                result = self.image_finder.search_images(
                    title, 
                    max_images=5, 
                    exclude_hosts=exclude_hosts or None,
                    exclude_servers=exclude_servers if exclude_servers else None
                )
                new_url = (result.get('thumbnail') or '').strip()
                if new_url and new_url != current_url:
                    # DB 저장 (DB_writer 사용)
                    if self.db_writer:
                        # DB_writer를 통해 비동기 저장
                        self.db_writer.update_thumbnail(self.torrent_id, new_url)
                        session.close()
                        self.updated.emit(self.torrent_id, new_url)
                    else:
                        # DB_writer가 없으면 직접 저장 (기존 방식)
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
    
    def __init__(self, db: Database, scraper_manager: ScraperManager, source_key: str, pages: int = 5, enable_thumbnail: bool = False, query: str = None, db_writer=None):
        super().__init__()
        self.db = db
        self.scraper_manager = scraper_manager
        self.source_key = source_key
        self.pages = pages
        self.enable_thumbnail = enable_thumbnail
        self.query = query  # 검색어
        self.db_writer = db_writer  # DB Writer Thread (비동기 저장용)
        self.db_writer_stats = {'added': 0, 'updated': 0, 'duplicate': 0}  # 통계 추적
        self._stop_requested = False
        
        # db_writer가 있으면 배치 완료 시그널 연결
        if self.db_writer:
            self.db_writer.batch_completed.connect(self._on_db_batch_completed)
    
    def stop(self):
        """스크래핑 중단 요청"""
        self._stop_requested = True
    
    def _on_db_batch_completed(self, stats: dict):
        """DB 배치 저장 완료 시그널 처리"""
        added = stats.get('added', 0)
        updated = stats.get('updated', 0)
        duplicate = stats.get('duplicate', 0)
        
        self.db_writer_stats['added'] += added
        self.db_writer_stats['updated'] += updated
        self.db_writer_stats['duplicate'] += duplicate
        
        # 디버그: 시그널이 제대로 전달되는지 확인
    
    def run(self):
        """스크래핑 실행"""
        try:
            self._stop_requested = False
            # 통계 초기화
            self.db_writer_stats = {'added': 0, 'updated': 0, 'duplicate': 0}
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
                    
                    # 스마트 스크래핑 사용 (중복 최소화, db_writer로 실시간 저장)
                    torrents = self.scraper_manager.scrape_source_smart(
                        key, 
                        self.db, 
                        max_pages=self.pages,
                        stop_on_duplicate=True,
                        stop_callback=lambda: self._stop_requested,
                        progress_callback=make_progress_cb(source_idx, source_info, num_sources),
                        db_writer=self.db_writer
                    )
                    
                    # db_writer를 사용하면 이미 실시간으로 저장되었으므로 추가 저장 불필요
                    # 큐에 남은 작업이 완료될 때까지 대기
                    if self.db_writer:
                        self.db_writer.queue.join()
                    print(f"[스크래핑] [{source_info['name']}] 스크래핑 완료: {len(torrents)}개 수집됨 (DB 저장 완료)")
                    
                    # 정지 요청 시 루프 중단
                    if self._stop_requested:
                        break
                
                # 모든 소스 처리 완료 후 최종 통계
                total_added = self.db_writer_stats.get('added', 0)
                total_updated = self.db_writer_stats.get('updated', 0)
                print(f"[스크래핑] 전체 통계: 신규 {total_added}개, 업데이트 {total_updated}개")
            
            # 특정 소스에서만 수집
            else:
                # 진행률 콜백: 페이지 기준으로 진행률 표시
                def progress_cb(page, max_pages, message):
                    progress = int((page / max_pages) * 100)
                    self.progress.emit(progress, message)
                
                # 스마트 스크래핑 사용 (db_writer로 실시간 비동기 저장)
                torrents = self.scraper_manager.scrape_source_smart(
                    self.source_key,
                    self.db,
                    max_pages=self.pages,
                    query=self.query,
                    stop_on_duplicate=True,
                    stop_callback=lambda: self._stop_requested,
                    progress_callback=progress_cb,
                    db_writer=self.db_writer
                )
                
                # db_writer를 사용하면 이미 실시간으로 저장되었으므로 추가 저장 불필요
                # 통계 초기화
                self.db_writer_stats = {'added': 0, 'updated': 0, 'duplicate': 0}
                
                # 큐에 남은 작업이 완료될 때까지 대기
                if self.db_writer:
                    print(f"[스크래핑] DB 저장 큐 완료 대기 중... (큐 크기: {self.db_writer.queue.qsize()})")
                    self.db_writer.queue.join()
                    print(f"[스크래핑] DB 저장 완료 (최종 통계: 추가={self.db_writer_stats.get('added', 0)}, 업데이트={self.db_writer_stats.get('updated', 0)})")
                
                # 통계 사용 (시그널로 받은 통계 누적값)
                total_added = self.db_writer_stats.get('added', 0)
                total_updated = self.db_writer_stats.get('updated', 0)
                total_duplicate = self.db_writer_stats.get('duplicate', 0)
                
                # 통계가 0이면 경고 출력
                if total_added == 0 and total_updated == 0 and len(torrents) > 0:
                    print(f"[스크래핑] ⚠️ 경고: {len(torrents)}개 수집했지만 DB 통계가 0입니다. 시그널이 제대로 전달되지 않았을 수 있습니다.")
                
                print(f"[스크래핑] 스크래핑 완료: {len(torrents)}개 수집됨 (DB 저장: 신규 {total_added}개, 업데이트 {total_updated}개, 중복 {total_duplicate}개)")
            
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
        
        # DB Writer Thread 초기화 (큐 기반 비동기 DB 업데이트)
        self.db_writer = DBWriterThread(self.db)
        self.db_writer.start()
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
        # 썸네일 교체 요청 항목 추적 (버튼 활성화를 위해)
        self.pending_replace_ids = set()
        # GIF 정리 스레드 참조 (소멸 방지)
        self.gif_cleanup_thread = None
        # 썸네일 검색 서버 초기화 스레드 참조 (소멸 방지)
        self.reset_searched_servers_thread = None
        self.init_ui()
        # 부팅 시 GIF 썸네일 검사 및 초기화 (백그라운드)
        self._cleanup_gif_thumbnails()
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
        
        # 검색어 입력 필드 (Sukebei 검색용)
        search_label = QLabel("검색어:")
        top_layout.addWidget(search_label)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("검색어 입력 (예: gachi)")
        self.search_input.setMinimumWidth(150)
        top_layout.addWidget(self.search_input)
        
        # 추천 검색어 버튼들
        recommended_keywords = ["uncen", "漏れ", "無修正"]
        for keyword in recommended_keywords:
            btn = QPushButton(keyword)
            btn.setMaximumWidth(80)
            btn.clicked.connect(lambda checked, kw=keyword: self.search_input.setText(kw))
            top_layout.addWidget(btn)
        
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
        self.filter_panel.reset_searched_servers_requested.connect(self.reset_all_searched_servers)
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
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.status_bar.showMessage("준비됨"))
    
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
        
        settings_menu.addSeparator()
        
        # 썸네일 검색 서버 초기화는 필터 패널에 버튼으로 배치됨 (설정 메뉴에서 제거)
        
        # GIF 썸네일 수동 정리
        cleanup_gif_action = QAction("GIF 썸네일 정리", self)
        cleanup_gif_action.triggered.connect(self._cleanup_gif_thumbnails)
        settings_menu.addAction(cleanup_gif_action)
    
    def _cleanup_gif_thumbnails(self):
        """부팅 시 GIF 썸네일 검사 및 초기화 (백그라운드)"""
        from PySide6.QtCore import QThread, Signal
        
        class GifCleanupThread(QThread):
            finished = Signal(int)  # (초기화된 개수)
            
            def __init__(self, db, db_writer):
                super().__init__()
                self.db = db
                self.db_writer = db_writer
            
            def run(self):
                try:
                    session = self.db.get_session()
                    try:
                        from database.models import Torrent
                        
                        # GIF 썸네일이 있는 모든 토렌트 조회
                        all_torrents = session.query(Torrent).filter(
                            Torrent.thumbnail_url.isnot(None),
                            Torrent.thumbnail_url != ''
                        ).all()
                        
                        gif_count = 0
                        for torrent in all_torrents:
                            if not torrent.thumbnail_url:
                                continue
                            
                            thumbnail_url_lower = torrent.thumbnail_url.lower()
                            # GIF 파일 감지: 더 정확한 패턴 사용
                            is_gif = (
                                thumbnail_url_lower.endswith('.gif') or
                                thumbnail_url_lower.endswith('.gif?') or
                                thumbnail_url_lower.endswith('.gif&') or
                                thumbnail_url_lower.endswith('.gif#') or
                                thumbnail_url_lower.endswith('.gif/') or
                                '.gif?' in thumbnail_url_lower or
                                '.gif&' in thumbnail_url_lower or
                                '.gif#' in thumbnail_url_lower or
                                '.gif/' in thumbnail_url_lower or
                                re.search(r'\.gif[\?#&/]', thumbnail_url_lower) is not None
                            )
                            
                            if is_gif:
                                # GIF 파일이면 초기화 (썸네일 URL 및 검색 서버 리스트 초기화)
                                if self.db_writer:
                                    self.db_writer.update_thumbnail(torrent.id, '')
                                    # 검색 서버 리스트도 초기화
                                    torrent.thumbnail_searched_servers = '[]'
                                    session.commit()
                                else:
                                    torrent.thumbnail_url = ''
                                    torrent.thumbnail_searched_servers = '[]'
                                    session.commit()
                                gif_count += 1
                        
                        if gif_count > 0:
                            print(f"[초기화] GIF 썸네일 {gif_count}개 초기화 완료 (썸네일 URL 및 검색 서버 리스트 초기화)")
                        self.finished.emit(gif_count)
                    finally:
                        session.close()
                except Exception as e:
                    print(f"[초기화] GIF 썸네일 초기화 오류: {e}")
                    self.finished.emit(0)
        
        # 백그라운드 스레드로 실행 (멤버 변수로 저장하여 소멸 방지)
        self.gif_cleanup_thread = GifCleanupThread(self.db, self.db_writer)
        self.gif_cleanup_thread.finished.connect(lambda count: self._on_gif_cleanup_finished(count))
        self.gif_cleanup_thread.start()
    
    def _on_gif_cleanup_finished(self, count: int):
        """GIF 정리 완료 처리"""
        if count > 0:
            print(f"[초기화] GIF 썸네일 {count}개 초기화 완료")
        # 스레드 정리
        if self.gif_cleanup_thread:
            self.gif_cleanup_thread.quit()
            self.gif_cleanup_thread.wait(1000)  # 최대 1초 대기
            self.gif_cleanup_thread = None
    
    def reset_all_searched_servers(self):
        """전체 DB에서 썸네일 검색 서버 항목 초기화"""
        from PySide6.QtWidgets import QMessageBox
        
        # QMessageBox를 비동기로 처리하여 UI 블로킹 방지
        from PySide6.QtCore import QTimer
        
        def show_question_async():
            reply = QMessageBox.question(
                self,
                "확인",
                "전체 DB의 썸네일 검색 서버 목록을 초기화하시겠습니까?\n\n이 작업은 모든 토렌트의 검색 서버 기록을 삭제하여 다시 검색할 수 있게 합니다.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                self._execute_reset_searched_servers()
        
        QTimer.singleShot(0, show_question_async)
    
    def _execute_reset_searched_servers(self):
        """검색 서버 초기화 실행"""
        from PySide6.QtCore import QThread, Signal
        
        class ResetSearchedServersThread(QThread):
            finished = Signal(int)  # (초기화된 개수)
            error = Signal(str)
            
            def __init__(self, db):
                super().__init__()
                self.db = db
            
            def run(self):
                try:
                    session = self.db.get_session()
                    try:
                        from database.models import Torrent
                        
                        # 모든 토렌트 조회
                        all_torrents = session.query(Torrent).all()
                        
                        reset_count = 0
                        for torrent in all_torrents:
                            if torrent.thumbnail_searched_servers and torrent.thumbnail_searched_servers.strip():
                                torrent.thumbnail_searched_servers = '[]'
                                reset_count += 1
                        
                        session.commit()
                        self.finished.emit(reset_count)
                    finally:
                        session.close()
                except Exception as e:
                    self.error.emit(str(e))
        
        # 백그라운드 스레드로 실행 (멤버 변수로 저장하여 소멸 방지)
        self.reset_searched_servers_thread = ResetSearchedServersThread(self.db)
        self.reset_searched_servers_thread.finished.connect(self._on_reset_searched_servers_finished)
        self.reset_searched_servers_thread.error.connect(self._on_reset_searched_servers_error)
        self.reset_searched_servers_thread.start()
        
        # 상태 바 업데이트를 비동기로 처리
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.status_bar.showMessage("썸네일 검색 서버 초기화 중...", 0))
    
    def _on_reset_searched_servers_finished(self, count: int):
        """검색 서버 초기화 완료 처리"""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox
        
        # 상태 바 업데이트를 비동기로 처리
        QTimer.singleShot(0, lambda: self.status_bar.showMessage(f"썸네일 검색 서버 초기화 완료: {count}개 항목", 5000))
        print(f"[초기화] 썸네일 검색 서버 초기화 완료: {count}개 항목")
        
        # QMessageBox를 비동기로 처리
        def show_info_async():
            QMessageBox.information(
                self,
                "완료",
                f"썸네일 검색 서버 초기화가 완료되었습니다.\n\n초기화된 항목: {count}개"
            )
        QTimer.singleShot(0, show_info_async)
        # 스레드 정리
        if self.reset_searched_servers_thread:
            self.reset_searched_servers_thread.quit()
            self.reset_searched_servers_thread.wait(1000)  # 최대 1초 대기
            self.reset_searched_servers_thread = None
    
    def _on_reset_searched_servers_error(self, error_msg: str):
        """검색 서버 초기화 오류 처리"""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox
        
        # 상태 바 업데이트를 비동기로 처리
        QTimer.singleShot(0, lambda: self.status_bar.showMessage(f"초기화 오류: {error_msg}", 5000))
        print(f"[초기화] 썸네일 검색 서버 초기화 오류: {error_msg}")
        
        # QMessageBox를 비동기로 처리
        def show_error_async():
            QMessageBox.critical(
                self,
                "오류",
                f"썸네일 검색 서버 초기화 중 오류가 발생했습니다:\n\n{error_msg}"
            )
        QTimer.singleShot(0, show_error_async)
        # 스레드 정리
        if self.reset_searched_servers_thread:
            self.reset_searched_servers_thread.quit()
            self.reset_searched_servers_thread.wait(1000)  # 최대 1초 대기
            self.reset_searched_servers_thread = None
    
    def load_torrents(self):
        """토렌트 목록 로드 (페이지네이션) - 비동기 처리"""
        from PySide6.QtCore import QTimer
        
        # DB 쿼리를 비동기로 처리하여 UI 블로킹 방지
        def load_async():
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
                
                # UI 업데이트는 메인 스레드에서 비동기로 처리
                def update_ui():
                    print(f"[UI] 토렌트 로드: 전체 {self.total_count}개, 현재 페이지 {self.current_page}/{self.total_pages}, 표시 {len(torrents)}개")
                    self.torrent_list.set_torrents(torrents)
                    
                    # UI 업데이트도 비동기로 처리하여 UI 블로킹 방지
                    def update_ui_async():
                        self.update_pagination_ui()
                        self.status_bar.showMessage(
                            f"페이지 {self.current_page}/{self.total_pages} - "
                            f"{len(torrents)}개 표시 (전체 {self.total_count}개)"
                        )
                        
                        # 썸네일 업데이트 시작 (비동기로 처리)
                        QTimer.singleShot(100, self.start_thumbnail_update)  # 100ms 후 시작
                    
                    QTimer.singleShot(0, update_ui_async)
                
                QTimer.singleShot(0, update_ui)
            
            except Exception as e:
                def show_error():
                    QMessageBox.critical(self, "오류", f"토렌트 로드 실패: {e}")
                QTimer.singleShot(0, show_error)
            finally:
                session.close()
        
        # 비동기로 DB 쿼리 실행
        QTimer.singleShot(0, load_async)
    
    def update_pagination_ui(self):
        """페이지네이션 UI 업데이트 - 비동기 처리"""
        from PySide6.QtCore import QTimer
        
        # UI 업데이트를 비동기로 처리하여 블로킹 방지
        def update_async():
            self.page_label.setText(f"페이지: {self.current_page} / {self.total_pages}")
            self.total_label.setText(f"전체: {self.total_count}개")
            self.page_input.setText(str(self.current_page))
        
        QTimer.singleShot(0, update_async)
        
        # 버튼 활성화/비활성화도 비동기로 처리
        def update_buttons():
            self.prev_btn.setEnabled(self.current_page > 1)
            self.next_btn.setEnabled(self.current_page < self.total_pages)
        
        QTimer.singleShot(0, update_buttons)
    
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
                from PySide6.QtCore import QTimer
                from PySide6.QtWidgets import QMessageBox
                def show_warning_async():
                    QMessageBox.warning(self, "경고", f"1-{self.total_pages} 범위의 페이지를 입력하세요.")
                QTimer.singleShot(0, show_warning_async)
        except ValueError:
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QMessageBox
            def show_warning_async():
                QMessageBox.warning(self, "경고", "올바른 페이지 번호를 입력하세요.")
            QTimer.singleShot(0, show_warning_async)
    
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
        # load_torrents()에서 자동으로 start_thumbnail_update()를 호출하지만,
        # 정렬 시에도 명시적으로 우선순위 대기열에 현재 페이지 항목 추가를 보장하기 위해
        # QTimer를 사용하여 약간의 지연 후 호출 (load_torrents()의 QTimer와 중복되지만 안전을 위해)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(200, self.start_thumbnail_update)  # 200ms 후 시작 (load_torrents의 100ms보다 늦게)
    
    def fetch_torrents(self):
        """새 토렌트 수집"""
        if self.scraper_thread and self.scraper_thread.isRunning():
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QMessageBox
            def show_warning_async():
                QMessageBox.warning(self, "경고", "이미 수집 작업이 진행 중입니다.")
            QTimer.singleShot(0, show_warning_async)
            return
        
        # 선택된 소스 가져오기
        source_key = self.source_combo.currentData()
        
        # 검색어 가져오기
        search_query = self.search_input.text().strip()
        search_query = search_query if search_query else None
        
        self.fetch_btn.setEnabled(False)
        self.source_combo.setEnabled(False)
        self.search_input.setEnabled(False)
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
            enable_thumbnail=enable_thumb,
            query=search_query,
            db_writer=self.db_writer  # DB Writer Thread 전달
        )
        self.scraper_thread.progress.connect(self.on_scrape_progress)
        self.scraper_thread.finished.connect(self.on_scrape_finished)
        self.scraper_thread.error.connect(self.on_scrape_error)
        self.scraper_thread.start()
    
    def stop_scraping(self):
        """스크래핑 중단"""
        if self.scraper_thread and self.scraper_thread.isRunning():
            self.scraper_thread.stop()
            from PySide6.QtCore import QTimer
            def update_ui_async():
                self.stop_btn.setEnabled(False)
                self.status_bar.showMessage("수집 중단 중...")
            QTimer.singleShot(0, update_ui_async)
    
    def on_scrape_progress(self, value: int, message: str):
        """스크래핑 진행 상황 업데이트"""
        from PySide6.QtCore import QTimer
        def update_ui_async():
            self.progress_bar.setValue(value)
            self.status_bar.showMessage(message)
        QTimer.singleShot(0, update_ui_async)
    
    def on_scrape_finished(self, added_count: int, updated_count: int, was_stopped: bool = False):
        """스크래핑 완료"""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox
        
        total = added_count + updated_count
        
        def update_ui_async():
            self.fetch_btn.setEnabled(True)
            self.source_combo.setEnabled(True)
            self.search_input.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.stop_btn.setVisible(False)
            self.progress_bar.setVisible(False)
            
            if was_stopped:
                message = f"수집 중단: 신규 {added_count}개, 업데이트 {updated_count}개 (총 {total}개) 저장됨"
                self.status_bar.showMessage(message)
                
                def show_info_async():
                    QMessageBox.information(
                        self, 
                        "수집 중단", 
                        f"수집이 중단되었습니다.\n\n"
                        f"지금까지 수집한 데이터:\n"
                        f"신규 추가: {added_count}개\n"
                        f"기존 업데이트: {updated_count}개\n"
                        f"총 처리: {total}개"
                    )
                QTimer.singleShot(0, show_info_async)
            else:
                message = f"수집 완료: 신규 {added_count}개, 업데이트 {updated_count}개 (총 {total}개)"
                self.status_bar.showMessage(message)
                
                def show_info_async():
                    QMessageBox.information(
                        self, 
                        "완료", 
                        f"신규 추가: {added_count}개\n"
                        f"기존 업데이트: {updated_count}개\n"
                        f"총 처리: {total}개"
                    )
                QTimer.singleShot(0, show_info_async)
        
        QTimer.singleShot(0, update_ui_async)
        
        # 목록 새로고침
        self.load_torrents()
        
        # 수집 완료 후 이미지 없는 항목들의 썸네일 업데이트 시작
        if not was_stopped:  # 중단된 경우가 아닐 때만
            print(f"[스크래핑] 수집 완료 후 이미지 없는 항목들의 썸네일 업데이트 시작...")
            from PySide6.QtCore import QTimer
            # DB 저장이 완전히 끝난 후 썸네일 업데이트 시작 (500ms 지연)
            QTimer.singleShot(500, self.start_thumbnail_update_for_missing)
    
    def on_scrape_error(self, error_msg: str):
        """스크래핑 오류"""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox
        
        def update_ui_async():
            self.fetch_btn.setEnabled(True)
            self.source_combo.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.stop_btn.setVisible(False)
            self.progress_bar.setVisible(False)
            self.status_bar.showMessage("수집 실패")
            
            def show_error_async():
                QMessageBox.critical(self, "오류", f"토렌트 수집 실패:\n{error_msg}")
            QTimer.singleShot(0, show_error_async)
        
        QTimer.singleShot(0, update_ui_async)
    
    def show_about(self):
        """정보 다이얼로그 표시"""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox
        
        sources = self.scraper_manager.get_available_sources()
        sources_list = "<br>".join([f"• {info['name']}: {info['description']}" 
                                    for info in sources.values()])
        
        def show_about_async():
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
        QTimer.singleShot(0, show_about_async)

    def fix_missing_dates(self):
        """업로드 날짜가 비어있는 항목을 원본에서 보정"""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox
        
        def fix_dates_async():
            session = self.db.get_session()
            try:
                fixed = self.db.backfill_missing_dates(session, limit=1000)
                
                def show_result_async():
                    QMessageBox.information(self, "날짜 보정", f"보정된 항목: {fixed}개")
                    if fixed:
                        self.load_torrents()
                QTimer.singleShot(0, show_result_async)
            except Exception as e:
                def show_error_async():
                    QMessageBox.critical(self, "오류", f"날짜 보정 실패: {e}")
                QTimer.singleShot(0, show_error_async)
            finally:
                session.close()
        
        QTimer.singleShot(0, fix_dates_async)

    def start_thumbnail_update_for_missing(self):
        """이미지 없는 모든 항목의 썸네일 업데이트 시작 (수집 완료 후 호출)"""
        try:
            session = self.db.get_session()
            try:
                from database.models import Torrent
                
                # 썸네일이 없는 모든 토렌트 조회
                missing_thumbnails = session.query(Torrent.id).filter(
                    (Torrent.thumbnail_url.is_(None)) | (Torrent.thumbnail_url == '')
                ).all()
                
                if not missing_thumbnails:
                    print(f"[썸네일] 이미지 없는 항목이 없습니다.")
                    return
                
                missing_ids = [t.id for t in missing_thumbnails]
                print(f"[썸네일] 이미지 없는 항목 {len(missing_ids)}개 발견...")
                
                # 기존 썸네일 업데이트 스레드가 실행 중이면 큐에 없는 항목만 추가
                if self.thumbnail_thread and self.thumbnail_thread.isRunning():
                    print(f"[썸네일] 기존 썸네일 업데이트 실행 중, 큐에 없는 항목만 추가...")
                    # 큐에 이미 있는 항목 확인
                    existing_ids = set()
                    import queue
                    
                    # priority_list에서 확인
                    if hasattr(self.thumbnail_thread, 'priority_list') and hasattr(self.thumbnail_thread, 'priority_lock'):
                        with self.thumbnail_thread.priority_lock:
                            unprocessed_priority = [x for x in self.thumbnail_thread.priority_list if not x['processed']]
                            for entry in unprocessed_priority:
                                item = entry['item']
                                existing_ids.add(item['id'])
                    
                    # main_list에서 확인
                    if hasattr(self.thumbnail_thread, 'main_list') and hasattr(self.thumbnail_thread, 'main_lock'):
                        with self.thumbnail_thread.main_lock:
                            unprocessed_main = [x for x in self.thumbnail_thread.main_list if not x['processed']]
                            for entry in unprocessed_main:
                                item = entry['item']
                                existing_ids.add(item['id'])
                    
                    # server_queues에서 확인
                    if hasattr(self.thumbnail_thread, 'server_queues'):
                        for q in self.thumbnail_thread.server_queues.values():
                            temp_items = []
                            while not q.empty():
                                try:
                                    item = q.get_nowait()
                                    existing_ids.add(item['id'])
                                    temp_items.append(item)
                                except queue.Empty:
                                    break
                            for item in temp_items:
                                q.put(item)
                    
                    # 큐에 없는 항목만 필터링
                    new_ids = [id for id in missing_ids if id not in existing_ids]
                    
                    if new_ids:
                        print(f"[썸네일] 큐에 없는 항목 {len(new_ids)}개를 큐에 추가...")
                        # DB에서 새 항목 정보 가져오기
                        new_torrents = session.query(Torrent).filter(
                            Torrent.id.in_(new_ids),
                            (Torrent.thumbnail_url == None) | (Torrent.thumbnail_url == '')
                        ).all()
                        
                        # main_queue에 추가
                        if hasattr(self.thumbnail_thread, 'main_queue'):
                            for torrent in new_torrents:
                                item = {
                                    'id': torrent.id,
                                    'title': torrent.title,
                                    'is_priority': False
                                }
                                self.thumbnail_thread.main_queue.put(item)
                        print(f"[썸네일] {len(new_torrents)}개 항목을 큐에 추가 완료")
                    else:
                        print(f"[썸네일] 모든 항목이 이미 큐에 있습니다.")
                else:
                    # 스레드가 실행 중이 아니면 새로 시작
                    print(f"[썸네일] 썸네일 업데이트 스레드 시작...")
                    self.thumbnail_thread = ThumbnailUpdateThread(
                        self.db,
                        priority_ids=missing_ids,  # 모든 이미지 없는 항목을 우선순위로
                        db_writer=self.db_writer
                    )
                    self.thumbnail_thread.progress.connect(self.on_thumbnail_progress)
                    self.thumbnail_thread.finished.connect(self.on_thumbnail_finished)
                    self.thumbnail_thread.error.connect(self.on_thumbnail_error)
                    self.thumbnail_thread.thumbnail_updated.connect(self.on_thumbnail_item_updated)
                    self.thumbnail_thread.start()
                
            finally:
                session.close()
        except Exception as e:
            print(f"[썸네일] 업데이트 시작 오류: {e}")
            import traceback
            traceback.print_exc()
    
    def start_thumbnail_update(self):
        """썸네일 백그라운드 업데이트 시작 (현재 페이지 우선)"""
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
        
        # 이미 실행 중이면 우선순위만 업데이트 (비동기로 처리하여 UI 블로킹 방지)
        if self.thumbnail_thread and self.thumbnail_thread.isRunning():
            if priority_ids:
                # 제목 추출하여 표시
                titles = []
                for t in torrents:
                    if t.id in priority_ids:
                        titles.append(t.title[:40] if t.title else f"ID:{t.id}")
                titles_str = ", ".join(titles[:5])  # 처음 5개만 표시
                if len(titles) > 5:
                    titles_str += f" 외 {len(titles) - 5}개"
                # 비동기로 실행하여 UI 블로킹 방지
                from PySide6.QtCore import QTimer
                def update_priority_async():
                    self.thumbnail_thread.update_priority_ids(priority_ids, force_first=True)
                QTimer.singleShot(0, update_priority_async)
            return
        
        # 새로 시작
        if priority_ids:
            # 제목 추출하여 표시
            titles = []
            for t in torrents:
                if t.id in priority_ids:
                    titles.append(t.title[:40] if t.title else f"ID:{t.id}")
            titles_str = ", ".join(titles[:5])  # 처음 5개만 표시
            if len(titles) > 5:
                titles_str += f" 외 {len(titles) - 5}개"
        self.thumbnail_thread = ThumbnailUpdateThread(self.db, priority_ids, db_writer=self.db_writer)
        self.thumbnail_thread.progress.connect(self.on_thumbnail_progress)
        self.thumbnail_thread.finished.connect(self.on_thumbnail_finished)
        self.thumbnail_thread.error.connect(self.on_thumbnail_error)
        self.thumbnail_thread.thumbnail_updated.connect(self.on_thumbnail_item_updated)
        self.thumbnail_thread.start()
    
    def on_thumbnail_progress(self, value: int, message: str):
        """썸네일 업데이트 진행 상황"""
        # 상태바에만 표시 (조용하게)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.status_bar.showMessage(f"[백그라운드] {message}"))
    
    def on_thumbnail_finished(self, updated_count: int):
        """썸네일 업데이트 완료"""
        from PySide6.QtCore import QTimer
        
        if updated_count > 0:
            print(f"[썸네일] 백그라운드 업데이트 완료: {updated_count}개")
            QTimer.singleShot(0, lambda: self.status_bar.showMessage(f"썸네일 {updated_count}개 업데이트 완료", 3000))
            # 목록 새로고침 (썸네일이 보이도록, 단 썸네일 업데이트는 다시 시작하지 않음)
            QTimer.singleShot(0, lambda: self.torrent_list.refresh_thumbnails())  # 썸네일만 새로고침
        else:
            print("[썸네일] 업데이트할 항목이 없습니다.")
            QTimer.singleShot(0, lambda: self.status_bar.showMessage("썸네일 업데이트 완료 (모든 항목 최신)", 2000))
        
        # 썸네일 교체 요청 항목 중 아직 활성화되지 않은 버튼들 활성화 (썸네일을 찾지 못한 경우 포함)
        if self.pending_replace_ids:
            for torrent_id in list(self.pending_replace_ids):
                self.torrent_list.enable_replace_button(torrent_id)
                self.pending_replace_ids.discard(torrent_id)
    
    def on_thumbnail_error(self, error_msg: str):
        """썸네일 업데이트 오류"""
        from PySide6.QtCore import QTimer
        print(f"[썸네일] 오류: {error_msg}")
        QTimer.singleShot(0, lambda: self.status_bar.showMessage(f"썸네일 업데이트 오류: {error_msg}", 3000))
        # 썸네일 교체 요청 항목이면 버튼 활성화 (오류 발생 시에도)
        # pending_replace_ids에서 모든 항목 확인 (오류 메시지에 torrent_id가 없을 수 있음)
        # 하지만 일반적으로 오류는 전체 업데이트 스레드에서 발생하므로 여기서는 처리하지 않음
    
    def on_thumbnail_item_updated(self, torrent_id: int, thumbnail_url: str):
        """개별 썸네일 업데이트 (현재 페이지 항목)"""
        # 현재 표시된 리스트에서 해당 항목 찾아서 GUI 업데이트
        self.torrent_list.update_thumbnail_by_id(torrent_id, thumbnail_url)
        # 썸네일 교체 요청 항목이면 버튼 활성화
        if torrent_id in self.pending_replace_ids:
            self.torrent_list.enable_replace_button(torrent_id)
            self.pending_replace_ids.discard(torrent_id)

    def on_replace_thumbnail_requested(self, torrent_id: int):
        """교체 버튼 클릭 처리: 최우선으로 처리"""
        try:
            # 디버그 정보는 비동기로 출력 (UI 블로킹 방지)
            from PySide6.QtCore import QTimer
            def print_debug_info():
                try:
                    debug_session = self.db.get_session()
                    try:
                        from database.models import Torrent
                        import json
                        debug_torrent = debug_session.get(Torrent, torrent_id)
                        if debug_torrent:
                            searched_servers_debug = []
                            if debug_torrent.thumbnail_searched_servers:
                                try:
                                    searched_servers_debug = json.loads(debug_torrent.thumbnail_searched_servers)
                                except (json.JSONDecodeError, TypeError):
                                    searched_servers_debug = []
                            
                            # 제목 형태에 따라 처리 가능한 서버 확인
                            title_text_debug = debug_torrent.title or ''
                            fc2_patterns_debug = [
                                r'FC2[-\s]?PPV[-\s]?(\d{6,8})',  # FC2-PPV-숫자
                                r'FC2[-\s]?PPV',                  # FC2-PPV
                                r'FC2PPV',                        # FC2PPV
                                r'FC2\s+PPV'                      # FC2 PPV
                            ]
                            is_fc2_debug = False
                            if title_text_debug:
                                title_upper_debug = title_text_debug.upper()
                                for pattern in fc2_patterns_debug:
                                    if re.search(pattern, title_upper_debug):
                                        is_fc2_debug = True
                                        break
                            
                            # FC2 항목: FC2PPV, JAVDB, JAVGURU만 처리 가능
                            # FC2가 아닌 항목: JAVDB, JAVBEE, JAVGURU만 처리 가능
                            if is_fc2_debug:
                                all_servers_debug = {'fc2ppv', 'javdb', 'javguru'}  # FC2 항목: FC2PPV, JAVDB, JAVGURU
                            else:
                                all_servers_debug = {'javdb', 'javbee', 'javguru'}  # FC2가 아닌 항목: JAVDB, JAVBEE, JAVGURU
                            
                            remaining_servers_debug = all_servers_debug - set(searched_servers_debug)
                            
                            print("=" * 80)
                            print(f"[썸네일 교체 요청] ID: {torrent_id}")
                            print(f"  제목: {title_text_debug[:80]}")
                            print(f"  FC2 항목 여부: {is_fc2_debug}")
                            print(f"  처리 가능한 검색 서버: {sorted(all_servers_debug)}")
                            print(f"  DB에서 이미 검색한 서버: {sorted(searched_servers_debug) if searched_servers_debug else '(없음)'}")
                            print(f"  아직 검색하지 않은 서버: {sorted(remaining_servers_debug) if remaining_servers_debug else '(없음)'}")
                            print(f"  DB 썸네일 URL: {debug_torrent.thumbnail_url or '(없음)'}")
                            print(f"  thumbnail_searched_servers 원본: {debug_torrent.thumbnail_searched_servers or '(없음)'}")
                            print("=" * 80)
                        else:
                            print(f"[썸네일 교체 요청] ID: {torrent_id} - DB에서 찾을 수 없음")
                    finally:
                        debug_session.close()
                except Exception as e:
                    print(f"[썸네일 교체 요청] 디버그 정보 출력 오류: {e}")
            
            # 비동기로 디버그 정보 출력 (UI 블로킹 방지)
            QTimer.singleShot(0, print_debug_info)
            
            # 모든 처리를 비동기로 실행하여 UI 블로킹 방지
            def process_replace_async():
                # 백그라운드 썸네일 업데이트가 실행 중이면 우선순위 큐에 최우선으로 추가
                if self.thumbnail_thread and self.thumbnail_thread.isRunning():
                    # 썸네일 교체 요청 항목 추적 (완료 후 버튼 활성화를 위해)
                    self.pending_replace_ids.add(torrent_id)
                    
                    # 상태바 메시지도 비동기로 처리
                    QTimer.singleShot(0, lambda: self.status_bar.showMessage(f"썸네일 교체 최우선 처리 중... (ID: {torrent_id})", 0))
                    
                    # 비동기로 우선순위 큐에 최우선으로 추가
                    def update_priority_async():
                        self.thumbnail_thread.update_priority_ids([torrent_id], force_first=True)
                    QTimer.singleShot(0, update_priority_async)
                    
                    # 타임아웃 설정: 30초 후에도 버튼이 활성화되지 않으면 자동 활성화
                    def timeout_handler():
                        if torrent_id in self.pending_replace_ids:
                            print(f"[썸네일 교체] 타임아웃: ID {torrent_id} 버튼 자동 활성화")
                            self.torrent_list.enable_replace_button(torrent_id)
                            self.pending_replace_ids.discard(torrent_id)
                    QTimer.singleShot(30000, timeout_handler)  # 30초 후
                else:
                    # 백그라운드 업데이트가 없으면 기존 방식으로 처리
                    self.replace_queue.put(torrent_id)
                    # 썸네일 교체 요청 항목 추적 (완료 후 버튼 활성화를 위해)
                    self.pending_replace_ids.add(torrent_id)
                    queue_size = self.replace_queue.qsize()
                    
                    # 상태바 메시지도 비동기로 처리
                    if queue_size == 1:
                        QTimer.singleShot(0, lambda: self.status_bar.showMessage(f"썸네일 교체 시작 (ID: {torrent_id})", 0))
                    else:
                        QTimer.singleShot(0, lambda: self.status_bar.showMessage(f"썸네일 교체 대기 중... ({queue_size}개 대기)", 0))
                    
                    # 현재 작업 중이 아니면 큐 처리 시작
                    if self.replace_worker is None or not self.replace_worker.isRunning():
                        self._process_replace_queue()
            
            # 비동기로 처리 실행
            QTimer.singleShot(0, process_replace_async)
        except Exception as e:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.status_bar.showMessage(f"썸네일 교체 오류: {e}", 3000))
            QTimer.singleShot(0, lambda: self.torrent_list.enable_replace_button(torrent_id))
    
    def _process_replace_queue(self):
        """교체 큐에서 다음 작업 처리"""
        try:
            if self.replace_queue.empty():
                return
            
            # 큐에서 다음 ID 가져오기
            torrent_id = self.replace_queue.get()
            
            # 작업 스레드 생성
            self.replace_worker = SingleThumbnailReplaceThread(self.db, torrent_id, self.shared_image_finder, db_writer=self.db_writer)
            
            # 완료/오류 시 다음 큐 항목 처리
            def _on_completed(tid, url):
                from PySide6.QtCore import QTimer
                self.on_thumbnail_item_updated(tid, url)
                remaining = self.replace_queue.qsize()
                if remaining > 0:
                    QTimer.singleShot(0, lambda: self.status_bar.showMessage(f"✅ 교체 완료! 남은 작업: {remaining}개", 2000))
                else:
                    QTimer.singleShot(0, lambda: self.status_bar.showMessage("✅ 모든 썸네일 교체 완료!", 2000))
                QTimer.singleShot(0, lambda: self.torrent_list.enable_replace_button(tid))
                # 다음 큐 항목 처리
                QTimer.singleShot(0, self._process_replace_queue)
            
            def _on_error(e):
                from PySide6.QtCore import QTimer
                remaining = self.replace_queue.qsize()
                QTimer.singleShot(0, lambda: self.status_bar.showMessage(f"❌ 교체 실패: {e} (남은 작업: {remaining}개)", 3000))
                QTimer.singleShot(0, lambda: self.torrent_list.enable_replace_button(torrent_id))
                # 다음 큐 항목 처리
                QTimer.singleShot(0, self._process_replace_queue)
            
            self.replace_worker.updated.connect(_on_completed)
            self.replace_worker.error.connect(_on_error)
            self.replace_worker.start()
            
        except Exception as e:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.status_bar.showMessage(f"큐 처리 오류: {e}", 3000))
    
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
    
    def _on_db_batch_completed(self, stats: dict):
        """DB 배치 저장 완료 시그널 처리"""
        self.db_writer_stats['added'] += stats.get('added', 0)
        self.db_writer_stats['updated'] += stats.get('updated', 0)
        self.db_writer_stats['duplicate'] += stats.get('duplicate', 0)
    
    def closeEvent(self, event):
        """윈도우 닫기 이벤트 (스레드 정리)"""
        print("[종료] 앱 종료 중... 스레드 정리")
        
        # DB Writer Thread 정리
        if self.db_writer and self.db_writer.isRunning():
            print("[종료] DB Writer Thread 중지 중...")
            self.db_writer.stop()
            self.db_writer.wait(2000)
            if self.db_writer.isRunning():
                print("[종료] DB Writer Thread 강제 종료")
                self.db_writer.terminate()
        
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
