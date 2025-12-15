"""Playwright 재사용 서버(매니저).

요구사항
- Playwright는 '처음에만' 초기화하고 재활용
- 문제 발생(브라우저/페이지 크래시, 타임아웃 등) 시에만 해당 스레드의 Playwright를 재초기화

주의
- 본 프로젝트는 썸네일 업데이트를 여러 워커 스레드에서 수행합니다.
  Playwright sync API는 스레드 간 객체 공유가 안전하지 않으므로,
  스레드별로 browser/context/page를 1세트씩 생성해 재사용합니다.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class PlaywrightHTTPError(RuntimeError):
    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status}: {url}")
        self.status = status
        self.url = url


@dataclass
class _ThreadState:
    playwright: Any
    browser: Any
    context: Any
    page: Any
    created_at: float

    def close(self):
        # close는 best-effort로 진행 (종료 중 예외는 무시)
        try:
            if self.page:
                self.page.close()
        except Exception:
            pass
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass


class PlaywrightServer:
    """스레드별로 Playwright를 1회 초기화 후 재사용하는 매니저."""

    def __init__(
        self,
        headless: bool = True,
        user_agent: str = DEFAULT_UA,
        request_delay: float = 1.0,
        locale: str = "ko-KR",
        timezone_id: str = "Asia/Seoul",
        viewport: Optional[Dict[str, int]] = None,
        proxy_url: str = "",
    ):
        self._headless = headless
        self._ua = user_agent
        self._request_delay = float(request_delay)
        self._locale = locale
        self._timezone_id = timezone_id
        self._viewport = viewport or {"width": 1920, "height": 1080}
        self._proxy_url = (proxy_url or "").strip()

        self._lock = threading.Lock()
        self._states: Dict[int, _ThreadState] = {}  # thread_ident -> state

    def _create_state(self) -> _ThreadState:
        from playwright.sync_api import sync_playwright

        p = sync_playwright().start()
        launch_kwargs: Dict[str, Any] = {"headless": self._headless}
        if self._proxy_url:
            launch_kwargs["proxy"] = {"server": self._proxy_url}

        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            user_agent=self._ua,
            viewport=self._viewport,
            locale=self._locale,
            timezone_id=self._timezone_id,
        )
        page = context.new_page()
        return _ThreadState(
            playwright=p,
            browser=browser,
            context=context,
            page=page,
            created_at=time.time(),
        )

    def _get_state(self) -> _ThreadState:
        tid = threading.get_ident()
        with self._lock:
            st = self._states.get(tid)
            if st is None:
                st = self._create_state()
                self._states[tid] = st
            return st

    def reset_current_thread(self):
        """현재 스레드의 Playwright만 재초기화."""
        tid = threading.get_ident()
        with self._lock:
            st = self._states.pop(tid, None)
        if st:
            st.close()

    def close_all(self):
        """가능한 범위에서 모든 스레드의 Playwright를 정리(종료 시 호출)."""
        with self._lock:
            states = list(self._states.values())
            self._states.clear()
        for st in states:
            st.close()

    def warmup(self):
        """현재 스레드에서 1회 초기화(선택)."""
        _ = self._get_state()

    def get_html(
        self,
        url: str,
        referer: Optional[str] = None,
        wait_until: str = "domcontentloaded",
        timeout_ms: int = 30000,
        max_retries: int = 3,
        extra_wait_sec: float = 0.5,
    ) -> str:
        """Playwright로 HTML을 가져온다. 문제 발생 시 현재 스레드 상태를 리셋 후 재시도."""
        last_exc: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                if self._request_delay:
                    time.sleep(self._request_delay)
                st = self._get_state()
                if referer:
                    st.page.set_extra_http_headers({"Referer": referer})

                resp = st.page.goto(url, wait_until=wait_until, timeout=timeout_ms)
                if resp:
                    status = resp.status
                    if status == 403:
                        raise PlaywrightHTTPError(status, url)
                    if status >= 400:
                        raise PlaywrightHTTPError(status, url)

                if extra_wait_sec:
                    time.sleep(extra_wait_sec)
                return st.page.content()
            except Exception as e:
                last_exc = e
                # Playwright 객체가 꼬이는 케이스가 많아, 실패 시에는 현재 스레드만 리셋
                self.reset_current_thread()
                if attempt < max_retries - 1:
                    time.sleep(self._request_delay * (attempt + 2))
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("PlaywrightServer.get_html: max retries exceeded")

    def probe_image(
        self,
        url: str,
        referer: Optional[str] = None,
        timeout_ms: int = 15000,
    ) -> Dict[str, Optional[object]]:
        """이미지 여부/크기 검사 (Playwright 네비게이션 기반).

        반환:
          { ok: bool, final_url: str, ct: str, size: Optional[int] }
        """
        try:
            st = self._get_state()
            page = st.context.new_page()
            try:
                if referer:
                    page.set_extra_http_headers({"Referer": referer})
                resp = page.goto(url, wait_until="commit", timeout=timeout_ms)
                if resp:
                    headers = resp.headers
                    ct = (headers.get("content-type") or "").lower()
                    cl = headers.get("content-length")
                    size = int(cl) if cl and str(cl).isdigit() else None
                    return {"ok": ct.startswith("image/"), "final_url": resp.url, "ct": ct, "size": size}
            finally:
                try:
                    page.close()
                except Exception:
                    pass
        except Exception:
            # 실패 시에도 상태 리셋까지는 하지 않음(대량 probe 시 과도한 리셋 방지)
            pass
        return {"ok": False, "final_url": url, "ct": "", "size": None}


_singleton_lock = threading.Lock()
_singleton: Optional[PlaywrightServer] = None


def get_playwright_server() -> PlaywrightServer:
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:
            try:
                from config import PROXY_URL
            except Exception:
                PROXY_URL = ""
            _singleton = PlaywrightServer(
                headless=True,
                user_agent=DEFAULT_UA,
                request_delay=1.0,
                proxy_url=PROXY_URL,
            )
        return _singleton


