"""연결 테스트 스크립트"""
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def test_site(url, name):
    """사이트 연결 테스트"""
    print(f"\n{'='*60}")
    print(f"테스트: {name}")
    print(f"URL: {url}")
    print(f"{'='*60}")
    
    try:
        # 기본 연결 시도
        response = requests.get(url, timeout=10, verify=False)
        print(f"✓ 연결 성공!")
        print(f"  상태 코드: {response.status_code}")
        print(f"  응답 크기: {len(response.content)} bytes")
        return True
    except requests.exceptions.ConnectionError as e:
        print(f"✗ 연결 실패: ConnectionError")
        print(f"  원인: ISP나 방화벽에서 차단한 것으로 보입니다")
        return False
    except requests.exceptions.Timeout:
        print(f"✗ 연결 실패: Timeout")
        return False
    except Exception as e:
        print(f"✗ 연결 실패: {type(e).__name__}")
        print(f"  세부 정보: {e}")
        return False

def main():
    """메인 테스트"""
    print("=" * 60)
    print("토렌트 사이트 연결 테스트")
    print("=" * 60)
    
    sites = [
        ("https://sukebei.nyaa.si", "Sukebei (Nyaa)"),
        ("https://www.javtorrent.re", "JAVTorrent"),
        ("https://www.torrentkitty.tv", "TorrentKitty"),
        ("https://www.google.com", "Google (기본 연결 테스트)"),
    ]
    
    results = []
    for url, name in sites:
        success = test_site(url, name)
        results.append((name, success))
    
    print("\n" + "=" * 60)
    print("테스트 결과 요약")
    print("=" * 60)
    
    working = [name for name, success in results if success]
    blocked = [name for name, success in results if not success]
    
    if working:
        print(f"\n✓ 접속 가능한 사이트 ({len(working)}개):")
        for name in working:
            print(f"  - {name}")
    
    if blocked:
        print(f"\n✗ 접속 불가능한 사이트 ({len(blocked)}개):")
        for name in blocked:
            print(f"  - {name}")
    
    if not working:
        print("\n⚠️  모든 사이트 접속 실패!")
        print("\n해결 방법:")
        print("1. VPN 사용 (가장 효과적)")
        print("   - ProtonVPN, Windscribe 등 무료 VPN 사용")
        print("2. 프록시 서버 사용")
        print("3. 다른 네트워크 시도 (예: 모바일 핫스팟)")
        print("4. 방화벽 설정 확인")
    else:
        print(f"\n✓ {len(working)}개 사이트에서 데이터 수집 가능합니다!")

if __name__ == "__main__":
    main()

