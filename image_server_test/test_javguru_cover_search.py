# -*- coding: utf-8 -*-
"""
JAV.GURU 전용 스크레이퍼 (정리판)

목적
- 검색어로 jav.guru에서 '첫 번째 결과'를 찾고, 카드 커버(썸네일) 이미지를 다운로드
- 검색 HTML이 403 등으로 막히면, WP REST/RSS로 첫 포스트 URL을 찾아
  포스트 페이지의 대표 이미지(커버/OG)로 폴백

엄격 일치 규칙
- 제목이 키워드와 '문자+숫자' 동일, 문자/숫자 사이의 '-'만 옵션 (예: STARS-080 ↔ STARS080 허용)

기능 요약
- 첫 카드만 처리
- 제목이 키워드와 엄격 일치할 때만 진행
- 자산(logo/favicon/icon/ads/emoji/svg 등) 제외, image/* 만 허용, 최소 용량 10KB
- 파일명에 코드가 없으면 `키워드_XX.ext`로 저장
- 디버그 JSON: out_dir/debug_<keyword>_javguru_<ts>.json

사용 예:
    scrape_javguru("ZSD-74", out_dir="ZSD-74_javguru")
"""

import os
import re
import json
import time
import pathlib
import urllib.parse as up
from datetime import datetime
from typing import Optional, Tuple, List, Dict

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext

# ==========================
# 설정
# ==========================
BASE = "https://jav.guru"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MIN_BYTES = 10 * 1024  # Content-Length가 있으면 10KB 이상
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}

# 경로 세그먼트 기준 자산(logo/favicon/icon/banner/ads/emoji/svg 등) 제외
ASSET_SEG_RE = re.compile(
    r"(?:^|/)(?:logo|favicon|sprite|icons?|ads?|banners?|static|themes|emoji|svg)(?:/|$)", re.I
)


# ==========================
# 공통 유틸
# ==========================
def is_probably_asset(url: str) -> bool:
    return bool(ASSET_SEG_RE.search(up.urlparse(url).path))


def urljoin(base: str, url: str) -> str:
    return up.urljoin(base, url)


def compile_keyword_strict(keyword: str) -> re.Pattern:
    """
    문자+숫자 정확 일치, 문자/숫자 사이 '-' 옵션.
    예) 'STARS-080' -> (?<!alnum)STARS-?080(?!alnum)
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
    if "jpeg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    if "bmp" in ct:
        return ".bmp"
    if "avif" in ct:
        return ".avif"
    return ".jpg"


def save_debug_json(out_dir: str, keyword: str, debug: dict):
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"debug_{keyword}_javguru_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(debug, f, ensure_ascii=False, indent=2)
    print(f"[debug] log saved -> {path}")


# ==========================
# HTTP & 네트워크
# ==========================
REQUEST_DELAY = 1.0  # Playwright 요청 간 지연 시간

def create_http_client() -> requests.Session:
    """
    이미지 다운로드용 requests.Session (Playwright는 HTML 수집용)
    """
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
            "Connection": "keep-alive",
            "DNT": "1",
        }
    )
    return s


def get_html_playwright(url: str, page: Page, referer: str | None = None, max_retries: int = 3) -> str:
    """
    Playwright를 사용한 HTML 요청 (재시도 로직 포함)
    """
    for attempt in range(max_retries):
        try:
            time.sleep(REQUEST_DELAY)  # 요청 전 지연
            if referer:
                page.set_extra_http_headers({"Referer": referer})
            
            response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            if response and response.status >= 400:
                raise Exception(f"HTTP {response.status}")
            
            # 추가 대기 (동적 컨텐츠 로딩)
            time.sleep(0.5)
            
            return page.content()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait_time = REQUEST_DELAY * (attempt + 2)
            print(f"[retry {attempt + 1}/{max_retries}] 연결 실패, {wait_time:.1f}초 후 재시도... ({type(e).__name__})")
            time.sleep(wait_time)
    raise Exception("get_html_playwright: max retries exceeded")


def head_or_small_get(url: str, session: requests.Session, referer: str) -> Dict[str, Optional[object]]:
    """
    이미지 여부/크기 검사: HEAD → 실패 시 작은 GET
    """
    headers = {"User-Agent": UA, "Referer": referer}
    try:
        r = session.head(url, headers=headers, allow_redirects=True, timeout=15)
        ct = (r.headers.get("content-type") or "").lower()
        cl = r.headers.get("content-length")
        size = int(cl) if cl and cl.isdigit() else None
        return {"ok": (ct.startswith("image/") if ct else False), "final_url": r.url, "ct": ct, "size": size}
    except Exception:
        pass
    try:
        with session.get(url, headers=headers, stream=True, allow_redirects=True, timeout=25) as g:
            ct = (g.headers.get("content-type") or "").lower()
            cl = g.headers.get("content-length")
            size = int(cl) if cl and cl.isdigit() else None
            return {"ok": (ct.startswith("image/") if ct else False), "final_url": g.url, "ct": ct, "size": size}
    except Exception:
        return {"ok": False, "final_url": url, "ct": "", "size": None}


# ==========================
# 검색(우선) & 파싱
# ==========================
def get_search_html(keyword: str, page: Page, debug: dict, save_html: bool = True) -> Tuple[Optional[str], Optional[str]]:
    """
    기본 검색: /?s=<keyword> (Playwright 버전)
    """
    url = f"{BASE}/?{up.urlencode({'s': keyword})}"
    try:
        html = get_html_playwright(url, page, referer=BASE + "/")
        if html and len(html) > 500:
            debug["search_html_status"] = 200
            debug["search_url"] = url
            debug["search_html_length"] = len(html)
            
            # 디버그용 HTML 저장
            if save_html:
                os.makedirs("debug_html", exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                html_path = f"debug_html/search_{keyword}_{ts}.html"
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html)
                debug["saved_search_html"] = html_path
                print(f"[debug] 검색 HTML 저장 -> {html_path}")
            
            return html, url
        debug["search_html_status"] = "empty"
        debug["search_html_len"] = len(html) if html else 0
        return None, url
    except Exception as e:
        debug["search_html_error"] = repr(e)
        return None, url


def find_first_card_and_title_from_search(soup: BeautifulSoup) -> Tuple[Optional[BeautifulSoup], Optional[str], Optional[str]]:
    """
    검색 결과에서 첫 번째 카드, 제목, 링크 추출
    JAV.GURU는 article 대신 .grid1, .grid2 클래스를 사용
    검색 결과 카드에는 이미지가 없으므로 포스트 링크를 반환
    """
    # JAV.GURU는 .grid1, .grid2 클래스의 div를 사용
    for card in soup.select(".grid1, .grid2, .row > div"):
        # h2 a 태그에서 제목과 링크 추출
        a = card.select_one("h2 a, h1 a, .entry-title a, a")
        if not a:
            continue
        title_txt = (a.get_text(" ", strip=True) or "").strip()
        link = a.get("href", "")
        if len(title_txt) >= 3 and link:
            return card, title_txt, link
    return None, None, None


def collect_card_images(card_node, base_url: str) -> List[Dict[str, str]]:
    """
    카드 내부 썸네일/커버 이미지 수집:
    - <img src|data-src|data-original|data-lazy-src|srcset|data-lazy-srcset>
    - .post-thumbnail .thumb .cover .image 에 style="background-image:url(...)" 형태
    """
    cands: List[Dict[str, str]] = []

    def add(u: Optional[str], how: str):
        if not u:
            return
        if u.startswith("//"):
            u = "https:" + u
        cands.append({"url": urljoin(base_url, u), "how": how})

    if getattr(card_node, "select", None):
        for img in card_node.select("img"):
            add(img.get("src"), "img.src")
            add(img.get("data-src"), "img.data-src")
            add(img.get("data-original"), "img.data-original")
            add(img.get("data-lazy-src"), "img.data-lazy-src")
            ss = img.get("srcset") or img.get("data-lazy-srcset")
            if ss:
                parts = [p.strip() for p in ss.split(",") if p.strip()]
                for p in reversed(parts):  # 큰 해상도 우선
                    add(p.split()[0], "img.srcset")

        for cov in card_node.select(".post-thumbnail, .thumb, .cover, .image"):
            style = cov.get("style", "")
            m = re.search(r"url\((['\"]?)(.+?)\1\)", style)
            if m:
                add(m.group(2), "bg-style")

    # dedupe
    uniq, seen = [], set()
    for c in cands:
        if c["url"] not in seen:
            uniq.append(c)
            seen.add(c["url"])
    return uniq


# ==========================
# 폴백: WP REST / RSS
# ==========================
def find_first_post_via_rest(keyword: str, page: Page, debug: dict) -> Tuple[Optional[str], Optional[str]]:
    """
    WP REST 검색: /wp-json/wp/v2/search?search=<keyword> (Playwright 버전)
    """
    api = f"{BASE}/wp-json/wp/v2/search?{up.urlencode({'search': keyword, 'per_page': 10})}"
    try:
        html = get_html_playwright(api, page, referer=BASE + "/")
        # JSON 응답 파싱
        import json
        data = json.loads(html)
        if isinstance(data, list):
            for obj in data:
                link = obj.get("url") or obj.get("link")
                title = obj.get("title") or obj.get("title_plain") or ""
                if link and title:
                    # REST title은 HTML 엔티티 포함 가능
                    title_text = BeautifulSoup(str(title), "html.parser").get_text(" ", strip=True)
                    return link, title_text
        debug["rest_status"] = "ok"
    except Exception as e:
        debug["rest_error"] = repr(e)
    return None, None


def find_first_post_via_rss(keyword: str, page: Page, debug: dict) -> Tuple[Optional[str], Optional[str]]:
    """
    RSS 검색: /?s=<keyword>&feed=rss2 (Playwright 버전)
    """
    feed = f"{BASE}/?{up.urlencode({'s': keyword, 'feed': 'rss2'})}"
    try:
        html = get_html_playwright(feed, page, referer=BASE + "/")
        soup = BeautifulSoup(html, "xml")
        item = soup.find("item")
        if item:
            link = item.findtext("link")
            title = item.findtext("title")
            return link, title
        debug["rss_status"] = "ok"
    except Exception as e:
        debug["rss_error"] = repr(e)
    return None, None


def collect_post_cover_images(post_html: str, base_url: str) -> List[Dict[str, str]]:
    """
    포스트 페이지에서 대표 이미지 후보 수집(우선순위):
    1) img.wp-post-image / .post-thumbnail img
    2) 본문 첫 이미지(.entry-content img / article img)
    3) <meta property="og:image">
    4) <meta name="twitter:image"> (백업)
    """
    soup = BeautifulSoup(post_html, "lxml")
    cands: List[Dict[str, str]] = []

    def add(u: Optional[str], how: str):
        if not u:
            return
        if u.startswith("//"):
            u = "https:" + u
        cands.append({"url": urljoin(base_url, u), "how": how})

    # 1) 대표 이미지
    for sel in ["img.wp-post-image", ".post-thumbnail img"]:
        tag = soup.select_one(sel)
        if tag:
            add(tag.get("src"), f"{sel}.src")
            add(tag.get("data-src"), f"{sel}.data-src")

    # 2) 본문 첫 이미지
    first_img = soup.select_one(".entry-content img, article img")
    if first_img:
        add(first_img.get("src"), "entry-first-img.src")
        add(first_img.get("data-src"), "entry-first-img.data-src")

    # 3) OG/Twitter
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        add(og.get("content"), "meta.og:image")

    tw = soup.find("meta", attrs={"name": "twitter:image"})
    if tw and tw.get("content"):
        add(tw.get("content"), "meta.twitter:image")

    # dedupe
    uniq, seen = [], set()
    for c in cands:
        if c["url"] and c["url"] not in seen:
            uniq.append(c)
            seen.add(c["url"])
    return uniq


# ==========================
# 다운로드
# ==========================
def download_pipeline(
    keyword: str,
    out_dir: str,
    url_items: List[Dict[str, str]],
    session: requests.Session,
    referer: str,
    debug: dict,
):
    os.makedirs(out_dir, exist_ok=True)
    accepted, rejected = [], []
    kw_re = compile_keyword_strict(keyword)

    for i, item in enumerate(url_items, 1):
        u, how = item["url"], item["how"]

        # 자산/아이콘 제외
        if is_probably_asset(u):
            rejected.append({"url": u, "reason": "asset_segment", "how": how})
            continue

        # 이미지/사이즈 검사
        probe = head_or_small_get(u, session, referer)
        size_ok = (probe["size"] is None) or (probe["size"] >= MIN_BYTES)
        if not probe["ok"] and pathlib.Path(up.urlparse(u).path).suffix.lower() not in IMG_EXTS:
            rejected.append({"url": u, "reason": f"not_image({probe['ct']})", "how": how})
            continue
        if not size_ok:
            rejected.append({"url": u, "reason": f"small({probe['size']})", "how": how})
            continue

        final_url = probe["final_url"] or u
        parsed = up.urlparse(final_url)
        name = pathlib.Path(parsed.path).name
        ext = pathlib.Path(name).suffix.lower() or ext_from_content_type(probe["ct"])

        # 파일명: 키워드가 없으면 강제
        dest_name = name if (name and kw_re.search(name)) else f"{keyword}_{i:02d}{ext}"
        dest = os.path.join(out_dir, dest_name)

        try:
            with session.get(final_url, headers={"User-Agent": UA, "Referer": referer}, stream=True, timeout=40) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
            accepted.append({"url": final_url, "saved": dest, "size": probe["size"], "ct": probe["ct"], "how": how})
            print(f"saved: {dest}")
            time.sleep(0.12)
        except Exception as e:
            rejected.append({"url": final_url, "reason": f"download_error:{e}", "how": how})

    debug["accepted"] = accepted
    debug["rejected"] = rejected
    debug["downloaded_count"] = len(accepted)


# ==========================
# 메인
# ==========================
def scrape_javguru(keyword: str, out_dir: str = "downloads", headless: bool = True):
    """
    1) /?s= 검색 페이지에서 첫 카드 이미지 시도
    2) 1이 막히면 WP REST → RSS 순서로 첫 포스트 URL 확보
       → 포스트 페이지에서 대표 이미지 수집
    
    Playwright 기반으로 봇 탐지 우회
    """
    debug = {"keyword": keyword, "min_bytes": MIN_BYTES, "site": "jav.guru", "mode": "playwright"}
    kw_re = compile_keyword_strict(keyword)

    # 이미지 다운로드용 세션 (requests)
    download_session = create_http_client()

    with sync_playwright() as p:
        # 브라우저 실행 (Chromium)
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=UA,
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR",
            timezone_id="Asia/Seoul",
        )
        page = context.new_page()

        try:
            # 1) 검색 페이지에서 링크 추출
            search_html, search_url = get_search_html(keyword, page, debug, save_html=True)
            post_url, post_title = None, None
            
            if search_html:
                soup = BeautifulSoup(search_html, "lxml")
                card, title, link = find_first_card_and_title_from_search(soup)
                debug["search_url"] = search_url
                debug["card_title_text"] = title
                debug["card_link"] = link
                
                # 디버그: grid 요소 개수 확인
                grids = soup.select(".grid1, .grid2")
                debug["grid_count"] = len(grids)
                print(f"[debug] grid 요소 발견: {len(grids)}개")

                # JAV.GURU 검색 결과 카드에는 이미지가 없으므로 바로 포스트 페이지로 이동
                if card and title and link and kw_re.search(title):
                    post_url = link
                    post_title = title
                    debug["method"] = "direct_from_search"
                    print(f"[debug] 검색 결과에서 링크 추출: {post_url}")
                else:
                    debug["warn"] = "title mismatch or no card; fallback to REST/RSS"

            # 2) 폴백: WP REST → RSS (검색 페이지에서 링크를 못 찾았을 때만)
            if not post_url:
                post_url, post_title = find_first_post_via_rest(keyword, page, debug)
            if not post_url:
                post_url, post_title = find_first_post_via_rss(keyword, page, debug)

            if not post_url or not post_title:
                debug["error"] = "no_result_via_rest_rss"
                save_debug_json(out_dir, keyword, debug)
                print("no result via REST/RSS")
                return

            debug["post_url"] = post_url
            debug["post_title_text"] = post_title

            # 제목 엄격 매칭
            if not kw_re.search(post_title or ""):
                debug["error"] = "title_mismatch_on_post"
                save_debug_json(out_dir, keyword, debug)
                print("title mismatch on post")
                return

            # 포스트 페이지에서 대표 이미지 수집
            try:
                post_html = get_html_playwright(post_url, page, referer=BASE + "/")
                post_imgs = collect_post_cover_images(post_html, post_url)
                debug["candidate_count_post"] = len(post_imgs)
                debug["raw_candidates_post"] = post_imgs[:50]

                if not post_imgs:
                    debug["error"] = "no_cover_on_post"
                    save_debug_json(out_dir, keyword, debug)
                    print("no cover on post")
                    return

                download_pipeline(keyword, out_dir, post_imgs, download_session, post_url, debug)
                save_debug_json(out_dir, keyword, debug)

            except Exception as e:
                debug["error"] = f"post_fetch_error:{e!r}"
                save_debug_json(out_dir, keyword, debug)
                print("post fetch error")
        
        finally:
            browser.close()
            download_session.close()


# ==========================
# 실행 예시
# ==========================
if __name__ == "__main__":
    # 동작 확인용 예시 (필요에 따라 수정)
    print("🚀 Playwright를 사용하여 요청 중...")
    scrape_javguru("THZA-05", out_dir="test_images_javguru", headless=True)
    # scrape_javguru("STARS-080", out_dir="test_images_javguru", headless=True)
    # scrape_javguru("EBOD-203", out_dir="test_images_javguru", headless=True)
