"""TLS 버전별 연결 테스트"""
import subprocess
import sys

def run_openssl_test(url, host, tls_version):
    """OpenSSL로 TLS 연결 테스트"""
    print(f"\n{'='*70}")
    print(f"OpenSSL {tls_version} 테스트: {url}")
    print(f"{'='*70}")
    
    cmd = [
        "openssl", "s_client",
        "-connect", f"{host}:443",
        "-servername", host,
        f"-{tls_version}",
        "-brief"
    ]
    
    try:
        result = subprocess.run(
            cmd,
            input=b"GET / HTTP/1.1\r\nHost: " + host.encode() + b"\r\n\r\n",
            capture_output=True,
            timeout=10
        )
        
        output = result.stdout.decode('utf-8', errors='ignore')
        
        if "Cipher is" in output or "Protocol" in output:
            print("OK 연결 성공!")
            # 중요 정보 출력
            for line in output.split('\n'):
                if any(keyword in line for keyword in ['Protocol', 'Cipher', 'Verification']):
                    print(f"  {line.strip()}")
            return True
        else:
            print("X 연결 실패")
            if result.stderr:
                error = result.stderr.decode('utf-8', errors='ignore')
                print(f"  오류: {error[:200]}")
            return False
            
    except subprocess.TimeoutExpired:
        print("X 시간 초과")
        return False
    except FileNotFoundError:
        print("X OpenSSL이 설치되지 않았습니다")
        print("  설치: https://slproweb.com/products/Win32OpenSSL.html")
        return False
    except Exception as e:
        print(f"X 오류: {e}")
        return False

def run_curl_test(url):
    """cURL로 연결 테스트"""
    print(f"\n{'='*70}")
    print(f"cURL 테스트: {url}")
    print(f"{'='*70}")
    
    cmd = ["curl", "-v", "--insecure", url]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=15
        )
        
        output = result.stderr.decode('utf-8', errors='ignore')
        
        if "HTTP" in output or "SSL connection" in output:
            print("OK 연결 성공!")
            # TLS 정보 출력
            for line in output.split('\n'):
                if any(keyword in line for keyword in ['TLS', 'SSL', 'cipher', 'HTTP/']):
                    print(f"  {line.strip()}")
            return True
        else:
            print("X 연결 실패")
            return False
            
    except subprocess.TimeoutExpired:
        print("X 시간 초과")
        return False
    except FileNotFoundError:
        print("X cURL이 설치되지 않았습니다")
        print("  Windows: https://curl.se/windows/")
        return False
    except Exception as e:
        print(f"X 오류: {e}")
        return False

def test_python_tls(url):
    """Python으로 TLS 버전별 테스트"""
    print(f"\n{'='*70}")
    print(f"Python TLS 테스트: {url}")
    print(f"{'='*70}")
    
    import ssl
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    # TLS 버전 목록
    tls_versions = [
        ('TLS 1.3', ssl.PROTOCOL_TLS),  # 최신 프로토콜
        ('TLS 1.2', ssl.PROTOCOL_TLSv1_2),
    ]
    
    results = []
    
    for version_name, ssl_version in tls_versions:
        print(f"\n[{version_name}] 시도 중...")
        
        try:
            # SSL 컨텍스트 생성
            context = ssl.SSLContext(ssl_version)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            
            # requests 어댑터에 SSL 컨텍스트 적용
            from requests.adapters import HTTPAdapter
            from urllib3.util.ssl_ import create_urllib3_context
            
            class SSLAdapter(HTTPAdapter):
                def init_poolmanager(self, *args, **kwargs):
                    kwargs['ssl_context'] = context
                    return super().init_poolmanager(*args, **kwargs)
            
            session = requests.Session()
            session.mount('https://', SSLAdapter())
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
            
            response = session.get(url, headers=headers, timeout=10, verify=False)
            
            print(f"  OK 성공! (상태: {response.status_code}, 크기: {len(response.content)} bytes)")
            results.append((version_name, True))
            return True  # 첫 성공시 종료
            
        except Exception as e:
            print(f"  X 실패: {type(e).__name__}")
            results.append((version_name, False))
    
    return False

def main():
    print("="*70)
    print("TLS 버전별 연결 테스트")
    print("="*70)
    
    # 테스트할 사이트
    sites = [
        ("https://sukebei.nyaa.si", "sukebei.nyaa.si"),
        ("https://www.google.com", "www.google.com"),  # 비교용
    ]
    
    for url, host in sites:
        print(f"\n\n{'#'*70}")
        print(f"# 사이트: {url}")
        print(f"{'#'*70}")
        
        # 1. OpenSSL TLS 1.3
        openssl_tls13 = run_openssl_test(url, host, "tls1_3")
        
        # 2. OpenSSL TLS 1.2
        openssl_tls12 = run_openssl_test(url, host, "tls1_2")
        
        # 3. cURL
        curl_ok = run_curl_test(url)
        
        # 4. Python
        python_ok = test_python_tls(url)
        
        # 결과 요약
        print(f"\n{'='*70}")
        print(f"결과 요약: {host}")
        print(f"{'='*70}")
        print(f"  OpenSSL TLS 1.3: {'OK' if openssl_tls13 else 'FAIL'}")
        print(f"  OpenSSL TLS 1.2: {'OK' if openssl_tls12 else 'FAIL'}")
        print(f"  cURL:            {'OK' if curl_ok else 'FAIL'}")
        print(f"  Python:          {'OK' if python_ok else 'FAIL'}")
        
        if curl_ok and not python_ok:
            print("\n⚠️  cURL은 성공했지만 Python은 실패!")
            print("  → ISP가 Python 요청을 선별 차단하고 있습니다")
            print("  → VPN 사용이 필요합니다")
    
    print("\n" + "="*70)
    print("테스트 완료")
    print("="*70)

if __name__ == "__main__":
    main()

