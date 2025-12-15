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

    # Playwright 서버는 처음에만 초기화(워밍업)하고 재사용.
    # 종료 시에는 전체 정리(브라우저/컨텍스트/Playwright stop) 수행.
    try:
        from scrapers.playwright_server import get_playwright_server

        try:
            get_playwright_server().warmup()
        except Exception:
            # 워밍업 실패는 치명적이지 않음(실제 검색 시 재초기화 가능)
            pass

        def _pw_cleanup():
            try:
                get_playwright_server().close_all()
            except Exception:
                pass

        app.aboutToQuit.connect(_pw_cleanup)
    except Exception:
        # Playwright 미설치/환경문제 등은 무시 (기존 경로 폴백 가능)
        pass
    
    # 메인 윈도우 생성 및 표시
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
