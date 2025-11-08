# -*- coding: utf-8 -*-
"""
JAVDB 전용: 검색 페이지의 '첫 번째 결과 카드' 커버 이미지만 추출/다운로드

요구사항
- 결과가 여러 개면 첫 카드만 사용
- 카드 '제목 텍스트'가 키워드와 '엄격 일치'할 때만 진행
  (문자+숫자 정확 동일, 문자/숫자 사이의 '-'만 옵션: 예 STARS-080 ↔ STARS080)
- 이미지 URL에 코드가 없어도 OK(제목으로 검증)
- 파일명에 코드가 없으면 저장 시 `키워드_XX.ext`로 강제 부여
- 디버그 JSON 저장

사용 예:
    scrape_javdb("STARS-080", out_dir="STARS-080_javdb")
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

# -------------------------
# 설정
# -------------------------
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
MIN_BYTES = 10 * 1024  # Content-Length가 있으면 10KB 이상만
IMG_EXTS  = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}

# 경로 세그먼트 기준 자산(logo/favicon/icon/banner/ads/emoji/svg 등) 제외
ASSET_SEG_RE = re.compile(
    r"(?:^|/)(?:logo|favicon|sprite|icons?|ads?|banners?|static|themes|emoji|svg)(?:/|$)",
    re.I
)

# javdb 미러 도메인
JAVDB_BASES = [
    "https://javdb.com",
    "https://javdb5.com",
    "https://javdb7.com",
    "https://javdb9.com",
]

# -------------------------
# 공통 유틸
# -------------------------
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
    path = os.path.join(out_dir, f"debug_{keyword}_javdb_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(debug, f, ensure_ascii=False, indent=2)
    print(f"[debug] log saved -> {path}")

# -------------------------
# HTTP 세션 (403 우회)
# -------------------------
def create_http_client(prefer_cloudscraper: bool = True):
    """
    requests.Session() 또는 cloudscraper(있으면) 반환.
    - 브라우저 헤더/over18 쿠키 적용
    """
    sess = None
    if prefer_cloudscraper:
        try:
            import cloudscraper  # type: ignore
            sess = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
        except Exception:
            sess = None
    if sess is None:
        sess = requests.Session()

    sess.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1",
    })
    for dom in [".javdb.com", ".javdb5.com", ".javdb7.com", ".javdb9.com"]:
        try:
            sess.cookies.set("over18", "1", domain=dom)
        except Exception:
            pass
    return sess

# -------------------------
# 검색 HTML 가져오기 (미러/워밍업/재시도)
# -------------------------
def get_search_html(keyword: str, session: requests.Session, debug: dict) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    last_err = None
    for base in JAVDB_BASES:
        url = f"{base}/search?{up.urlencode({'q': keyword, 'f': 'all'})}"
        try:
            # 홈 워밍업(쿠키/리다이렉트 유도)
            session.get(base + "/", headers={"Referer": base + "/"}, timeout=20)
            # 1차
            r = session.get(url, headers={"Referer": base + "/"}, timeout=25, allow_redirects=True)
            # 403이면 재시도
            if r.status_code == 403:
                time.sleep(1.2)
                session.get(base + "/", headers={"Referer": base + "/"}, timeout=20)
                r = session.get(url, headers={"Referer": base + "/"}, timeout=25, allow_redirects=True)

            if r.status_code == 200 and len(r.text) > 1000:
                debug.update({"javdb_base": base, "status": r.status_code})
                return r.text, url, base

            last_err = f"status={r.status_code}, len={len(r.text)}"
            debug.setdefault("javdb_attempts", []).append(
                {"base": base, "status": r.status_code, "len": len(r.text)}
            )
        except Exception as e:
            last_err = repr(e)
            debug.setdefault("javdb_attempts", []).append({"base": base, "error": last_err})
    debug["error"] = "javdb_blocked_or_403"
    debug["hint"] = "Install cloudscraper or update JAVDB_BASES with a working mirror."
    debug["last_err"] = last_err
    return None, None, None

# -------------------------
# 결과 카드/제목/이미지 추출
# -------------------------
def _extract_bg_url(style_str: str) -> Optional[str]:
    if not style_str: return None
    m = re.search(r"url\((['\"]?)(.+?)\1\)", style_str)
    return m.group(2) if m else None

def find_first_card_and_title(soup: BeautifulSoup) -> Tuple[Optional[BeautifulSoup], Optional[str]]:
    """
    첫 번째 결과 카드(anchor)와 제목 텍스트를 반환.
    - 보통 a[href^="/v/"] 가 카드 전체를 감쌈.
    - 제목은 anchor 내부 strong/.title 등에서 추출.
    - 'img/cover' 요소가 없는 a는 건너뜀(네비/광고 회피).
    """
    anchors = soup.select('a[href^="/v/"]')
    for a in anchors:
        has_visual = bool(a.select_one("img, .cover, .video-cover, .image"))
        if not has_visual:
            continue
        title_node = a.select_one(".title, strong, h3, h2")
        title_txt = (title_node.get_text(" ", strip=True) if title_node else a.get_text(" ", strip=True)) or ""
        if len(title_txt) >= 4:
            return a, title_txt
    return None, None

def collect_card_images(anchor, base_url: str) -> List[Dict[str, str]]:
    """
    카드 내부의 커버 이미지 수집:
    - <img src|data-src|data-original|srcset>
    - .cover/.image 스타일 background-image:url(...)
    """
    cands: List[Dict[str, str]] = []

    def add(u: Optional[str], how: str):
        if not u:
            return
        if u.startswith("//"):
            u = "https:" + u
        cands.append({"url": urljoin(base_url, u), "how": how})

    # <img>
    for img in anchor.select("img"):
        add(img.get("src"), "src")
        add(img.get("data-src"), "data-src")
        add(img.get("data-original"), "data-original")
        ss = img.get("srcset")
        if ss:
            parts = [p.strip() for p in ss.split(",") if p.strip()]
            for p in reversed(parts):   # 해상도 큰 것부터
                add(p.split()[0], "srcset")

    # background-image
    for cov in anchor.select(".cover, .video-cover, .image"):
        bg = _extract_bg_url(cov.get("style", ""))
        add(bg, "bg-style")

    # 중복 제거
    uniq, seen = [], set()
    for c in cands:
        if c["url"] not in seen:
            uniq.append(c); seen.add(c["url"])
    return uniq

# -------------------------
# 다운로드 파이프라인
# -------------------------
def head_or_small_get(url: str, session: requests.Session, referer: str) -> Dict[str, Optional[object]]:
    headers = {"User-Agent": UA, "Referer": referer}
    try:
        r = session.head(url, headers=headers, allow_redirects=True, timeout=15)
        ct = (r.headers.get("content-type") or "").lower()
        cl = r.headers.get("content-length")
        size = int(cl) if cl and cl.isdigit() else None
        return {"ok": ct.startswith("image/"), "final_url": r.url, "ct": ct, "size": size}
    except Exception:
        pass
    try:
        with session.get(url, headers=headers, stream=True, allow_redirects=True, timeout=25) as g:
            ct = (g.headers.get("content-type") or "").lower()
            cl = g.headers.get("content-length")
            size = int(cl) if cl and cl.isdigit() else None
            return {"ok": ct.startswith("image/"), "final_url": g.url, "ct": ct, "size": size}
    except Exception:
        return {"ok": False, "final_url": url, "ct": "", "size": None}

def download_pipeline(keyword: str, out_dir: str, url_items, session: requests.Session, referer: str, debug: dict):
    os.makedirs(out_dir, exist_ok=True)
    accepted, rejected = [], []
    kw_re = compile_keyword_strict(keyword)

    for i, item in enumerate(url_items, 1):
        u, how = item["url"], item["how"]

        if is_probably_asset(u):
            rejected.append({"url": u, "reason": "asset_segment", "how": how})
            continue

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

        # 파일명에 키워드가 없으면 키워드 기반 강제 이름
        dest_name = name if (name and kw_re.search(name)) else f"{keyword}_{i:02d}{ext}"
        dest = os.path.join(out_dir, dest_name)

        try:
            with session.get(final_url, headers={"User-Agent": UA, "Referer": referer}, stream=True, timeout=40) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk: f.write(chunk)
            accepted.append({"url": final_url, "saved": dest, "size": probe["size"], "ct": probe["ct"], "how": how})
            print(f"saved: {dest}")
            time.sleep(0.2)
        except Exception as e:
            rejected.append({"url": final_url, "reason": f"download_error:{e}", "how": how})

    debug["accepted"] = accepted
    debug["rejected"] = rejected
    debug["downloaded_count"] = len(accepted)

# -------------------------
# 메인 파이프라인
# -------------------------
def scrape_javdb(keyword: str, out_dir: str = "downloads"):
    debug = {"keyword": keyword, "min_bytes": MIN_BYTES, "site": "javdb"}
    with create_http_client(prefer_cloudscraper=True) as s:
        html, search_url, base = get_search_html(keyword, s, debug)
        debug["search_url"] = search_url
        debug["html_len"] = len(html or "")
        debug["base"] = base

        if not html:
            save_debug_json(out_dir, keyword, debug)
            print("javdb: blocked/403 — see debug json for attempts.")
            return

        soup = BeautifulSoup(html, "lxml")
        anchor, title = find_first_card_and_title(soup)
        debug["card_title_text"] = title
        if not anchor:
            debug["error"] = "no_result_card_anchor"
            save_debug_json(out_dir, keyword, debug)
            return

        kw_re = compile_keyword_strict(keyword)
        if not title or not kw_re.search(title):
            debug["card_title_match"] = False
            save_debug_json(out_dir, keyword, debug)
            print("title mismatch with keyword — skipped")
            return
        debug["card_title_match"] = True

        url_items = collect_card_images(anchor, base or "https://javdb.com")
        debug["raw_candidates"] = url_items[:50]

        download_pipeline(keyword, out_dir, url_items, s, search_url or (base + "/"), debug)
        save_debug_json(out_dir, keyword, debug)

# -------------------------
# 실행 예시
# -------------------------
if __name__ == "__main__":
    # cloudscraper 설치 권장: pip install cloudscraper
    # javdb
    scrape_javdb("STARS-080", out_dir="test_images")
    # javdb
    scrape_javdb("ZSD-74",   out_dir="test_images")
    # javdb
    scrape_javdb("asfg",   out_dir="test_images")
    # javdb
    scrape_javdb("IPZZ-663",   out_dir="test_images")
