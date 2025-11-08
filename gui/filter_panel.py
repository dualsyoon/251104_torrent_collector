"""필터 패널 위젯"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QCheckBox, QLineEdit, QPushButton, QGroupBox, QListWidget,
    QAbstractItemView
)
from PySide6.QtCore import Signal
from config import TIME_RANGES, TIME_RANGE_DAYS
from typing import List, Optional


class FilterPanel(QWidget):
    """필터링 옵션 패널"""
    
    # 필터 변경 시그널
    filter_changed = Signal(dict)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()
    
    def init_ui(self):
        """UI 초기화"""
        layout = QVBoxLayout(self)
        
        # 제목
        title_label = QLabel("<h3>필터 옵션</h3>")
        layout.addWidget(title_label)
        
        # 기간 필터
        period_group = QGroupBox("기간")
        period_layout = QVBoxLayout()
        
        self.period_combo = QComboBox()
        # config.py의 TIME_RANGES 사용
        period_items = list(TIME_RANGES.values())
        self.period_combo.addItems(period_items)
        self.period_combo.currentTextChanged.connect(self.on_filter_changed)
        period_layout.addWidget(self.period_combo)
        
        period_group.setLayout(period_layout)
        layout.addWidget(period_group)
        
        # 검색
        search_group = QGroupBox("검색")
        search_layout = QVBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("제목 검색...")
        self.search_input.returnPressed.connect(self.on_filter_changed)
        search_layout.addWidget(self.search_input)
        
        # 추천 검색어 버튼들
        recommended_layout = QHBoxLayout()
        recommended_keywords = ["uncen", "漏れ", "無修正"]
        for keyword in recommended_keywords:
            btn = QPushButton(keyword)
            btn.setMaximumWidth(60)
            btn.clicked.connect(lambda checked, kw=keyword: self._set_search_keyword(kw))
            recommended_layout.addWidget(btn)
        recommended_layout.addStretch()
        search_layout.addLayout(recommended_layout)
        
        search_btn = QPushButton("🔍 검색")
        search_btn.clicked.connect(self.on_filter_changed)
        search_layout.addWidget(search_btn)
        
        search_group.setLayout(search_layout)
        layout.addWidget(search_group)
        
        # 필터 초기화 버튼
        reset_btn = QPushButton("🔄 필터 초기화")
        reset_btn.clicked.connect(self.reset_filters)
        layout.addWidget(reset_btn)
        
        layout.addStretch()
    
    def _set_search_keyword(self, keyword: str):
        """추천 검색어 버튼 클릭 시 검색어 설정"""
        self.search_input.setText(keyword)
        self.on_filter_changed()
    
    def on_filter_changed(self):
        """필터 변경 이벤트"""
        filters = self.get_filters()
        self.filter_changed.emit(filters)
    
    def get_filters(self) -> dict:
        """현재 필터 설정 반환
        
        Returns:
            필터 딕셔너리
        """
        # 기간 (config.py의 TIME_RANGES 사용)
        period_text = self.period_combo.currentText()
        # TIME_RANGES의 값에서 키 찾기 (역매핑)
        period_key = next((key for key, value in TIME_RANGES.items() if value == period_text), None)
        period_days = TIME_RANGE_DAYS.get(period_key) if period_key else None
        
        # 검색어
        search_query = self.search_input.text().strip()
        search_query = search_query if search_query else None
        
        return {
            'period_days': period_days,
            'search_query': search_query
        }
    
    def reset_filters(self):
        """필터 초기화"""
        self.period_combo.setCurrentIndex(0)
        self.search_input.clear()
        self.on_filter_changed()

