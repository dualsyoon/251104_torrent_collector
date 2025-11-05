"""GUI 빠른 테스트 (샘플 데이터)"""
import sys
from database.database import Database

def test_gui():
    """GUI 시작 전 테스트"""
    print("="*70)
    print("GUI 시작 전 체크")
    print("="*70)
    
    # 1. 데이터베이스 초기화
    print("\n1. 데이터베이스 체크...")
    try:
        db = Database()
        print(f"   [OK] DB 경로: {db.db_path}")
        
        # 데이터 개수 확인
        session = db.get_session()
        count = db.get_total_count(session)
        session.close()
        
        print(f"   [OK] 현재 토렌트 개수: {count}개")
        
        if count == 0:
            print("\n   [!] 데이터가 없습니다!")
            print("   샘플 데이터를 추가하시겠습니까? (y/n): ", end='')
            answer = input().strip().lower()
            if answer == 'y':
                print("\n   샘플 데이터 추가 중...")
                import subprocess
                subprocess.run([sys.executable, "add_sample_data.py"])
                
                # 다시 확인
                session = db.get_session()
                count = db.get_total_count(session)
                session.close()
                print(f"   [OK] 샘플 데이터 추가 완료: {count}개")
        
    except Exception as e:
        print(f"   [X] 오류: {e}")
        return False
    
    # 2. 스크래퍼 체크
    print("\n2. 스크래퍼 체크...")
    try:
        from scrapers.scraper_manager import ScraperManager
        sm = ScraperManager()
        sources = list(sm.scrapers.keys())
        print(f"   [OK] 사용 가능한 소스: {sources}")
    except Exception as e:
        print(f"   [X] 오류: {e}")
        return False
    
    # 3. GUI 시작
    print("\n3. GUI 시작...")
    print("="*70)
    
    try:
        from PySide6.QtWidgets import QApplication
        from gui.main_window import MainWindow
        from scrapers.scraper_manager import ScraperManager
        
        app = QApplication(sys.argv)
        
        db = Database()
        scraper_manager = ScraperManager()
        
        window = MainWindow(db, scraper_manager)
        window.show()
        
        print("[OK] GUI 실행 중...")
        sys.exit(app.exec())
        
    except Exception as e:
        print(f"[X] GUI 시작 실패: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_gui()

