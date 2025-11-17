"""토렌트 수집기 메인 애플리케이션"""
import sys
from PySide6.QtWidgets import QApplication
from gui import MainWindow


def main():
    """애플리케이션 진입점"""
    app = QApplication(sys.argv)
    app.setApplicationName("토렌트 수집기")
    app.setOrganizationName("TorrentCollector")
    
    # 시스템 트레이 사용 시 마지막 창이 닫혀도 앱이 종료되지 않도록 설정
    app.setQuitOnLastWindowClosed(False)
    
    # 메인 윈도우 생성 및 표시
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
