"""Selenium 스크래퍼 테스트"""
import sys

print("="*70)
print("Selenium 스크래퍼 테스트")
print("="*70)

try:
    from scrapers.selenium_scraper import SeleniumSukebeiScraper
    
    print("\n1. Selenium 스크래퍼 초기화...")
    scraper = SeleniumSukebeiScraper()
    
    print("\n2. 첫 페이지 수집 시도...")
    torrents = scraper.scrape_page(page=1, sort_by='seeders', order='desc')
    
    print(f"\n3. 결과:")
    print(f"   수집된 토렌트: {len(torrents)}개")
    
    if torrents:
        print(f"\n[OK OK] 성공! Selenium으로 ISP 차단 우회 완료!")
        print(f"\n첫 번째 토렌트:")
        first = torrents[0]
        print(f"  제목: {first['title'][:60]}...")
        print(f"  크기: {first['size']}")
        print(f"  시더: {first['seeders']}")
        print(f"  국가: {first['country']}")
        print(f"  검열: {'검열' if first['censored'] else '무검열'}")
        
        print(f"\n이제 메인 애플리케이션에서 'Sukebei (Selenium)' 소스를 선택하세요!")
    else:
        print(f"\n[X] 토렌트를 찾지 못했습니다")
    
    print("\n4. 브라우저 종료...")
    scraper.close()
    
    print(f"\n{'='*70}")
    print("테스트 완료!")
    print("="*70)
    
except ImportError as e:
    print(f"\n[X] Selenium이 설치되지 않았습니다")
    print(f"\n설치 방법:")
    print(f"  pip install selenium webdriver-manager")
    print(f"\n그 다음:")
    print(f"  python test_selenium.py")
    
except Exception as e:
    print(f"\n[X] 오류 발생: {e}")
    import traceback
    traceback.print_exc()

