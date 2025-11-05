"""썸네일 검색 테스트 스크립트"""
from scrapers.image_finder import ImageFinder, ThumbnailEnhancer

def test_image_search():
    """이미지 검색 테스트"""
    print("=" * 80)
    print("썸네일 검색 테스트")
    print("=" * 80)
    
    # 테스트 케이스들 (JAVDB 우선)
    test_titles = [
        "SSIS-123",
        "IPX-456",
        "FC2-1234567",
    ]
    
    finder = ImageFinder()
    
    for title in test_titles:
        print(f"\n{'='*80}")
        print(f"테스트 제목: {title}")
        print(f"{'='*80}")
        
        # 코드 추출 테스트
        codes = finder._extract_codes(title)
        print(f"추출된 코드: {codes}")
        
        # JAVDB 우선 검색 테스트
        print(f"\n[JAVDB 우선 검색]")
        result = finder.search_images(title, max_images=3)
        
        print(f"\n[결과]")
        print(f"썸네일: {result['thumbnail'][:100] if result['thumbnail'] else '없음'}...")
        assert result['thumbnail'].startswith('http'), "JAVDB 썸네일을 찾지 못했습니다"
        
        # 각 소스별 테스트
        print(f"\n[소스별 테스트]")
        if codes:
            print(f"\n[JAVDB]")
            for code in codes[:1]:
                javdb_urls = finder._search_javdb(code)
                print(f"  {code}: {len(javdb_urls)}개 발견")
                if javdb_urls:
                    print(f"    첫 번째: {javdb_urls[0][:80]}...")
        
        # Google/DuckDuckGo는 성인 컨텐츠 필터링으로 인해 비활성화됨
        print(f"\n[참고] Google/DuckDuckGo는 성인 컨텐츠 필터링으로 사용 불가")
        
        print("\n" + "-" * 80)

def test_thumbnail_enhancer():
    """ThumbnailEnhancer 테스트"""
    print("\n" + "=" * 80)
    print("ThumbnailEnhancer 테스트")
    print("=" * 80)
    
    enhancer = ThumbnailEnhancer()
    
    test_data = {
        'title': 'SSIS-123 [1080p] Uncensored',
        'magnet_link': 'magnet:?xt=urn:btih:test',
        'thumbnail_url': '',
        'snapshot_urls': ''
    }
    
    print(f"\n원본 데이터:")
    print(f"  제목: {test_data['title']}")
    print(f"  썸네일: {test_data['thumbnail_url'] or '없음'}")
    
    print(f"\n[검색 중...]")
    result = enhancer.enhance_torrent(test_data)
    
    print(f"\n결과:")
    print(f"  썸네일: {result['thumbnail_url'][:100] if result['thumbnail_url'] else '없음'}...")
    print(f"  스냅샷: 비활성화")

if __name__ == "__main__":
    test_image_search()
    test_thumbnail_enhancer()

