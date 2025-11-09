"""토렌트 리스트 위젯"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QLabel, QPushButton, QHeaderView, QMessageBox, QAbstractItemView
)
from PySide6.QtCore import Qt, Signal, QUrl, QSize, QSettings, QEvent, QTimer
import time
from PySide6.QtGui import QDesktopServices, QPixmap, QIcon, QCursor
from typing import List, Dict, Optional
from database.models import Torrent
from .image_loader import ImageCache, ImageDownloader
from config import IMAGE_CACHE_SIZE


class TorrentListWidget(QWidget):
    """토렌트 목록 표시 위젯"""
    
    # 시그널 정의
    refresh_requested = Signal()
    replace_thumbnail_requested = Signal(int)  # torrent_id
    sort_requested = Signal(str, str)  # (column_name, order: 'asc' or 'desc')
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.torrents: List[Torrent] = []
        
        # 정렬 상태 추적
        self.current_sort_column = None
        self.current_sort_order = None
        
        # 현재 호버 중인 행 추적
        self.current_hover_row = None
        
        # 이미지 캐시 및 다운로더 (config.py에서 설정)
        self.image_cache = ImageCache(max_cache=IMAGE_CACHE_SIZE)
        self.image_downloader = ImageDownloader(self.image_cache)
        self.image_downloader.image_loaded.connect(self._on_image_loaded)
        self.image_downloader.download_failed.connect(self._on_image_failed)
        
        # 이미지 URL -> 행 번호 매핑 (썸네일)
        self.url_to_rows: Dict[str, List[int]] = {}
        # 행별 로딩 시작 시간 및 타임아웃 타이머 추적
        self.row_loading_start_time: Dict[int, float] = {}  # row -> start_time
        self.row_timeout_timers: Dict[int, 'QTimer'] = {}  # row -> QTimer
        # 스냅샷 비활성화
        # 행 -> 원본 Pixmap 매핑 (호버 미리보기용)
        self.row_to_pixmap: Dict[int, QPixmap] = {}
        # UI 설정 (QSettings에서 불러오기)
        self.settings = QSettings()
        
        # 기존 설정값 가져오기
        saved_width = int(self.settings.value('ui/thumbnail_width', 120))
        saved_height = int(self.settings.value('ui/row_height', 80))
        
        # 너무 큰 값이면 강제로 작은 값으로 변경 (한 번만)
        if saved_width > 150:
            saved_width = 120
            self.settings.setValue('ui/thumbnail_width', 120)
        if saved_height > 100:
            saved_height = 80
            self.settings.setValue('ui/row_height', 80)
        
        self.thumbnail_col_width = saved_width
        self.row_height = saved_height
        self.enable_hover_preview = self.settings.value('ui/hover_preview', True, type=bool)
        # 미리보기 라벨 (오버레이)
        self.preview_label = None
        
        self.init_ui()
    
    def init_ui(self):
        """UI 초기화"""
        layout = QVBoxLayout(self)
        
        # 상단 정보 바
        info_layout = QHBoxLayout()
        self.info_label = QLabel("토렌트 0개")
        info_layout.addWidget(self.info_label)
        info_layout.addStretch()
        
        refresh_btn = QPushButton("🔄 새로고침")
        refresh_btn.clicked.connect(self.refresh_requested.emit)
        info_layout.addWidget(refresh_btn)
        
        layout.addLayout(info_layout)
        
        # 토렌트 테이블
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            '썸네일', '제목', '크기', '시더', '리처', '다운로드수', '날짜', '썸네일 교체'
        ])
        
        # 아이콘 크기 설정 (썸네일 표시용)
        icon_size = min(self.thumbnail_col_width - 10, self.row_height - 10)
        self.table.setIconSize(QSize(icon_size, icon_size))
        
        # 헤더 설정 (크기 변경 방지)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.setSectionsMovable(False)
        
        # 먼저 제목 컬럼을 Stretch로 설정
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        
        # 나머지 컬럼은 고정 크기로 설정 (제목 제외)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, self.thumbnail_col_width)  # 썸네일
        
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 100)  # 크기
        
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.setColumnWidth(3, 60)   # 시더
        
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        self.table.setColumnWidth(4, 60)   # 리처
        
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        self.table.setColumnWidth(5, 100)  # 다운로드수 (90 -> 100으로 약간 늘림)
        
        header.setSectionResizeMode(6, QHeaderView.Fixed)
        self.table.setColumnWidth(6, 100)  # 날짜
        
        header.setSectionResizeMode(7, QHeaderView.Fixed)
        self.table.setColumnWidth(7, 100)  # 썸네일 교체
        
        # 마우스 트래킹 및 아이템 호버 이벤트
        self.table.setMouseTracking(True)
        self.table.itemEntered.connect(self._on_item_entered)
        self.table.viewport().installEventFilter(self)
        
        # 기본 정렬 비활성화 (DB에서 정렬하도록 변경)
        self.table.setSortingEnabled(False)
        
        # 헤더 클릭 시 DB 정렬
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        
        # 테이블 설정
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        
        # 더블 클릭 시 magnet 링크 복사
        self.table.doubleClicked.connect(self.on_row_double_clicked)
        
        # 스크롤 시 이미지 로딩 (lazy loading)
        self.table.verticalScrollBar().valueChanged.connect(self._on_scroll)
        
        layout.addWidget(self.table)
    
    def refresh_thumbnails(self):
        """현재 표시된 썸네일만 새로고침 (DB에서 최신 데이터 다시 로드하지 않음)"""
        # 호버 미리보기 숨김
        self._hide_preview()
        
        # 현재 테이블의 각 행에 대해 썸네일 다시 로드
        for row in range(self.table.rowCount()):
            if row < len(self.torrents):
                torrent = self.torrents[row]
                if torrent.thumbnail_url:
                    # 캐시 무효화하고 다시 로드
                    self.image_cache.remove(torrent.thumbnail_url)
                    self._load_thumbnail(row, torrent.thumbnail_url)
        
        print(f"[UI] 썸네일 새로고침: {len(self.torrents)}개")
    
    def update_thumbnail_by_id(self, torrent_id: int, thumbnail_url: str):
        """특정 토렌트의 썸네일만 업데이트 (ID로 찾기)"""
        # 현재 표시된 torrents에서 해당 ID 찾기
        for row, torrent in enumerate(self.torrents):
            if torrent.id == torrent_id:
                # 썸네일 URL 업데이트
                torrent.thumbnail_url = thumbnail_url
                # 캐시 제거하고 새로 로드
                self.image_cache.remove(thumbnail_url)
                self._load_thumbnail(row, thumbnail_url)
                print(f"[UI] 썸네일 즉시 업데이트: 행 {row}")
                break
    
    def set_torrents(self, torrents: List[Torrent]):
        """토렌트 목록 설정
        
        Args:
            torrents: Torrent 객체 리스트
        """
        # 페이지 변경 시 호버 미리보기 숨김
        self._hide_preview()
        
        # 현재 호버 중인 행 리셋
        self.current_hover_row = None
        
        # 이전 페이지의 진행 중인 이미지 다운로드 취소 (비동기로 처리하여 UI 블로킹 방지)
        def cleanup_async():
            self.image_downloader.cancel_all()
            
            # 이전 페이지의 타임아웃 타이머 모두 정리
            for row in list(self.row_timeout_timers.keys()):
                self._clear_row_timeout(row)
            self.row_loading_start_time.clear()
        
        # 비동기로 정리 작업 실행
        QTimer.singleShot(0, cleanup_async)
        
        # 테이블 설정도 비동기로 처리하여 UI 블로킹 방지
        def setup_table_async():
            self.torrents = torrents
            
            # UI 업데이트를 더 작은 단위로 나눠서 처리
            def update_info_label():
                self.info_label.setText(f"토렌트 {len(torrents)}개")
            
            def update_table_structure():
                # URL 매핑 및 pixmap 캐시 초기화
                self.url_to_rows.clear()
                self.row_to_pixmap.clear()  # 이전 페이지 썸네일 캐시 제거
                # 스냅샷 비활성화
                
                self.table.setRowCount(len(torrents))
                
                # 행 높이 설정 (썸네일 표시를 위해 더 크게)
                self.table.verticalHeader().setDefaultSectionSize(self.row_height)
                
                # 테이블 행 설정 시작
                setup_row_batch(0, batch_size=1)
            
            # 순차적으로 비동기 실행
            QTimer.singleShot(0, update_info_label)
            QTimer.singleShot(0, update_table_structure)
        
        # 비동기로 테이블 설정 실행
        QTimer.singleShot(0, setup_table_async)
        
        # 테이블 행 설정을 배치로 나눠서 처리 (UI 블로킹 방지)
        def setup_row_batch(start_idx: int, batch_size: int = 1):
            """배치 단위로 행 설정 (UI 블로킹 방지)"""
            end_idx = min(start_idx + batch_size, len(torrents))
            
            for row in range(start_idx, end_idx):
                torrent = torrents[row]
                # 썸네일 (컬럼 0)
                thumbnail_item = QTableWidgetItem()
                thumbnail_item.setTextAlignment(Qt.AlignCenter)
                # 로딩 중 표시
                if torrent.thumbnail_url:
                    thumbnail_item.setText("로딩중...")
                else:
                    thumbnail_item.setText("이미지 없음")
                self.table.setItem(row, 0, thumbnail_item)
                
                # 썸네일은 lazy loading으로 처리 (set_torrents 후 _load_visible_images에서 처리)
                
                # 스냅샷 비활성화: 컬럼 없음

                # 제목 (컬럼 1) - 텍스트 드래그 복사 가능하도록 QLabel 사용
                title_label = QLabel(torrent.title)
                title_label.setToolTip(torrent.title)
                title_label.setWordWrap(True)
                title_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
                title_label.setStyleSheet("QLabel { background-color: transparent; padding: 5px; }")
                self.table.setCellWidget(row, 1, title_label)
                # 정렬을 위한 빈 아이템 (텍스트는 비우고 데이터만 설정)
                sort_item = QTableWidgetItem()
                sort_item.setData(Qt.DisplayRole, torrent.title)  # 정렬용 데이터
                sort_item.setText("")  # 표시 텍스트는 비움
                self.table.setItem(row, 1, sort_item)
                
                # 크기
                size_item = QTableWidgetItem(torrent.size or 'N/A')
                size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, 2, size_item)
                
                # 시더
                seeders = torrent.seeders or 0
                seeders_item = QTableWidgetItem()
                seeders_item.setData(Qt.DisplayRole, seeders)  # 숫자로 정렬
                seeders_item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, 3, seeders_item)
                
                # 리처
                leechers = torrent.leechers or 0
                leechers_item = QTableWidgetItem()
                leechers_item.setData(Qt.DisplayRole, leechers)  # 숫자로 정렬
                leechers_item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, 4, leechers_item)
                
                # 다운로드수
                downloads = torrent.downloads or 0
                downloads_item = QTableWidgetItem()
                downloads_item.setData(Qt.DisplayRole, downloads)  # 숫자로 정렬
                downloads_item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, 5, downloads_item)
                
                # 날짜
                date_str = torrent.upload_date.strftime('%Y-%m-%d') if torrent.upload_date else 'N/A'
                date_item = QTableWidgetItem(date_str)
                date_item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, 6, date_item)
                
                # 교체 버튼 (맨 오른쪽)
                replace_btn = QPushButton("썸네일 교체")
                replace_btn.setToolTip("이 썸네일을 다른 소스에서 다시 검색합니다")
                # 포커스 정책: 클릭해도 포커스를 받지 않음 (스크롤 이동 방지)
                replace_btn.setFocusPolicy(Qt.NoFocus)
                # 버튼 스타일 설정 (배경색을 조금 다르게)
                replace_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #4A90E2;
                        color: white;
                        border: 1px solid #357ABD;
                        border-radius: 3px;
                        padding: 4px 8px;
                        font-size: 11px;
                    }
                    QPushButton:hover {
                        background-color: #5A9FF2;
                    }
                    QPushButton:pressed {
                        background-color: #3A80D2;
                    }
                    QPushButton:disabled {
                        background-color: #CCCCCC;
                        color: #666666;
                    }
                """)
                # 클릭 핸들러: 현재 토렌트 ID 전달
                replace_btn.clicked.connect(lambda _, tid=torrent.id: self._on_replace_clicked(tid))
                self.table.setCellWidget(row, 7, replace_btn)
            
            # 다음 배치 처리 (클로저 문제 방지를 위해 end_idx를 명시적으로 전달)
            if end_idx < len(torrents):
                def next_batch():
                    setup_row_batch(end_idx, batch_size)
                QTimer.singleShot(5, next_batch)  # 5ms 후 다음 배치
            else:
                # 모든 행 설정 완료 후 이미지 로딩 시작
                QTimer.singleShot(0, self._load_all_images)

    def _on_replace_clicked(self, torrent_id: int):
        """행의 교체 버튼 클릭 시그널 처리"""
        try:
            # 버튼 상태 변경을 비동기로 처리하여 UI 블로킹 방지
            def update_button_async():
                try:
                    # torrent_id로 행 찾아서 버튼 비활성화
                    for row, torrent in enumerate(self.torrents):
                        if torrent.id == torrent_id:
                            btn = self.table.cellWidget(row, 7)
                            if btn and isinstance(btn, QPushButton):
                                btn.setEnabled(False)
                                btn.setText("검색중...")
                            break
                except Exception as e:
                    print(f"[교체] 버튼 상태 변경 오류: {e}")
            
            # 버튼 상태 변경을 즉시 실행 (다음 이벤트 루프에서)
            QTimer.singleShot(0, update_button_async)
            
            # 시그널 발생도 비동기로 처리하여 UI 블로킹 방지
            QTimer.singleShot(0, lambda: self.replace_thumbnail_requested.emit(torrent_id))
        except Exception as e:
            print(f"[교체] 버튼 클릭 처리 오류: {e}")
    
    def enable_replace_button(self, torrent_id: int):
        """교체 완료/실패 후 버튼 재활성화"""
        try:
            # 해당 torrent_id의 행 찾기
            for row, torrent in enumerate(self.torrents):
                if torrent.id == torrent_id:
                    # 해당 행의 교체 버튼 가져오기
                    btn = self.table.cellWidget(row, 7)
                    if btn and isinstance(btn, QPushButton):
                        btn.setEnabled(True)
                        btn.setText("썸네일 교체")
                    break
        except Exception:
            pass
    
    def _create_action_widget(self, torrent: Torrent) -> QWidget:
        """액션 버튼 위젯 생성
        
        Args:
            torrent: Torrent 객체
            
        Returns:
            버튼이 있는 QWidget
        """
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)
        
        # Magnet 버튼
        magnet_btn = QPushButton("🧲")
        magnet_btn.setToolTip("Magnet 링크 열기")
        magnet_btn.setMaximumWidth(40)
        magnet_btn.clicked.connect(lambda: self.open_magnet(torrent.magnet_link))
        layout.addWidget(magnet_btn)
        
        return widget
    
    def open_magnet(self, magnet_link: str):
        """Magnet 링크 열기
        
        Args:
            magnet_link: Magnet URI
        """
        if magnet_link:
            QDesktopServices.openUrl(QUrl(magnet_link))
        else:
            QMessageBox.warning(self, "오류", "Magnet 링크가 없습니다.")
    
    def on_row_double_clicked(self, index):
        """행 더블 클릭 이벤트
        
        Args:
            index: QModelIndex
        """
        row = index.row()
        if 0 <= row < len(self.torrents):
            torrent = self.torrents[row]
            self.open_magnet(torrent.magnet_link)
    
    def _load_thumbnail(self, row: int, url: str):
        """썸네일 이미지 로딩
        
        Args:
            row: 행 번호
            url: 이미지 URL
        """
        if not url:
            return
        
        # URL -> 행 번호 매핑 추가
        if url not in self.url_to_rows:
            self.url_to_rows[url] = []
        if row not in self.url_to_rows[url]:
            self.url_to_rows[url].append(row)
        
        # 캐시 확인
        cached = self.image_cache.get(url)
        if cached:
            self._set_thumbnail(row, cached)
            # 타임아웃 타이머 정리
            self._clear_row_timeout(row)
            return
        
        # 로딩 시작 시간 기록
        self.row_loading_start_time[row] = time.time()
        
        # 기존 타임아웃 타이머가 있으면 제거
        self._clear_row_timeout(row)
        
        # 5초 타임아웃 타이머 설정
        timeout_timer = QTimer()
        timeout_timer.setSingleShot(True)
        timeout_timer.timeout.connect(lambda: self._on_loading_timeout(row, url))
        timeout_timer.start(5000)  # 5초
        self.row_timeout_timers[row] = timeout_timer
        
        # 다운로드 시작 (비동기)
        self.image_downloader.download(url)
    
    def _set_thumbnail(self, row: int, pixmap: QPixmap):
        """썸네일 이미지 설정
        
        Args:
            row: 행 번호
            pixmap: QPixmap 객체
        """
        if row < 0 or row >= self.table.rowCount():
            return
        
        if pixmap.isNull():
            print(f"[TorrentList] 썸네일이 null입니다 (row: {row})")
            return
        
        # 현재 셀 크기에 맞춰 이미지 크기 조정
        cell_width = self.table.columnWidth(0) - 16
        cell_height = self.table.rowHeight(row) - 16
        target_w = max(32, cell_width)
        target_h = max(32, cell_height)
        scaled_pixmap = pixmap.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if scaled_pixmap.isNull():
            print(f"[TorrentList] 스케일된 썸네일이 null입니다 (row: {row})")
            return
        
        icon = QIcon(scaled_pixmap)
        
        thumbnail_item = self.table.item(row, 0)
        if thumbnail_item:
            # 텍스트 제거하고 아이콘만 표시
            thumbnail_item.setText("")
            thumbnail_item.setIcon(icon)
            # 아이콘 모드로 설정
            thumbnail_item.setFlags(thumbnail_item.flags() & ~Qt.ItemIsEditable)
            # 셀 크기 힌트 설정
            thumbnail_item.setSizeHint(scaled_pixmap.size())
            # 행 높이도 설정값으로 유지
            self.table.setRowHeight(row, self.row_height)
    
    def _on_image_loaded(self, url: str, pixmap: QPixmap):
        """이미지 로딩 완료 시그널 처리
        
        Args:
            url: 이미지 URL
            pixmap: 로딩된 QPixmap
        """
        # pixmap이 유효한 경우에만 처리
        if pixmap.isNull():
            return
        
        if url in self.url_to_rows:
            for row in self.url_to_rows[url]:
                # 타임아웃 타이머 정리
                self._clear_row_timeout(row)
                # 로딩 시작 시간 제거
                if row in self.row_loading_start_time:
                    del self.row_loading_start_time[row]
                
                # 원본 pixmap 저장 (호버 미리보기용) - 유효한 이미지만
                self.row_to_pixmap[row] = pixmap
                self._set_thumbnail(row, pixmap)
                
                # 현재 호버 중인 행의 이미지가 로딩 완료되면 자동으로 미리보기 표시
                if self.enable_hover_preview and row == self.current_hover_row:
                    self._show_preview(pixmap)
        # else: URL이 url_to_rows에 없는 경우는 페이지 변경이나 썸네일 업데이트로 인한 정상적인 상황일 수 있으므로 조용히 무시
    
    def _on_image_failed(self, url: str):
        """이미지 다운로드 실패 시그널 처리
        
        Args:
            url: 실패한 이미지 URL
        """
        if url in self.url_to_rows:
            for row in self.url_to_rows[url]:
                # 타임아웃 타이머 정리
                self._clear_row_timeout(row)
                # 로딩 시작 시간 제거
                if row in self.row_loading_start_time:
                    del self.row_loading_start_time[row]
                
                # 다른 서버에서 썸네일 검색 요청 (비동기로 처리하여 UI 블로킹 방지)
                QTimer.singleShot(0, lambda r=row: self._request_thumbnail_search(r))
    
    def _on_loading_timeout(self, row: int, url: str):
        """이미지 로딩 타임아웃 처리 (5초)
        
        Args:
            row: 행 번호
            url: 타임아웃된 이미지 URL
        """
        # 타임아웃 타이머 정리
        self._clear_row_timeout(row)
        # 로딩 시작 시간 제거
        if row in self.row_loading_start_time:
            del self.row_loading_start_time[row]
        
        # 로딩 중 텍스트를 "타임아웃"으로 변경
        thumbnail_item = self.table.item(row, 0)
        if thumbnail_item:
            thumbnail_item.setText("타임아웃")
        
        # 다른 서버에서 썸네일 검색 요청 (비동기로 처리하여 UI 블로킹 방지)
        QTimer.singleShot(0, lambda: self._request_thumbnail_search(row))
    
    def _request_thumbnail_search(self, row: int):
        """다른 서버에서 썸네일 검색 요청
        
        Args:
            row: 행 번호
        """
        if 0 <= row < len(self.torrents):
            torrent = self.torrents[row]
            # replace_thumbnail_requested 시그널 발생 (비동기로 처리)
            QTimer.singleShot(0, lambda: self.replace_thumbnail_requested.emit(torrent.id))
    
    def _clear_row_timeout(self, row: int):
        """행의 타임아웃 타이머 정리
        
        Args:
            row: 행 번호
        """
        if row in self.row_timeout_timers:
            timer = self.row_timeout_timers[row]
            if timer:
                timer.stop()
                timer.deleteLater()
            del self.row_timeout_timers[row]
    
    def _load_visible_images(self):
        """보이는 행의 이미지만 로딩 (lazy loading)"""
        if not self.torrents:
            return
        
        # 현재 보이는 행 범위 계산
        viewport = self.table.viewport()
        start_row = self.table.rowAt(viewport.y())
        if start_row < 0:
            start_row = 0
        
        end_row = self.table.rowAt(viewport.y() + viewport.height())
        if end_row < 0:
            end_row = len(self.torrents) - 1
        
        # 여유분을 추가하여 스크롤 시 미리 로딩 (앞뒤 5개 행)
        start_row = max(0, start_row - 5)
        end_row = min(len(self.torrents), end_row + 5)
        
        for row in range(start_row, end_row):
            if row < len(self.torrents):
                torrent = self.torrents[row]
                if torrent.thumbnail_url:
                    # 캐시 확인
                    cached = self.image_cache.get(torrent.thumbnail_url)
                    if not cached:
                        # 로딩 시작
                        self._load_thumbnail(row, torrent.thumbnail_url)
                    else:
                        # 캐시에 있으면 바로 표시
                        self._set_thumbnail(row, cached)
                # 스냅샷 로딩
                self._load_snapshots_for_row(row, torrent)
                # 스냅샷 비활성화
    
    def _on_scroll(self):
        """스크롤 이벤트 처리"""
        self._load_visible_images()

    def _load_all_images(self):
        """현재 페이지의 모든 행 이미지를 메모리에 선로딩 (비동기 처리)"""
        if not self.torrents:
            return
        
        # 배치 처리: 한 번에 처리할 행 수 제한하여 UI 반응성 유지
        from PySide6.QtCore import QTimer
        
        def process_batch(start_idx: int, batch_size: int = 5):
            """배치 단위로 이미지 로드 (UI 블로킹 방지)"""
            end_idx = min(start_idx + batch_size, len(self.torrents))
            
            for row in range(start_idx, end_idx):
                if row < len(self.torrents):
                    torrent = self.torrents[row]
                    if torrent.thumbnail_url:
                        cached = self.image_cache.get(torrent.thumbnail_url)
                        if not cached:
                            self._load_thumbnail(row, torrent.thumbnail_url)
                        else:
                            self._set_thumbnail(row, cached)
                    self._load_snapshots_for_row(row, torrent)
            
            # 다음 배치 처리 (클로저 문제 방지를 위해 end_idx를 명시적으로 전달)
            if end_idx < len(self.torrents):
                def next_batch():
                    process_batch(end_idx, batch_size)
                QTimer.singleShot(10, next_batch)  # 10ms 후 다음 배치
        
        # 첫 배치 시작
        process_batch(0, batch_size=5)

    def _load_snapshots_for_row(self, row: int, torrent: Torrent):
        """주어진 행의 스냅샷 이미지를 선로딩"""
        urls = []
        try:
            if torrent.snapshot_urls:
                data = json.loads(torrent.snapshot_urls)
                if isinstance(data, list):
                    urls = data[:3]
        except Exception:
            urls = []
        if not urls:
            return
        for u in urls:
            if not u:
                continue
            cached_snap = self.image_cache.get(u)
            if cached_snap:
                if u in self.snapshot_url_to_rows:
                    for r in self.snapshot_url_to_rows[u]:
                        labels = self.row_to_snapshot_labels.get(r) or []
                        row_urls = self.row_to_snapshot_urls.get(r) or []
                        if r == row and u in row_urls:
                            try:
                                idx = row_urls.index(u)
                            except ValueError:
                                idx = -1
                            if 0 <= idx < len(labels):
                                lbl = labels[idx]
                                target = cached_snap.scaled(lbl.width(), lbl.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                                lbl.setPixmap(target)
                                lbl.setText("")
            else:
                self.image_downloader.download(u)

    def _on_header_clicked(self, logical_index: int):
        """테이블 헤더 클릭 시 전체 DB에서 정렬"""
        # 컬럼 인덱스 -> DB 필드명 매핑
        column_to_field = {
            0: None,  # 썸네일 (정렬 불가)
            1: 'title',  # 제목
            2: 'size',  # 크기
            3: 'seeders',  # 시더
            4: 'leechers',  # 리처
            5: 'downloads',  # 다운로드수
            6: 'upload_date',  # 날짜
            7: None,  # 교체 버튼 (정렬 불가)
        }
        
        field = column_to_field.get(logical_index)
        if field is None:
            return  # 정렬 불가능한 컬럼
        
        # 정렬 순서 토글
        if self.current_sort_column == field:
            # 같은 컬럼 클릭 시 오름차순 <-> 내림차순 토글
            self.current_sort_order = 'asc' if self.current_sort_order == 'desc' else 'desc'
        else:
            # 새 컬럼 클릭 시 내림차순으로 시작
            self.current_sort_column = field
            self.current_sort_order = 'desc'
        
        # 헤더 정렬 표시기 업데이트 (크기 변경 방지를 위해 블록)
        header = self.table.horizontalHeader()
        header.blockSignals(True)  # 시그널 일시 차단
        
        if self.current_sort_order == 'asc':
            header.setSortIndicator(logical_index, Qt.AscendingOrder)
        else:
            header.setSortIndicator(logical_index, Qt.DescendingOrder)
        
        header.blockSignals(False)  # 시그널 재개
        
        # MainWindow에 정렬 요청
        self.sort_requested.emit(field, self.current_sort_order)

    def _on_item_entered(self, item: QTableWidgetItem):
        """아이템에 마우스가 진입했을 때 (호버)"""
        if not self.enable_hover_preview:
            return
        if item and item.column() == 0:
            row = item.row()
            self.current_hover_row = row  # 현재 호버 중인 행 저장
            
            # 1. 먼저 row_to_pixmap에서 확인 (이미 로딩된 경우)
            pix = self.row_to_pixmap.get(row)
            if pix and not pix.isNull():
                self._show_preview(pix)
                return
            
            # 2. row_to_pixmap에 없으면 캐시에서 직접 확인
            if row < len(self.torrents):
                torrent = self.torrents[row]
                if torrent.thumbnail_url:
                    cached_pix = self.image_cache.get(torrent.thumbnail_url)
                    if cached_pix and not cached_pix.isNull():
                        # 캐시에서 찾았으면 row_to_pixmap에 저장하고 표시
                        self.row_to_pixmap[row] = cached_pix
                        self._show_preview(cached_pix)
                        return
                    else:
                        # 캐시에도 없으면 즉시 다운로드 요청
                        self.image_downloader.download(torrent.thumbnail_url)
            
            self._hide_preview()
        else:
            self.current_hover_row = None  # 호버 해제
            self._hide_preview()

    def eventFilter(self, obj, event):
        """뷰포트 마우스 이동/이탈 처리"""
        if obj is self.table.viewport():
            if event.type() == QEvent.Leave:
                self.current_hover_row = None
                self._hide_preview()
            elif event.type() == QEvent.MouseMove:
                # 미리보기 위치를 커서에 맞춰 이동
                if self.preview_label and self.preview_label.isVisible():
                    pos = QCursor.pos()
                    self.preview_label.move(pos.x() + 16, pos.y() + 16)
        # 스냅샷 라벨 호버 처리
        if isinstance(obj, QLabel) and obj.property('snapshot_url'):
            url = obj.property('snapshot_url')
            if event.type() in (QEvent.Enter, QEvent.MouseMove):
                if self.enable_hover_preview:
                    pix = self.image_cache.get(url)
                    if pix and not pix.isNull():
                        self._show_preview(pix)
            elif event.type() == QEvent.Leave:
                self._hide_preview()
        return super().eventFilter(obj, event)

    def _ensure_preview_label(self):
        if self.preview_label is None:
            self.preview_label = QLabel()
            self.preview_label.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.preview_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.preview_label.setStyleSheet("background: rgba(0,0,0,0.6); border: 1px solid #444;")
            self.preview_label.hide()

    def _show_preview(self, pixmap: QPixmap):
        self._ensure_preview_label()
        
        # 면적 기준으로 리사이즈 (목표 면적: 약 480,000 픽셀 = 800x600)
        import math
        target_area = 800 * 600  # 480,000 픽셀
        current_area = pixmap.width() * pixmap.height()
        
        # 큰 이미지만 축소, 작은 이미지는 원본 그대로
        display_pixmap = pixmap
        if current_area > target_area:
            scale_factor = math.sqrt(target_area / current_area)
            new_width = int(pixmap.width() * scale_factor)
            new_height = int(pixmap.height() * scale_factor)
            
            display_pixmap = pixmap.scaled(
                new_width, new_height,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
        
        # 앱 윈도우의 실제 클라이언트 영역 가져오기
        from PySide6.QtGui import QGuiApplication
        cursor_pos = QCursor.pos()
        
        win = self.window()
        if win:
            # 윈도우 프레임을 제외한 실제 클라이언트 영역을 전역 좌표로 변환
            win_top_left = win.mapToGlobal(win.rect().topLeft())
            win_bottom_right = win.mapToGlobal(win.rect().bottomRight())
            
            app_left = win_top_left.x()
            app_top = win_top_left.y()
            app_right = win_bottom_right.x()
            app_bottom = win_bottom_right.y()
            app_width = app_right - app_left
            app_height = app_bottom - app_top
            
            # 안전 여백 (픽셀)
            margin = 10
            
            # 미리보기가 앱보다 크면 앱 크기에 맞게 축소
            max_width = app_width - (margin * 2)
            max_height = app_height - (margin * 2)
            
            preview_width = display_pixmap.width()
            preview_height = display_pixmap.height()
            
            if preview_width > max_width or preview_height > max_height:
                # 앱 크기에 맞게 축소
                scale_w = max_width / preview_width if preview_width > max_width else 1.0
                scale_h = max_height / preview_height if preview_height > max_height else 1.0
                scale = min(scale_w, scale_h)
                
                new_w = int(preview_width * scale)
                new_h = int(preview_height * scale)
                
                display_pixmap = display_pixmap.scaled(
                    new_w, new_h,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                preview_width = display_pixmap.width()
                preview_height = display_pixmap.height()
            
            self.preview_label.setPixmap(display_pixmap)
            self.preview_label.resize(preview_width, preview_height)
            
            # 기본 위치: 커서 오른쪽 아래 (16px 오프셋)
            offset = 16
            x = cursor_pos.x() + offset
            y = cursor_pos.y() + offset

            # 오른쪽으로 벗어나면 왼쪽에 표시
            if x + preview_width + margin > app_right:
                x = cursor_pos.x() - preview_width - offset

            # 아래로 벗어나면 위쪽에 표시
            if y + preview_height + margin > app_bottom:
                y = cursor_pos.y() - preview_height - offset

            # 여전히 벗어나면 강제로 앱 내부로 이동 (최종 안전장치)
            x = max(app_left + margin, min(x, app_right - preview_width - margin))
            y = max(app_top + margin, min(y, app_bottom - preview_height - margin))
            
            self.preview_label.move(x, y)
            self.preview_label.show()
        else:
            # 윈도우를 찾을 수 없으면 화면 경계로 제한
            screen = QGuiApplication.screenAt(cursor_pos) or QGuiApplication.primaryScreen()
            screen_geo = screen.availableGeometry()
            
            preview_width = display_pixmap.width()
            preview_height = display_pixmap.height()
            
            self.preview_label.setPixmap(display_pixmap)
            self.preview_label.resize(preview_width, preview_height)
            
            x = cursor_pos.x() + 16
            y = cursor_pos.y() + 16
            
            if x + preview_width > screen_geo.right():
                x = cursor_pos.x() - preview_width - 16
            if y + preview_height > screen_geo.bottom():
                y = cursor_pos.y() - preview_height - 16
            
            x = max(screen_geo.left(), min(x, screen_geo.right() - preview_width))
            y = max(screen_geo.top(), min(y, screen_geo.bottom() - preview_height))
            
            self.preview_label.move(x, y)
            self.preview_label.show()

    def _hide_preview(self):
        if self.preview_label:
            self.preview_label.hide()

    def apply_settings(self, thumbnail_width: int, row_height: int, hover_preview: bool):
        """외부에서 설정 변경 시 적용"""
        self.thumbnail_col_width = int(thumbnail_width)
        self.row_height = int(row_height)
        self.enable_hover_preview = bool(hover_preview)
        # 저장
        self.settings.setValue('ui/thumbnail_width', self.thumbnail_col_width)
        self.settings.setValue('ui/row_height', self.row_height)
        self.settings.setValue('ui/hover_preview', self.enable_hover_preview)
        # 적용
        self.table.setColumnWidth(0, self.thumbnail_col_width)
        icon_size = min(self.thumbnail_col_width - 10, self.row_height - 10)
        self.table.setIconSize(QSize(icon_size, icon_size))
        for row in range(self.table.rowCount()):
            self.table.setRowHeight(row, self.row_height)
            # 기존 썸네일 재적용 (크기 재계산)
            if row in self.row_to_pixmap:
                self._set_thumbnail(row, self.row_to_pixmap[row])

