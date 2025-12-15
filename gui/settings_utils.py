"""GUI 설정(QSettings) 관련 유틸

동작 보존을 위해, 기존 코드에 있던 기본값/클램프(과도한 값 방지) 로직을 그대로 캡슐화합니다.
"""

from __future__ import annotations

from typing import Tuple

from PySide6.QtCore import QSettings

DEFAULT_THUMBNAIL_WIDTH = 120
DEFAULT_ROW_HEIGHT = 80

# 기존 동작: 너무 큰 값이면 한 번만 기본값으로 강제 변경
MAX_THUMBNAIL_WIDTH = 150
MAX_ROW_HEIGHT = 100


def load_ui_settings(settings: QSettings) -> Tuple[int, int, bool]:
    """UI 설정을 로드하고(필요 시) 안전 범위로 클램프합니다.

    Returns:
        (thumbnail_width, row_height, hover_preview)
    """

    saved_width = int(settings.value("ui/thumbnail_width", DEFAULT_THUMBNAIL_WIDTH))
    saved_height = int(settings.value("ui/row_height", DEFAULT_ROW_HEIGHT))

    # 너무 큰 값이면 강제로 작은 값으로 변경 (한 번만)
    if saved_width > MAX_THUMBNAIL_WIDTH:
        saved_width = DEFAULT_THUMBNAIL_WIDTH
        settings.setValue("ui/thumbnail_width", DEFAULT_THUMBNAIL_WIDTH)
    if saved_height > MAX_ROW_HEIGHT:
        saved_height = DEFAULT_ROW_HEIGHT
        settings.setValue("ui/row_height", DEFAULT_ROW_HEIGHT)

    hover_preview = settings.value("ui/hover_preview", True, type=bool)
    return saved_width, saved_height, hover_preview


__all__ = [
    "DEFAULT_THUMBNAIL_WIDTH",
    "DEFAULT_ROW_HEIGHT",
    "MAX_THUMBNAIL_WIDTH",
    "MAX_ROW_HEIGHT",
    "load_ui_settings",
]


