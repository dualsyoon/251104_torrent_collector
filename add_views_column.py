"""views 컬럼 추가 스크립트"""
from database.database import Database
from sqlalchemy import text
import sqlite3

def add_views_column():
    """torrents 테이블에 views 컬럼 추가"""
    db = Database()
    
    try:
        with db.engine.connect() as conn:
            # SQLite는 IF NOT EXISTS 미지원, 직접 확인
            result = conn.execute(text("PRAGMA table_info(torrents)"))
            columns = [row[1] for row in result]
            
            if 'views' in columns:
                print("[OK] views 컬럼이 이미 존재합니다.")
            else:
                # views 컬럼 추가
                conn.execute(text("ALTER TABLE torrents ADD COLUMN views INTEGER DEFAULT 0"))
                conn.commit()
                print("[OK] views 컬럼이 성공적으로 추가되었습니다!")
            
            # 기존 데이터의 views 값 추정 (downloads * 5)
            conn.execute(text("""
                UPDATE torrents 
                SET views = downloads * 5 
                WHERE views = 0 OR views IS NULL
            """))
            conn.commit()
            
            print("[OK] 기존 데이터의 조회수가 추정되었습니다 (완료수 x 5)")
            
    except Exception as e:
        print(f"[X] 오류 발생: {e}")
        raise

if __name__ == "__main__":
    add_views_column()

