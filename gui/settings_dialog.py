"""환경 설정 다이얼로그"""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QCheckBox, QPushButton, QFormLayout, QGroupBox


class SettingsDialog(QDialog):
    """UI/스크래핑/이미지 관련 환경 설정"""
    def __init__(self, parent=None, thumbnail_width: int = 260, row_height: int = 220, hover_preview: bool = True,
                 max_scrape_pages: int = 100, enable_thumbnail: bool = True,
                 enable_javdb_fallback: bool = False, enable_selenium_for_images: bool = True,
                 image_http_timeout: int = 10, image_http_retries: int = 2):
        super().__init__(parent)
        self.setWindowTitle("환경 설정")
        self.thumbnail_width = thumbnail_width
        self.row_height = row_height
        self.hover_preview = hover_preview
        self.max_scrape_pages = max_scrape_pages
        self.enable_thumbnail = enable_thumbnail
        self.enable_javdb_fallback = enable_javdb_fallback
        self.enable_selenium_for_images = enable_selenium_for_images
        self.image_http_timeout = image_http_timeout
        self.image_http_retries = image_http_retries
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # UI 그룹
        ui_group = QGroupBox("UI 설정")
        ui_form = QFormLayout(ui_group)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(120, 600)
        self.width_spin.setValue(self.thumbnail_width)
        ui_form.addRow("썸네일 칼럼 너비", self.width_spin)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(120, 600)
        self.height_spin.setValue(self.row_height)
        ui_form.addRow("행 높이", self.height_spin)
        self.hover_check = QCheckBox("이미지 호버 미리보기 활성화")
        self.hover_check.setChecked(self.hover_preview)
        ui_form.addRow("미리보기", self.hover_check)

        layout.addWidget(ui_group)

        # 스크래핑 그룹
        scrape_group = QGroupBox("스크래핑")
        scrape_form = QFormLayout(scrape_group)
        self.max_pages_spin = QSpinBox()
        self.max_pages_spin.setRange(1, 1000)
        self.max_pages_spin.setValue(self.max_scrape_pages)
        scrape_form.addRow("최대 스크래핑 페이지 수", self.max_pages_spin)
        self.enable_thumb_check = QCheckBox("썸네일 검색 활성화")
        self.enable_thumb_check.setChecked(self.enable_thumbnail)
        scrape_form.addRow("썸네일 검색", self.enable_thumb_check)
        layout.addWidget(scrape_group)

        # 이미지 검색/다운로드 그룹
        image_group = QGroupBox("이미지 검색/다운로드")
        image_form = QFormLayout(image_group)
        self.enable_javdb_check = QCheckBox("JAVDB 백업 검색 사용")
        self.enable_javdb_check.setChecked(self.enable_javdb_fallback)
        image_form.addRow("JAVDB", self.enable_javdb_check)
        self.enable_selenium_check = QCheckBox("이미지 검색에 Selenium 사용")
        self.enable_selenium_check.setChecked(self.enable_selenium_for_images)
        image_form.addRow("Selenium", self.enable_selenium_check)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(3, 60)
        self.timeout_spin.setValue(self.image_http_timeout)
        image_form.addRow("이미지 요청 타임아웃(초)", self.timeout_spin)
        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 10)
        self.retries_spin.setValue(self.image_http_retries)
        image_form.addRow("검색 요청 재시도", self.retries_spin)

        layout.addWidget(image_group)

        btns = QHBoxLayout()
        ok_btn = QPushButton("확인")
        cancel_btn = QPushButton("취소")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

    def get_values(self):
        return {
            'ui': {
                'thumbnail_width': self.width_spin.value(),
                'row_height': self.height_spin.value(),
                'hover_preview': self.hover_check.isChecked(),
            },
            'scrape': {
                'max_pages': self.max_pages_spin.value(),
                'enable_thumbnail': self.enable_thumb_check.isChecked(),
            },
            'images': {
                'enable_javdb_fallback': self.enable_javdb_check.isChecked(),
                'enable_selenium_for_images': self.enable_selenium_check.isChecked(),
                'image_http_timeout': self.timeout_spin.value(),
                'image_http_retries': self.retries_spin.value(),
        }
    }


