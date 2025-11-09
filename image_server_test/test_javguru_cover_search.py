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
def create_http_client(prefer_cloudscraper: bool = True) -> requests.Session:
    """
    cloudscraper(가능하면) 우선, 실패 시 requests.Session
    """
    s = None
    if prefer_cloudscraper:
        try:
            import cloudscraper  # pip install cloudscraper
            s = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
        except Exception:
            s = None
    if s is None:
        s = requests.Session()

    s.headers.update(
        {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "DNT": "1",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
    )
    try:
        s.get(BASE + "/", headers={"Referer": BASE + "/"}, timeout=15)
    except Exception:
        pass
    return s


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
def get_search_html(keyword: str, session: requests.Session, debug: dict) -> Tuple[Optional[str], Optional[str]]:
    """
    기본 검색: /?s=<keyword>
    """
    url = f"{BASE}/?{up.urlencode({'s': keyword})}"
    try:
        r = session.get(url, headers={"Referer": BASE + "/"}, timeout=25, allow_redirects=True)
        if r.status_code == 200 and len(r.text) > 500:
            debug["search_html_status"] = r.status_code
            debug["search_url"] = url
            return r.text, url
        debug["search_html_status"] = r.status_code
        debug["search_html_len"] = len(r.text)
        return None, url
    except Exception as e:
        debug["search_html_error"] = repr(e)
        return None, url


def find_first_card_and_title_from_search(soup: BeautifulSoup) -> Tuple[Optional[BeautifulSoup], Optional[str]]:
    """
    검색 결과에서 첫 번째 카드(article)와 제목 텍스트 추출
    """
    for art in soup.select("article"):
        a = art.select_one("h2 a, h1 a, .entry-title a, a")
        if not a:
            continue
        title_txt = (a.get_text(" ", strip=True) or "").strip()
        if len(title_txt) >= 3:
            return art, title_txt
    return None, None


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
def find_first_post_via_rest(keyword: str, session: requests.Session, debug: dict) -> Tuple[Optional[str], Optional[str]]:
    """
    WP REST 검색: /wp-json/wp/v2/search?search=<keyword>
    """
    api = f"{BASE}/wp-json/wp/v2/search?{up.urlencode({'search': keyword, 'per_page': 10})}"
    try:
        r = session.get(api, headers={"Referer": BASE + "/"}, timeout=20)
        if r.status_code == 200:
            data = r.json()
            for obj in data:
                link = obj.get("url") or obj.get("link")
                title = obj.get("title") or obj.get("title_plain") or ""
                if link and title:
                    # REST title은 HTML 엔티티 포함 가능
                    title_text = BeautifulSoup(str(title), "html.parser").get_text(" ", strip=True)
                    return link, title_text
        debug["rest_status"] = r.status_code
    except Exception as e:
        debug["rest_error"] = repr(e)
    return None, None


def find_first_post_via_rss(keyword: str, session: requests.Session, debug: dict) -> Tuple[Optional[str], Optional[str]]:
    """
    RSS 검색: /?s=<keyword>&feed=rss2
    """
    feed = f"{BASE}/?{up.urlencode({'s': keyword, 'feed': 'rss2'})}"
    try:
        r = session.get(feed, headers={"Referer": BASE + "/"}, timeout=20)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "xml")
            item = soup.find("item")
            if item:
                link = item.findtext("link")
                title = item.findtext("title")
                return link, title
        debug["rss_status"] = r.status_code
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
def scrape_javguru(keyword: str, out_dir: str = "downloads"):
    """
    1) /?s= 검색 페이지에서 첫 카드 이미지 시도
    2) 1이 막히면 WP REST → RSS 순서로 첫 포스트 URL 확보
       → 포스트 페이지에서 대표 이미지 수집
    """
    debug = {"keyword": keyword, "min_bytes": MIN_BYTES, "site": "jav.guru"}
    kw_re = compile_keyword_strict(keyword)

    with create_http_client(prefer_cloudscraper=True) as s:
        # 1) 검색 페이지 시도
        search_html, search_url = get_search_html(keyword, s, debug)
        if search_html:
            soup = BeautifulSoup(search_html, "lxml")
            card, title = find_first_card_and_title_from_search(soup)
            debug["search_url"] = search_url
            debug["card_title_text"] = title

            if card and title and kw_re.search(title):
                imgs = collect_card_images(card, BASE)
                debug["candidate_count_search"] = len(imgs)
                debug["raw_candidates_search"] = imgs[:50]
                if imgs:
                    download_pipeline(keyword, out_dir, imgs, s, search_url, debug)
                    save_debug_json(out_dir, keyword, debug)
                    return
                else:
                    debug["warn"] = "no image on search card; fallback to post page"
            else:
                debug["warn"] = "title mismatch or no card; fallback to post page"

        # 2) 폴백: WP REST → RSS
        post_url, post_title = find_first_post_via_rest(keyword, s, debug)
        if not post_url:
            post_url, post_title = find_first_post_via_rss(keyword, s, debug)

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
            pr = s.get(post_url, headers={"Referer": BASE + "/"}, timeout=25)
            pr.raise_for_status()
            post_imgs = collect_post_cover_images(pr.text, post_url)
            debug["candidate_count_post"] = len(post_imgs)
            debug["raw_candidates_post"] = post_imgs[:50]

            if not post_imgs:
                debug["error"] = "no_cover_on_post"
                save_debug_json(out_dir, keyword, debug)
                print("no cover on post")
                return

            download_pipeline(keyword, out_dir, post_imgs, s, post_url, debug)
            save_debug_json(out_dir, keyword, debug)

        except Exception as e:
            debug["error"] = f"post_fetch_error:{e!r}"
            save_debug_json(out_dir, keyword, debug)
            print("post fetch error")


# ==========================
# 실행 예시
# ==========================
if __name__ == "__main__":
    # 동작 확인용 예시 (필요에 따라 수정)
    scrape_javguru("ZSD-74", out_dir="test_images_javguru")
    scrape_javguru("STARS-080", out_dir="test_images_javguru")
    scrape_javguru("IPZZ-663", out_dir="test_images_javguru")
