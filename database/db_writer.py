"""
중앙화된 DB Writer Thread
모든 DB write 작업을 큐를 통해 순차적으로 처리
"""
from PySide6.QtCore import QThread, Signal
from queue import Queue, Empty
from enum import Enum
from typing import Dict, Any, Optional, List
import time


class WriteOperationType(Enum):
    """Write 작업 타입"""
    ADD_TORRENT = "add_torrent"
    UPDATE_THUMBNAIL = "update_thumbnail"
    BATCH_ADD_TORRENTS = "batch_add_torrents"
    BATCH_UPDATE_THUMBNAILS = "batch_update_thumbnails"


class WriteOperation:
    """Write 작업 객체"""
    def __init__(self, op_type: WriteOperationType, data: Dict[str, Any], callback_id: Optional[str] = None):
        self.op_type = op_type
        self.data = data
        self.callback_id = callback_id
        self.timestamp = time.time()


class DBWriterThread(QThread):
    """
    단일 스레드로 모든 DB write 작업 처리
    SQLite의 동시 쓰기 제약 문제 해결
    """
    # Signals
    operation_completed = Signal(str, bool, object)  # callback_id, success, result
    error_occurred = Signal(str, str)  # operation_type, error_message
    batch_completed = Signal(dict)  # stats: {'added': int, 'updated': int, 'duplicate': int}
    
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.queue = Queue()
        self._running = True
        
    def add_operation(self, operation: WriteOperation):
        """작업 추가"""
        self.queue.put(operation)
    
    def add_torrent(self, torrent_data: Dict[str, Any], callback_id: Optional[str] = None):
        """토렌트 추가 요청"""
        op = WriteOperation(WriteOperationType.ADD_TORRENT, torrent_data, callback_id)
        self.queue.put(op)
    
    def update_thumbnail(self, torrent_id: int, thumbnail_url: str, callback_id: Optional[str] = None):
        """썸네일 업데이트 요청"""
        op = WriteOperation(
            WriteOperationType.UPDATE_THUMBNAIL,
            {'torrent_id': torrent_id, 'thumbnail_url': thumbnail_url},
            callback_id
        )
        self.queue.put(op)
    
    def batch_add_torrents(self, torrents: List[Dict[str, Any]], callback_id: Optional[str] = None):
        """배치 토렌트 추가"""
        op = WriteOperation(
            WriteOperationType.BATCH_ADD_TORRENTS,
            {'torrents': torrents},
            callback_id
        )
        self.queue.put(op)
    
    def batch_update_thumbnails(self, updates: List[Dict[str, Any]], callback_id: Optional[str] = None):
        """배치 썸네일 업데이트"""
        op = WriteOperation(
            WriteOperationType.BATCH_UPDATE_THUMBNAILS,
            {'updates': updates},
            callback_id
        )
        self.queue.put(op)
    
    def stop(self):
        """스레드 정지"""
        self._running = False
        # 빈 작업을 넣어 블로킹 해제
        self.queue.put(None)
    
    def run(self):
        """메인 루프 - 큐에서 작업을 가져와 순차 처리"""
        from database.models import Torrent
        
        session = self.db.get_session()
        
        processed_count = 0  # 처리한 작업 수
        
        try:
            while self._running:
                try:
                    # 작업 가져오기 (timeout 1초)
                    operation = self.queue.get(timeout=1)
                    
                    if operation is None:  # 종료 신호
                        break
                    
                    processed_count += 1
                    
                    success = False
                    result = None
                    error_msg = None
                    
                    try:
                        # 작업 타입별 처리
                        if operation.op_type == WriteOperationType.ADD_TORRENT:
                            result = self._add_torrent(session, operation.data)
                            success = True
                            
                        elif operation.op_type == WriteOperationType.UPDATE_THUMBNAIL:
                            self._update_thumbnail(session, operation.data)
                            success = True
                            result = operation.data['torrent_id']
                            
                        elif operation.op_type == WriteOperationType.BATCH_ADD_TORRENTS:
                            torrent_count = len(operation.data['torrents'])
                            result = self._batch_add_torrents(session, operation.data['torrents'])
                            success = True
                            # 배치 완료 시그널 발생
                            if isinstance(result, dict):
                                self.batch_completed.emit(result)
                            
                        elif operation.op_type == WriteOperationType.BATCH_UPDATE_THUMBNAILS:
                            update_count = len(operation.data['updates'])
                            self._batch_update_thumbnails(session, operation.data['updates'])
                            success = True
                            result = update_count
                        
                        # 커밋
                        session.commit()
                        
                    except Exception as e:
                        error_msg = str(e)
                        session.rollback()
                        print(f"[DBWriter] ❌ 오류 발생: {error_msg} (타입: {operation.op_type.value})")
                        import traceback
                        traceback.print_exc()
                        self.error_occurred.emit(operation.op_type.value, error_msg)
                    
                    # 콜백 실행
                    if operation.callback_id:
                        self.operation_completed.emit(operation.callback_id, success, result)
                    
                    self.queue.task_done()
                    
                except Empty:
                    # 타임아웃 - 정상적인 대기 상태
                    continue
                except Exception as e:
                    print(f"[DBWriter] ❌ 예상치 못한 오류: {e}")
                    import traceback
                    traceback.print_exc()
                    
        finally:
            session.close()
    
    def _add_torrent(self, session, torrent_data: Dict[str, Any]) -> str:
        """토렌트 추가 (내부 메서드)"""
        from database.models import Torrent
        
        # 중복 확인: source_id 또는 제목으로 확인
        existing = None
        source_id = torrent_data.get('source_id')
        source_site = torrent_data.get('source_site')
        title = torrent_data.get('title')
        
        # 1순위: source_id로 확인
        if source_id and source_site:
            existing = session.query(Torrent).filter_by(
                source_id=source_id,
                source_site=source_site
            ).first()
        
        # 2순위: 제목으로 확인 (source_id가 없거나 못 찾은 경우)
        if not existing and title:
            existing = session.query(Torrent).filter_by(
                title=title
            ).first()
        
        if existing:
            # 중복 항목 발견 - 다운로드수 비교하여 업데이트 여부 결정
            new_downloads = torrent_data.get('downloads', 0) or 0
            existing_downloads = existing.downloads or 0
            
            # 새 항목의 다운로드수가 더 많으면 업데이트
            if new_downloads > existing_downloads:
                # 다운로드수가 더 많은 항목으로 업데이트
                for key in ['downloads', 'seeders', 'leechers', 'completed', 'magnet_link', 'torrent_link', 'size', 'size_bytes']:
                    if key in torrent_data:
                        setattr(existing, key, torrent_data[key])
                
                # 썸네일이 없었는데 새로 생겼으면 업데이트
                if not existing.thumbnail_url and torrent_data.get('thumbnail_url'):
                    existing.thumbnail_url = torrent_data.get('thumbnail_url')
                    existing.snapshot_urls = torrent_data.get('snapshot_urls', '')
                
                # 인기도 점수 재계산
                existing.calculate_popularity()
                return 'updated'
            
            # 기존 항목이 다운로드수가 더 많거나 같으면 중복으로 처리
            # 단, _is_update 플래그가 있으면 통계 정보만 업데이트
            is_update = torrent_data.get('_is_update', False)
            if is_update:
                for key in ['downloads', 'seeders', 'leechers', 'completed']:
                    if key in torrent_data:
                        setattr(existing, key, torrent_data[key])
                existing.calculate_popularity()
                return 'updated'
            
            return 'duplicate'
        else:
            # 신규 추가
            # genres 필드는 별도 처리 (Many-to-Many 관계)
            genres_list = torrent_data.get('genres', [])
            
            # genres를 제외한 나머지 필드로 Torrent 객체 생성
            torrent_kwargs = {k: v for k, v in torrent_data.items() if not k.startswith('_') and k != 'genres'}
            torrent = Torrent(**torrent_kwargs)
            session.add(torrent)
            session.flush()  # ID를 얻기 위해 flush
            
            # genres 처리: 문자열 리스트를 Genre 객체로 변환
            if genres_list:
                from database.models import Genre
                for genre_name in genres_list:
                    if isinstance(genre_name, str) and genre_name.strip():
                        # Genre 객체 찾기 또는 생성
                        genre = session.query(Genre).filter_by(name=genre_name).first()
                        if not genre:
                            genre = Genre(name=genre_name)
                            session.add(genre)
                            session.flush()
                        torrent.genres.append(genre)
            
            return 'added'
    
    def _update_thumbnail(self, session, data: Dict[str, Any]):
        """썸네일 업데이트 (내부 메서드)"""
        from database.models import Torrent
        
        torrent_id = data.get('torrent_id')
        thumbnail_url = data.get('thumbnail_url', '')
        
        # 타입 검증
        if not torrent_id:
            return
        
        if not isinstance(torrent_id, int):
            try:
                torrent_id = int(torrent_id)
            except:
                return
        
        if not isinstance(thumbnail_url, str):
            thumbnail_url = str(thumbnail_url) if thumbnail_url else ''
        
        try:
            # SQLAlchemy 1.4/2.0 호환 조회
            try:
                torrent = session.get(Torrent, torrent_id)
            except Exception:
                torrent = session.query(Torrent).get(torrent_id)
            
            if torrent:
                # thumbnail_url이 문자열인지 확인
                if not isinstance(thumbnail_url, str):
                    thumbnail_url = str(thumbnail_url) if thumbnail_url else ''
                
                torrent.thumbnail_url = thumbnail_url
            # torrent가 없으면 조용히 무시
        except Exception as e:
            print(f"[DBWriter] ⚠️ 썸네일 업데이트 오류 (torrent_id={torrent_id}): {e}")
            raise
    
    def _batch_add_torrents(self, session, torrents: List[Dict[str, Any]]) -> Dict[str, int]:
        """배치 토렌트 추가"""
        stats = {'added': 0, 'updated': 0, 'duplicate': 0}
        
        for idx, torrent_data in enumerate(torrents):
            result = self._add_torrent(session, torrent_data)
            stats[result] = stats.get(result, 0) + 1
        
        return stats
    
    def _batch_update_thumbnails(self, session, updates: List[Dict[str, Any]]):
        """배치 썸네일 업데이트"""
        for update in updates:
            self._update_thumbnail(session, update)

