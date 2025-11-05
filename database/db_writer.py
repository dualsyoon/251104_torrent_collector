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
        
        try:
            while self._running:
                try:
                    # 작업 가져오기 (timeout 1초)
                    operation = self.queue.get(timeout=1)
                    
                    if operation is None:  # 종료 신호
                        break
                    
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
                            result = self._batch_add_torrents(session, operation.data['torrents'])
                            success = True
                            
                        elif operation.op_type == WriteOperationType.BATCH_UPDATE_THUMBNAILS:
                            self._batch_update_thumbnails(session, operation.data['updates'])
                            success = True
                            result = len(operation.data['updates'])
                        
                        # 커밋
                        session.commit()
                        
                    except Exception as e:
                        error_msg = str(e)
                        session.rollback()
                        self.error_occurred.emit(operation.op_type.value, error_msg)
                    
                    # 콜백 실행
                    if operation.callback_id:
                        self.operation_completed.emit(operation.callback_id, success, result)
                    
                    self.queue.task_done()
                    
                except Empty:
                    continue
                except Exception as e:
                    print(f"[DBWriter] 예상치 못한 오류: {e}")
                    
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
            # 업데이트
            is_update = torrent_data.get('_is_update', False)
            if is_update:
                for key in ['downloads', 'seeders', 'leechers', 'completed']:
                    if key in torrent_data:
                        setattr(existing, key, torrent_data[key])
                return 'updated'
            return 'duplicate'
        else:
            # 신규 추가
            torrent = Torrent(**{k: v for k, v in torrent_data.items() if not k.startswith('_')})
            session.add(torrent)
            return 'added'
    
    def _update_thumbnail(self, session, data: Dict[str, Any]):
        """썸네일 업데이트 (내부 메서드)"""
        from database.models import Torrent
        
        torrent_id = data['torrent_id']
        thumbnail_url = data['thumbnail_url']
        
        torrent = session.get(Torrent, torrent_id)
        if torrent:
            torrent.thumbnail_url = thumbnail_url
    
    def _batch_add_torrents(self, session, torrents: List[Dict[str, Any]]) -> Dict[str, int]:
        """배치 토렌트 추가"""
        stats = {'added': 0, 'updated': 0, 'duplicate': 0}
        
        for torrent_data in torrents:
            result = self._add_torrent(session, torrent_data)
            stats[result] = stats.get(result, 0) + 1
        
        return stats
    
    def _batch_update_thumbnails(self, session, updates: List[Dict[str, Any]]):
        """배치 썸네일 업데이트"""
        for update in updates:
            self._update_thumbnail(session, update)

