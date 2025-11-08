# -*- coding: utf-8 -*-
"""
JAVLIBRARY (Playwright 기반) 최종판

기능
- /{locale}/ 검색(ID 우선 → 제목 보조) → '첫 번째 카드' 상세 → #video_jacket_img 1장 저장
- 제목/ID가 키워드와 '엄격 일치'(문자+숫자, 가운데 '-'만 옵션)일 때만 저장
- 브라우저 세션으로 쿠키 취득/유지(스토리지 파일 지속화) + 지수 백오프/지터(403/429 대응)
- 이미지 다운로드도 브라우저 쿠키로 수행
- 디버그 JSON 저장: debug_<키워드>_javlibrary_YYYYmmdd_HHMMSS.json

사전 설치:
    pip install playwright beautifulsoup4 lxml
    python -m playwright install chromium

환경 변수(선택):
    HEADLESS=0            # 창 띄우기(챌린지 통과 수동 지원)
    JL_STORAGE=./.jl_store # 쿠키/세션 저장 위치
"""

import os
import re
import json
import time
import random
import pathlib
import urllib.parse as up
from datetime import datetime
from typing import Optional, Tuple, List, Dict

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# -------------------------
# 설정
# -------------------------
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
MIN_BYTES = 10 * 1024
IMG_EXTS  = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}

ASSET_SEG_RE = re.compile(
    r"(?:^|/)(?:logo|favicon|sprite|icons?|ads?|banners?|static|themes|emoji|svg)(?:/|$)",
    re.I
)

DEFAULT_BASE     = "https://www.javlibrary.com"
DEFAULT_LOCALES  = ["en", "ja", "cn"]
STORAGE_DIR      = os.environ.get("JL_STORAGE", "./.jl_storage")
os.makedirs(STORAGE_DIR, exist_ok=True)

# -------------------------
# 유틸
# -------------------------
def is_probably_asset(url: str) -> bool:
    from urllib.parse import urlparse
    return bool(ASSET_SEG_RE.search(urlparse(url).path))

def urljoin(base: str, url: str) -> str:
    from urllib.parse import urljoin as _j
    return _j(base, url)

def compile_keyword_strict(keyword: str) -> re.Pattern:
    """
    문자+숫자 '정확 일치', 문자/숫자 사이 '-'만 옵션 허용
    예) STARS-080 ↔ STARS080
    """
    m = re.match(r"^\s*([A-Za-z]+)\s*-?\s*(\d+)\s*$", keyword)
    if not m:
        k = keyword.strip()
        k = re.escape(k).replace(r"\-", "-?")
        return re.compile(rf"(?<![A-Za-z0-9]){k}(?![A-Za-z0-9])", re.I)
    prefix, num = m.groups()
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(prefix)}-?{re.escape(num)}(?![A-Za-z0-9])", re.I)

def ext_from_content_type(ct: str) -> str:
    ct = (ct or "").lower()
    if "jpeg" in ct: return ".jpg"
    if "png"  in ct: return ".png"
    if "webp" in ct: return ".webp"
    if "gif"  in ct: return ".gif"
    if "bmp"  in ct: return ".bmp"
    if "avif" in ct: return ".avif"
    return ".jpg"

def save_debug_json(out_dir: str, keyword: str, debug: dict):
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"debug_{keyword}_javlibrary_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(debug, f, ensure_ascii=False, indent=2)
    print(f"[debug] log saved -> {path}")

def _sleep_jitter(base_sec=1.0, mult=1.0):
    time.sleep(base_sec*mult + random.random()*0.6)

# -------------------------
# Playwright 스토리지/쿠키
# -------------------------
def _storage_path(base: str, loc: str) -> str:
    host = re.sub(r"^https?://", "", base).replace("/", "_")
    return os.path.join(STORAGE_DIR, f"{host}_{loc}.json")

def cookies_to_header(cookies: list) -> str:
    kv = [f"{c['name']}={c['value']}" for c in cookies if 'name' in c and 'value' in c]
    return "; ".join(kv)

# -------------------------
# Playwright 요청 도우미
# -------------------------
def playwright_fetch_html_and_cookies(base: str, url: str, loc: str, headless: bool, debug: dict):
    """
    지정 URL로 이동해 HTML과 쿠키를 반환.
    저장된 storage_state가 있으면 재사용하고, 종료 시 갱신 저장하여 이후 실행에 활용.
    """
    base = re.sub(r'/(en|ja|cn)/?$', '', base.rstrip('/'))  # 안전 정규화
    storage_state_path = _storage_path(base, loc)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )

        ctx = p.chromium.new_context(
            user_agent=UA,
            java_script_enabled=True,
            ignore_https_errors=False
        ) if False else browser.new_context(  # keep code simple but explicit
            user_agent=UA,
            java_script_enabled=True,
            ignore_https_errors=False,
            storage_state=storage_state_path if os.path.exists(storage_state_path) else None
        )

        # over18 쿠키 보강
        try:
            ctx.add_cookies([
                {"name": "over18", "value": "1",  "domain": "www.javlibrary.com", "path": "/"},
                {"name": "over18", "value": "18", "domain": "www.javlibrary.com", "path": "/"},
            ])
        except Exception:
            pass

        page = ctx.new_page()
        # 홈/로캘 워밍업
        try:
            page.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        try:
            page.goto(f"{base}/{loc}/", wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        time.sleep(0.6)

        # 타겟 이동
        resp = page.goto(url, wait_until="networkidle", timeout=60000)
        status = resp.status if resp else -1
        html = page.content()
        cookies = ctx.cookies()

        # 스토리지 저장(이후 실행에서 재사용)
        try:
            ctx.storage_state(path=storage_state_path)
        except Exception:
            pass

        debug.setdefault("pl_resp_meta", {})[f"{loc}"] = {"status": status, "len_html": len(html)}
        ctx.close(); browser.close()
        return status, html, cookies

def download_image_via_playwright(cover_url: str, referer: str, ua: str, ctx_cookies: list, out_path: str):
    with sync_playwright() as p:
        req = p.request.new_context(extra_http_headers={
            "User-Agent": ua,
            "Referer": referer,
            "Cookie": cookies_to_header(ctx_cookies),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        })
        r = req.get(cover_url, timeout=45000)
        r.raise_for_status()
        content = r.body()
        if len(content) < MIN_BYTES:
            raise RuntimeError(f"image too small: {len(content)} bytes")
        with open(out_path, "wb") as f:
            f.write(content)

# -------------------------
# 파싱
# -------------------------
def find_first_card_and_href(soup: BeautifulSoup) -> Tuple[Optional[BeautifulSoup], Optional[str], Optional[str]]:
    cand = None
    for card in soup.select("div.video, .videothumblist .video, #rightcolumn .video"):
        a = card.select_one('a[href*="?v="], a[href^="/?v="], a[href^="./?v="]')
        if not a:
            continue
        title_node = a.select_one(".title, strong, h3, h2, span")
        title_txt = (title_node.get_text(" ", strip=True) if title_node else a.get_text(" ", strip=True)) or ""
        if len(title_txt) >= 2:
            cand = (a, title_txt, a.get("href"))
            break
    if not cand:
        for a in soup.select('a[href*="?v="]'):
            tt = a.get_text(" ", strip=True) or ""
            if len(tt) >= 2:
                cand = (a, tt, a.get("href"))
                break
    return cand if cand else (None, None, None)

def parse_detail_for_id_title_cover(html: str, base_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    soup = BeautifulSoup(html, "lxml")
    # cover
    cover = None
    img = soup.select_one("#video_jacket_img")
    if img:
        src = img.get("src") or img.get("data-src")
        if src:
            if src.startswith("//"): src = "https:" + src
            cover = urljoin(base_url, src)
    # title
    title = None
    tnode = soup.select_one("#video_title, #video_title h3, h3#video_title")
    if tnode:
        title = tnode.get_text(" ", strip=True)
    # code
    code = None
    for el in soup.find_all(string=re.compile(r"\bID\b", re.I)):
        txt = el.parent.get_text(" ", strip=True) if hasattr(el, "parent") else str(el)
        m = re.search(r"\bID\b\s*[:：]?\s*([A-Za-z]+-?\d+)", txt)
        if m:
            code = m.group(1)
            break
    if not code:
        vid = soup.select_one("#video_id")
        if vid:
            txt = vid.get_text(" ", strip=True)
            m = re.search(r"([A-Za-z]+-?\d+)", txt or "")
            if m:
                code = m.group(1)
    return code, title, cover

# -------------------------
# 메인 파이프라인
# -------------------------
def scrape_javlibrary(keyword: str, out_dir: str = "downloads",
                      base: str = DEFAULT_BASE,
                      locales: Optional[List[str]] = None):
    locales = locales or DEFAULT_LOCALES
    os.makedirs(out_dir, exist_ok=True)
    kw_re = compile_keyword_strict(keyword)

    debug: Dict = {"keyword": keyword, "site": "javlibrary", "base": base, "locales": locales}
    headless_env = os.environ.get("HEADLESS")
    headless = not (headless_env == "0" or headless_env == "false")

    # 베이스 정규화(혹시 /ja/ 등 붙여 전달되면 제거)
    base = re.sub(r'/(en|ja|cn)/?$', '', base.rstrip('/'))

    # 1) 로캘 우선순위로 검색(ID → 제목), 백오프/지연 포함
    search_url_used = None
    html = None
    pl_cookies = []
    last_status = None

    for loc in locales:
        home = f"{base}/{loc}/"
        id_url = f"{home}vl_searchbyid.php?{up.urlencode({'keyword': keyword})}"
        title_url = f"{home}vl_searchbytitle.php?{up.urlencode({'keyword': keyword})}"

        for target_url in (id_url, title_url):
            for attempt in range(4):  # 최대 4회 재시도
                status, html_text, cookies = playwright_fetch_html_and_cookies(base, target_url, loc, headless, debug)
                debug.setdefault("attempts", []).append({"loc": loc, "url": target_url, "status": status, "len": len(html_text)})
                last_status = status
                if status == 200 and len(html_text) > 600:
                    search_url_used, html, pl_cookies = target_url, html_text, cookies
                    break
                if status in (403, 429, 503):
                    _sleep_jitter(base_sec=1.5, mult=(attempt+1))
                    continue
                else:
                    _sleep_jitter(base_sec=0.8)
            if search_url_used:
                break
        if search_url_used:
            break

    debug["search_url"] = search_url_used
    debug["html_len"]   = len(html or "")

    if not html:
        debug["error"] = f"blocked_or_not_found(status={last_status})"
        save_debug_json(out_dir, keyword, debug)
        print("blocked/not found — see debug json.")
        return

    # 2) 검색 결과 파싱 → 첫 카드 상세
    soup = BeautifulSoup(html, "lxml")
    anchor, card_title, href = find_first_card_and_href(soup)
    debug["card_title_text"] = card_title
    detail_url = urljoin(search_url_used, href) if href else None
    # 검색 결과가 곧 상세일 수도 있음
    if not detail_url and soup.select_one("#video_jacket_img"):
        detail_url = search_url_used
    if not detail_url:
        debug["error"] = "no_result_card_or_detail"
        save_debug_json(out_dir, keyword, debug)
        print("no result card/detail — skipped")
        return

    # 3) 상세 페이지 → ID/제목/커버 추출
    status, detail_html, _ = playwright_fetch_html_and_cookies(base, detail_url, locales[0], headless, debug)
    debug["detail_url"] = detail_url
    debug.setdefault("detail_resp", {"status": status, "len": len(detail_html)})
    if status != 200 or len(detail_html) < 500:
        debug["error"] = f"detail_fetch_failed(status={status})"
        save_debug_json(out_dir, keyword, debug)
        print("detail fetch failed — skipped")
        return

    code, title, cover = parse_detail_for_id_title_cover(detail_html, base)
    debug["detail_code"]  = code
    debug["detail_title"] = title
    debug["detail_cover"] = cover

    # 4) 제목/ID 엄격 매칭
    title_ok = bool(title and kw_re.search(title))
    code_ok  = bool(code  and kw_re.search(code))
    debug["match"] = {"title_ok": title_ok, "code_ok": code_ok}
    if not (title_ok or code_ok):
        debug["error"] = "strict_match_failed"
        save_debug_json(out_dir, keyword, debug)
        print("strict title/id match failed — skipped")
        return

    # 5) 이미지 저장(파일명에 키워드 없으면 강제명)
    if not cover:
        debug["error"] = "no_cover_in_detail"
        save_debug_json(out_dir, keyword, debug)
        print("no cover — skipped")
        return

    from urllib.parse import urlparse
    name = pathlib.Path(urlparse(cover).path).name
    ext = pathlib.Path(name).suffix.lower() or ".jpg"
    if not name or not kw_re.search(name):
        name = f"{keyword}_01{ext}"
    out_path = os.path.join(out_dir, name)

    try:
        download_image_via_playwright(cover, referer=detail_url, ua=UA, ctx_cookies=pl_cookies, out_path=out_path)
        debug["accepted"] = [{"url": cover, "saved": out_path}]
        debug["rejected"] = []
        debug["downloaded_count"] = 1
        print(f"saved: {out_path}")
    except Exception as e:
        debug["accepted"] = []
        debug["rejected"] = [{"url": cover, "reason": f"download_error:{e}"}]
        debug["downloaded_count"] = 0

    save_debug_json(out_dir, keyword, debug)

# -------------------------
# 실행 예시
# -------------------------
if __name__ == "__main__":
    # HEADLESS=0 로 두면 창이 떠서 챌린지/동의가 나오면 직접 통과 가능
    scrape_javlibrary("STARS-080", out_dir="test_images", locales=["ja"])
    scrape_javlibrary("ZSD-74",   out_dir="test_images", locales=["ja"])
    scrape_javlibrary("4017-204", out_dir="test_images", locales=["ja"])
    scrape_javlibrary("asfg",     out_dir="test_images", locales=["ja"])
