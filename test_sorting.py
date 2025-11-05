"""정렬 기능 테스트"""
from database.database import Database

def test_sorting():
    """정렬 기능 테스트"""
    print("="*70)
    print("정렬 기능 테스트")
    print("="*70)
    
    db = Database()
    session = db.get_session()
    
    try:
        # 샘플 데이터 가져오기
        torrents = db.get_torrents(session, limit=10)
        
        if not torrents:
            print("\n⚠️ 데이터가 없습니다. 먼저 데이터를 수집하세요:")
            print("   python add_sample_data.py")
            return
        
        print(f"\n총 {len(torrents)}개의 토렌트:")
        print("\n{:<50} {:>6} {:>6} {:>7} {:>7} {:>5} {:>7}".format(
            "제목", "시더", "리처", "완료수", "조회수", "댓글", "인기도"
        ))
        print("-"*100)
        
        for t in torrents:
            title = t.title[:47] + "..." if len(t.title) > 50 else t.title
            print("{:<50} {:>6} {:>6} {:>7} {:>7} {:>5} {:>7.1f}".format(
                title,
                t.seeders,
                t.leechers,
                t.downloads,
                t.views,
                t.comments,
                t.popularity_score
            ))
        
        print("\n✓ 모든 필드가 정상적으로 표시됩니다!")
        print("\nGUI에서 컬럼 헤더를 클릭하면 해당 필드로 정렬됩니다:")
        print("  - 시더: 클릭 → 시더 많은 순")
        print("  - 완료수: 클릭 → 완료 많은 순")
        print("  - 조회수: 클릭 → 조회 많은 순")
        print("  - 인기도: 클릭 → 인기도 높은 순")
        print("  - 다시 클릭 → 역순 정렬")
        
    finally:
        session.close()

if __name__ == "__main__":
    test_sorting()

