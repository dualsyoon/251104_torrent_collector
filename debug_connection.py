"""상세 연결 디버깅 스크립트"""
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def test_with_different_methods(url):
    """다양한 방법으로 연결 테스트"""
    print(f"\n{'='*70}")
    print(f"테스트 URL: {url}")
    print(f"{'='*70}\n")
    
    # 방법 1: 기본 requests
    print("[1] 기본 requests.get()...")
    try:
        response = requests.get(url, timeout=10)
        print(f"   OK 성공! 상태: {response.status_code}, 크기: {len(response.content)} bytes")
    except Exception as e:
        print(f"   X 실패: {type(e).__name__}: {e}")
    
    # 방법 2: SSL 검증 비활성화
    print("\n[2] SSL 검증 비활성화 (verify=False)...")
    try:
        response = requests.get(url, timeout=10, verify=False)
        print(f"   OK 성공! 상태: {response.status_code}, 크기: {len(response.content)} bytes")
    except Exception as e:
        print(f"   X 실패: {type(e).__name__}: {e}")
    
    # 방법 3: 브라우저 User-Agent
    print("\n[3] 브라우저 User-Agent 추가...")
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        response = requests.get(url, headers=headers, timeout=10, verify=False)
        print(f"   OK 성공! 상태: {response.status_code}, 크기: {len(response.content)} bytes")
        print(f"   Content-Type: {response.headers.get('Content-Type', 'N/A')}")
        return response  # 성공한 response 반환
    except Exception as e:
        print(f"   X 실패: {type(e).__name__}: {e}")
    
    # 방법 4: Session 사용
    print("\n[4] Session 사용...")
    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        })
        response = session.get(url, timeout=10, verify=False)
        print(f"   OK 성공! 상태: {response.status_code}, 크기: {len(response.content)} bytes")
        return response
    except Exception as e:
        print(f"   X 실패: {type(e).__name__}: {e}")
    
    # 방법 5: HTTP 어댑터 설정
    print("\n[5] HTTP 어댑터 재시도 설정...")
    try:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        session = requests.Session()
        retry = Retry(connect=3, backoff_factor=0.5)
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        })
        response = session.get(url, timeout=15, verify=False)
        print(f"   OK 성공! 상태: {response.status_code}, 크기: {len(response.content)} bytes")
        return response
    except Exception as e:
        print(f"   X 실패: {type(e).__name__}: {e}")
    
    return None

def test_parsing(response, site_name):
    """파싱 테스트"""
    if not response:
        return
    
    print(f"\n{'='*70}")
    print(f"파싱 테스트: {site_name}")
    print(f"{'='*70}\n")
    
    from bs4 import BeautifulSoup
    
    try:
        soup = BeautifulSoup(response.content, 'lxml')
        
        # Sukebei 테스트
        if 'sukebei' in site_name.lower():
            table = soup.find('table', class_='torrent-list')
            if table:
                rows = table.find('tbody').find_all('tr')
                print(f"OK 토렌트 테이블 발견: {len(rows)}개 행")
                if rows:
                    first_row = rows[0]
                    cols = first_row.find_all('td')
                    if len(cols) >= 2:
                        title_col = cols[1]
                        title_links = title_col.find_all('a')
                        if title_links:
                            print(f"OK 첫 번째 토렌트: {title_links[-1].get_text(strip=True)[:50]}...")
            else:
                print("X 토렌트 테이블을 찾을 수 없습니다")
                print(f"페이지 내용 미리보기: {soup.get_text()[:200]}...")
        
        # JAVTorrent 테스트
        elif 'javtorrent' in site_name.lower():
            items = soup.find_all('div', class_='post-item')
            print(f"OK 포스트 아이템 발견: {len(items)}개")
            if items:
                first_item = items[0]
                title = first_item.find('h2') or first_item.find('a')
                if title:
                    print(f"OK 첫 번째 토렌트: {title.get_text(strip=True)[:50]}...")
        
        # TorrentKitty 테스트
        elif 'torrentkitty' in site_name.lower():
            table = soup.find('table', id='archiveResult')
            if table:
                rows = table.find_all('tr')[1:]  # 헤더 제외
                print(f"OK 검색 결과 테이블 발견: {len(rows)}개 행")
            else:
                print("X 검색 결과 테이블을 찾을 수 없습니다")
    
    except Exception as e:
        print(f"X 파싱 오류: {e}")

def main():
    print("=" * 70)
    print("상세 연결 디버깅")
    print("=" * 70)
    
    sites = [
        ("https://sukebei.nyaa.si", "Sukebei"),
        ("https://www.javtorrent.re", "JAVTorrent"),
        ("https://www.torrentkitty.tv/search/uncensored/1", "TorrentKitty"),
    ]
    
    for url, name in sites:
        response = test_with_different_methods(url)
        if response:
            test_parsing(response, name)
            print(f"\n[OK] {name}: 연결 및 파싱 모두 성공!")
        else:
            print(f"\n[FAIL] {name}: 모든 방법 실패")
    
    print("\n" + "=" * 70)
    print("디버깅 완료")
    print("=" * 70)
    print("\n어떤 방법이 성공했는지 확인하고,")
    print("해당 방법을 스크래퍼 코드에 적용하겠습니다.")

if __name__ == "__main__":
    main()

