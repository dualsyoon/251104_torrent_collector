"""빠른 연결 테스트"""
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url = "https://sukebei.nyaa.si"

print("=" * 60)
print("빠른 연결 테스트")
print("=" * 60)
print(f"\nURL: {url}\n")

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Connection': 'close',
}

try:
    print("연결 시도 중...")
    response = requests.get(
        url, 
        headers=headers,
        timeout=15,
        verify=False,
        allow_redirects=True
    )
    
    print(f"\n✓ 성공!")
    print(f"  상태 코드: {response.status_code}")
    print(f"  응답 크기: {len(response.content)} bytes")
    print(f"  Content-Type: {response.headers.get('Content-Type')}")
    
    # 간단한 파싱 테스트
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(response.content, 'lxml')
    table = soup.find('table', class_='torrent-list')
    
    if table:
        rows = table.find('tbody').find_all('tr')
        print(f"  토렌트 개수: {len(rows)}개")
        print("\n✓✓ 연결과 파싱 모두 정상 작동!")
        print("\n스크래퍼 코드에 문제가 있을 수 있습니다.")
        print("수정된 코드로 다시 시도해주세요.")
    else:
        print("\n✗ 토렌트 테이블을 찾을 수 없습니다")
        print("사이트 구조가 변경되었을 수 있습니다")
        
except Exception as e:
    print(f"\n✗ 실패!")
    print(f"  오류: {type(e).__name__}")
    print(f"  상세: {e}")
    print("\n이 경우 VPN이 필요할 수 있습니다.")

print("\n" + "=" * 60)

