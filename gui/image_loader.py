"""이미지 다운로드 및 캐싱 관리 (메모리 전용)"""
import requests
from typing import Optional
from PySide6.QtCore import QThread, Signal, QObject
from PySide6.QtGui import QPixmap, QImage
from urllib.parse import urlparse
import time

try:
    import httpx  # HTTP/2 지원
    HAS_HTTPX = True
except Exception:
    HAS_HTTPX = False


class ImageCache:
    """이미지 메모리 캐시 관리"""
    
    def __init__(self, max_cache: int = 200):
        """초기화
        
        Args:
            max_cache: 최대 메모리 캐시 개수 (기본값: 200)
        """
        # 메모리 캐시만 사용
        self.memory_cache = {}
        self.access_order = []  # LRU를 위한 접근 순서
        self.max_cache = max_cache
    
    def get(self, url: str) -> Optional[QPixmap]:
        """캐시에서 이미지 가져오기
        
        Args:
            url: 이미지 URL
            
        Returns:
            QPixmap 또는 None
        """
        if not url:
            return None
        
        # 메모리 캐시 확인
        if url in self.memory_cache:
            # LRU: 접근 순서 업데이트
            if url in self.access_order:
                self.access_order.remove(url)
            self.access_order.append(url)
            return self.memory_cache[url]
        
        return None
    
    def save(self, url: str, pixmap: QPixmap) -> bool:
        """이미지를 메모리 캐시에 저장
        
        Args:
            url: 이미지 URL
            pixmap: QPixmap 객체
            
        Returns:
            성공 여부
        """
        if not url or pixmap.isNull():
            return False
        
        # 캐시가 가득 찬 경우 LRU 방식으로 오래된 항목 제거
        if len(self.memory_cache) >= self.max_cache:
            if self.access_order:
                # 가장 오래된 항목 제거
                oldest_url = self.access_order.pop(0)
                if oldest_url in self.memory_cache:
                    del self.memory_cache[oldest_url]
        
        # 메모리 캐시에 추가
        self.memory_cache[url] = pixmap
        if url not in self.access_order:
            self.access_order.append(url)
        
        return True
    
    def remove(self, url: str) -> bool:
        """특정 URL의 캐시 제거
        
        Args:
            url: 제거할 이미지 URL
            
        Returns:
            성공 여부
        """
        if not url:
            return False
        
        # 메모리 캐시에서 제거
        if url in self.memory_cache:
            del self.memory_cache[url]
        
        # 접근 순서에서도 제거
        if url in self.access_order:
            self.access_order.remove(url)
        
        return True
    
    def clear(self):
        """메모리 캐시 지우기"""
        self.memory_cache.clear()
        self.access_order.clear()
    
    def get_cache_size(self) -> int:
        """현재 캐시된 이미지 개수 반환"""
        return len(self.memory_cache)


class ImageDownloadWorker(QThread):
    """단일 이미지 다운로드 워커 스레드"""
    
    image_loaded = Signal(str, QPixmap)  # (url, pixmap)
    download_failed = Signal(str)  # (url)
    
    def __init__(self, cache: ImageCache, url: str):
        """초기화
        
        Args:
            cache: ImageCache 인스턴스
            url: 다운로드할 이미지 URL
        """
        super().__init__()
        self.cache = cache
        self.url = url
        # requests/httpx 공통 클라이언트 준비
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,ja;q=0.6,ko;q=0.6',
            'Connection': 'keep-alive'
        })
        self.httpx_client = None
        if HAS_HTTPX:
            try:
                self.httpx_client = httpx.Client(http2=True, headers=dict(self.session.headers), timeout=10, follow_redirects=True)
            except Exception:
                self.httpx_client = None
    
    def run(self):
        """이미지 다운로드 실행"""
        if not self.url:
            self.download_failed.emit(self.url)
            return
        
        # 캐시 확인 (스레드 안전하게)
        cached = self.cache.get(self.url)
        if cached:
            self.image_loaded.emit(self.url, cached)
            return
        
        # 다운로드
        try:
            # 일부 서버는 Referer/HTTP2/쿠키 없으면 차단 → 방어적 헤더 + 재시도
            image_data = self._download_bytes(self.url)
            if image_data is None:
                self.download_failed.emit(self.url)
                return
            
            # 이미지 데이터를 QPixmap으로 변환
            image = QImage()
            if image.loadFromData(image_data):
                pixmap = QPixmap.fromImage(image)
                
                # 메모리 캐시에 저장
                self.cache.save(self.url, pixmap)
                
                self.image_loaded.emit(self.url, pixmap)
            else:
                self.download_failed.emit(self.url)
                
        except Exception as e:
            print(f"[ImageDownloadWorker] 다운로드 실패 ({self.url}): {e}")
            self.download_failed.emit(self.url)

    def _download_bytes(self, url: str) -> Optional[bytes]:
        """안정적으로 이미지 바이트 다운로드 (HTTP/2 + 헤더 + 재시도)"""
        parsed = urlparse(url)
        referer = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None
        headers = {
            'Referer': referer or '',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Dest': 'image',
            'Sec-Fetch-Site': 'same-origin'
        }
        last_exc = None
        for attempt in range(3):
            try:
                # 우선 httpx(HTTP/2) 시도
                if self.httpx_client is not None:
                    r = self.httpx_client.get(url, headers=headers)
                    if r.status_code == 200 and r.content:
                        return r.content
                    # 403/404 등은 한 번 더 시도 (쿠키 취득용 홈 접근)
                    if r.status_code in (403, 404) and referer:
                        try:
                            self.httpx_client.get(referer)
                        except Exception:
                            pass
                # fallback: requests
                r2 = self.session.get(url, timeout=10, stream=True, headers=headers)
                r2.raise_for_status()
                data = r2.content
                if data:
                    return data
            except Exception as e:
                last_exc = e
                time.sleep(0.4 * (attempt + 1))
                continue
        if last_exc:
            print(f"[ImageDownloadWorker] 다운로드 실패 ({url}): {last_exc}")
        return None


class ImageDownloader(QObject):
    """이미지 다운로더 관리자 (비동기)"""
    
    image_loaded = Signal(str, QPixmap)  # (url, pixmap)
    download_failed = Signal(str)  # (url)
    
    def __init__(self, cache: ImageCache):
        """초기화
        
        Args:
            cache: ImageCache 인스턴스
        """
        super().__init__()
        self.cache = cache
        self.active_workers = {}  # url -> worker 매핑
    
    def download(self, url: str):
        """이미지 다운로드 시작 (비동기)
        
        Args:
            url: 이미지 URL
        """
        if not url:
            self.download_failed.emit(url)
            return
        
        # 이미 다운로드 중이면 스킵
        if url in self.active_workers:
            return
        
        # 캐시 확인
        cached = self.cache.get(url)
        if cached:
            self.image_loaded.emit(url, cached)
            return
        
        # 워커 스레드 생성 및 시작
        worker = ImageDownloadWorker(self.cache, url)
        worker.image_loaded.connect(self._on_image_loaded)
        worker.download_failed.connect(self._on_download_failed)
        worker.finished.connect(lambda: self._remove_worker(url))
        
        self.active_workers[url] = worker
        worker.start()
    
    def _on_image_loaded(self, url: str, pixmap: QPixmap):
        """이미지 로딩 완료 처리"""
        self.image_loaded.emit(url, pixmap)
    
    def _on_download_failed(self, url: str):
        """다운로드 실패 처리"""
        self.download_failed.emit(url)
    
    def _remove_worker(self, url: str):
        """완료된 워커 제거"""
        if url in self.active_workers:
            worker = self.active_workers.pop(url)
            worker.deleteLater()
    
    def cancel_all(self):
        """모든 대기 중인 다운로드 취소 (페이지 변경 시)"""
        for url, worker in list(self.active_workers.items()):
            if worker.isRunning():
                worker.quit()
                worker.wait(100)  # 짧게 대기
                if worker.isRunning():
                    worker.terminate()
        self.active_workers.clear()
    
    def stop_all(self):
        """모든 다운로드 워커 중지 (앱 종료 시)"""
        for url, worker in list(self.active_workers.items()):
            if worker.isRunning():
                worker.quit()
                worker.wait(1000)  # 최대 1초 대기
                if worker.isRunning():
                    worker.terminate()
        self.active_workers.clear()


