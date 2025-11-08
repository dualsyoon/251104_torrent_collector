# -*- coding: utf-8 -*-
"""
Sukebei Nyaa 검색 → '첫 번째 결과(제목 엄격 매칭)' 상세 페이지에서
🔹상세페이지에 '이미지 형태(= 실제 <img>)'로 들어있는 URL만 추출(필수)
🔹썸네일 업그레이드( *_t.jpg → .jpg ) 없음  ← 요구사항
🔹HTML 뷰어(.html) 따라가지 않음         ← 요구사항

기본 규칙
- 검색 결과가 여러 개여도 '첫 번째 결과'만 처리.
- 그 결과의 제목이 키워드와 '엄격 매칭'일 때만 진행.
  (문자+숫자가 정확히 같고, 문자/숫자 사이의 '-' 만 옵션. 예: STARS-080 ↔ STARS080)
- 상세 페이지의 '설명(Description)' 영역 중심으로 '렌더된 이미지(<img>)'만 수집:
  - <img>의 src / data-src / data-original / srcset
  - (설명이 마크다운 원문일 경우) [![](IMG)](...)의 IMG만 인식, LINK는 무시
- 자산/아이콘(logo/favicon/icon/ads 등) 제외, image/* 만 허용, 최소 용량(MIN_BYTES) 필터
- 디버그 JSON을 out_dir/debug_<tag>_<ts>.json으로 저장

엔트리포인트
- scrape_nyaa_image_urls_by_keyword(keyword, out_dir="downloads", download=False)
  → 검색어로 상세 페이지를 찾아가고, 상세페이지에 '보이는' 이미지 URL만 리스트 반환(+선택 저장)
"""

import os
import re
import json
import time
import pathlib
import urllib.parse as up
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# -------------------- 설정 --------------------
BASE_SEARCH = "https://sukebei.nyaa.si/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Python-requests"
MIN_BYTES = 10 * 1024  # 10KB (Content-Length 있으면 최소 용량, 없으면 무시)
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}

# 썸네일/프록시/자산 필터
THUMB_HOSTS = ("i0.wp.com", "i1.wp.com", "i2.wp.com")
ASSET_SEG_RE = re.compile(
    r"(?:^|/)(?:logo|favicon|sprite|icons?|ads?|banners?|static|assets|themes|emoji|svg)(?:/|$)",
    re.I
)

# 마크다운 이미지+링크 쌍 (IMG와 LINK를 분리. 우리는 IMG만 사용)
MD_IMG_LINK_RE = re.compile(
    r'\[!\[[^\]]*\]\((?P<img>https?://[^\s\)\]]+)\)\]\((?P<link>https?://[^\s\)\]]+)\)',
    re.I
)

# -------------------- 헤더/유틸 --------------------
def make_headers(referer: str | None = None) -> dict:
    h = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1",
    }
    if referer:
        h["Referer"] = referer
    return h

def is_probably_asset(u: str) -> bool:
    path = up.urlparse(u).path
    return bool(ASSET_SEG_RE.search(path))  # uploads 여부와 무관, 세그먼트로 판단

def compile_keyword_strict(keyword: str) -> re.Pattern:
    """
    '문자+숫자'가 정확히 같고, 문자/숫자 사이의 '-' 만 옵션.
    예) 'STARS-080' -> (?<!alnum)STARS-?080(?!alnum)
    """
    m = re.match(r"^\s*([A-Za-z]+)\s*-?\s*(\d+)\s*$", keyword)
    if not m:
        k = keyword.strip()
        k = re.escape(k).replace(r"\-", "-?")
        return re.compile(rf"(?<![A-Za-z0-9]){k}(?![A-Za-z0-9])", re.I)
    prefix, num = m.groups()
    return re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(prefix)}-?{re.escape(num)}(?![A-Za-z0-9])",
        re.I,
    )

def urljoin(base, url):
    return up.urljoin(base, url)

def ext_from_content_type(ct: str) -> str:
    ct = (ct or "").lower()
    if "jpeg" in ct: return ".jpg"
    if "png"  in ct: return ".png"
    if "webp" in ct: return ".webp"
    if "gif"  in ct: return ".gif"
    if "bmp"  in ct: return ".bmp"
    if "avif" in ct: return ".avif"
    return ".jpg"

def head_or_small_get(url: str, session: requests.Session, referer: str):
    headers = make_headers(referer)
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

def get_html(url: str, session: requests.Session, referer: str | None = None) -> str:
    r = session.get(url, headers=make_headers(referer), timeout=25)
    r.raise_for_status()
    return r.text

# -------------------- 검색/파싱 --------------------
def build_search_url(keyword: str) -> str:
    qs = up.urlencode({"f": 0, "c": "0_0", "q": keyword})
    return f"{BASE_SEARCH}?{qs}"

def find_first_result_and_title(soup: BeautifulSoup):
    """
    검색 결과 테이블에서 가장 먼저 나오는 '/view/xxxx' 링크와 제목 텍스트를 찾음.
    """
    a = soup.select_one("td a[href^='/view/'], a[href^='/view/']")
    if not a:
        return None, None
    title = (a.get_text(" ", strip=True) or "")[:500]
    href = a.get("href")
    if not href:
        return None, None
    return urljoin(BASE_SEARCH, href), title

def find_description_nodes(soup: BeautifulSoup):
    """
    상세 페이지에서 '설명'에 해당하는 컨테이너 후보들을 찾는다.
    - id 우선: #torrent-description, #description
    - 패널 구조: 'Description' 헤더를 가진 panel의 body
    - 백업: 기사/본문스러운 블록 몇 개
    """
    nodes = []
    nodes.extend(soup.select("#torrent-description, #description"))
    for panel in soup.select(".panel"):
        header = panel.select_one(".panel-heading")
        if header and re.search(r"\bdescription\b", header.get_text(" ", strip=True), re.I):
            body = panel.select_one(".panel-body") or panel
            nodes.append(body)
    if not nodes:
        nodes.extend(soup.select("article"))
    if not nodes:
        nodes.extend(soup.select("div.content, .content, .container"))
    seen, uniq = set(), []
    for n in nodes:
        k = str(n)
        if k not in seen:
            uniq.append(n); seen.add(k)
    return uniq[:3]

# -------------------- 공통 저장/로깅 --------------------
def save_debug_json(out_dir: str, tag: str, debug: dict):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"debug_{tag}_{ts}.json")
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(debug, f, ensure_ascii=False, indent=2)
    print(f"[debug] log saved -> {path}")

# -------------------- 핵심: 상세페이지에서 '보이는 이미지' URL만 추출 --------------------
def extract_visible_image_urls_from_view(
    view_url: str,
    out_dir: str = "downloads",
    download: bool = False,             # 기본은 URL만 출력(요구: "url출력")
    session: requests.Session | None = None,
    referer: str | None = None,
) -> list[str]:
    """
    상세페이지에 '보이는' 이미지 URL만 반환(+선택 저장).
    - <img>의 src / data-src / data-original / srcset 만 대상
    - 마크다운이 원문으로 있을 때는 [![](IMG)](...) 의 IMG만 추출 (LINK는 무시)
    - a[href]의 .html 뷰어는 따르지 않음
    - 프록시 deproxy, 썸네일 업그레이드( *_t → 원본 ) 같은 조작 없음
    - image/* 만 허용, MIN_BYTES 필터, 자산(logo/icon/ads 등) 제외
    """
    debug = {"view_url": view_url, "mode": "visible_imgs_only", "min_bytes": MIN_BYTES}
    s = session or requests.Session()
    results = []

    # 상세 HTML 로딩
    html = get_html(view_url, s, referer=referer or BASE_SEARCH)
    dsoup = BeautifulSoup(html, "lxml")
    debug["detail_html_len"] = len(html)

    # 설명 영역 우선 + 백업
    nodes = find_description_nodes(dsoup)
    if not nodes:
        nodes = [dsoup]

    # 1) DOM 기준으로 이미지 후보 수집(<img>만!)
    raw = []
    def add_img_url(u, how):
        if u:
            raw.append({"url": urljoin(view_url, u), "how": how})

    for n in nodes:
        for img in n.find_all("img"):
            add_img_url(img.get("src"), "img.src")
            add_img_url(img.get("data-src"), "img.data-src")
            add_img_url(img.get("data-original"), "img.data-original")
            srcset = img.get("srcset")
            if srcset:
                parts = [p.strip() for p in srcset.split(",") if p.strip()]
                for p in parts:
                    add_img_url(p.split()[0], "img.srcset")

    # 2) 마크다운이 원문으로 있을 수 있으므로 [![](IMG)](LINK) → IMG만 추가
    for m in MD_IMG_LINK_RE.finditer(html or ""):
        img_url = m.group("img")
        if img_url:
            add_img_url(img_url, "md.img")

    # 중복 제거
    seen, cands = set(), []
    for it in raw:
        u = it["url"]
        if u not in seen:
            cands.append(it); seen.add(u)

    debug["candidate_count"] = len(cands)
    debug["candidates_sample"] = cands[:20]

    accepted, rejected = [], []

    # 저장 파일명 prefix (뷰 ID 추출)
    view_id = re.search(r"/view/(\d+)", view_url)
    base_prefix = f"view{view_id.group(1)}" if view_id else "view"

    for i, item in enumerate(cands, 1):
        u, how = item["url"], item["how"]

        # a) 자산/아이콘 제외
        if is_probably_asset(u):
            rejected.append({"url": u, "reason": "asset_segment", "how": how})
            continue

        # b) URL이 .html 등 이미지가 아니면 제외 (우리는 <img>만 수집했으나 안전장치)
        ext = pathlib.Path(up.urlparse(u).path).suffix.lower()
        if ext == ".html":
            rejected.append({"url": u, "reason": "html_viewer_not_allowed", "how": how})
            continue

        # c) 네트워크 검사: image/* 만 허용 + 최소 용량
        probe = head_or_small_get(u, s, referer=view_url)
        if not probe["ok"]:
            rejected.append({"url": u, "reason": f"not_image({probe['ct']})", "how": how})
            continue
        size_ok = (probe["size"] is None) or (probe["size"] >= MIN_BYTES)
        if not size_ok:
            rejected.append({"url": u, "reason": f"small({probe['size']})", "how": how})
            continue

        # d) 통과 → URL 출력(필수), 필요 시 저장
        results.append(u)
        if download:
            name = pathlib.Path(up.urlparse(u).path).name or f"{base_prefix}_{i:02d}{ext_from_content_type(probe['ct'])}"
            dest = os.path.join(out_dir, name)
            os.makedirs(out_dir, exist_ok=True)
            try:
                with s.get(u, headers=make_headers(view_url), stream=True, timeout=40) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as f:
                        for chunk in r.iter_content(8192):
                            if chunk: f.write(chunk)
                accepted.append({"url": u, "saved": dest, "ct": probe["ct"], "size": probe["size"], "how": how})
                print(f"saved: {dest}")
                time.sleep(0.08)
            except Exception as e:
                rejected.append({"url": u, "reason": f"download_error:{e}", "how": how})
        else:
            accepted.append({"url": u, "ct": probe["ct"], "size": probe["size"], "how": how})

    debug["accepted"] = accepted
    debug["rejected"] = rejected
    debug["returned_count"] = len(results)
    save_debug_json(out_dir, f"view_{view_id.group(1) if view_id else 'manual'}", debug)

    return results

# -------------------- 엔트리: 검색어로 상세 찾아가서 '보이는 이미지' URL만 --------------------
def scrape_nyaa_image_urls_by_keyword(
    keyword: str,
    out_dir: str = "downloads",
    download: bool = False  # 기본 False: "url 출력" 중심(요구사항)
) -> list[str]:
    """
    1) 검색어로 검색 페이지 진입
    2) '첫 번째 결과'의 제목을 키워드와 엄격 매칭 체크
    3) 통과 시 상세(view) 페이지로 이동하여 '보이는 이미지' URL만 추출(+선택 저장)
    """
    debug = {"keyword": keyword, "base": BASE_SEARCH, "mode": "by_keyword_visible_imgs", "min_bytes": MIN_BYTES}
    kw_re = compile_keyword_strict(keyword)

    with requests.Session() as s:
        # 1) 검색
        search_url = build_search_url(keyword)
        html = get_html(search_url, s, referer=BASE_SEARCH)
        debug["search_url"] = search_url
        debug["search_html_len"] = len(html)

        soup = BeautifulSoup(html, "lxml")
        view_url, title_text = find_first_result_and_title(soup)
        debug["view_url"] = view_url
        debug["title_text"] = title_text

        # 2) 제목 엄격 매칭
        if not view_url or not title_text:
            debug["error"] = "no_result_view_link"
            save_debug_json(out_dir, keyword, debug)
            return []

        if not kw_re.search(title_text or ""):
            debug["error"] = "title_not_match_first_result"
            save_debug_json(out_dir, keyword, debug)
            return []

        # 3) 상세 페이지에서 '보이는 이미지'만 추출
        urls = extract_visible_image_urls_from_view(
            view_url,
            out_dir=out_dir,
            download=download,
            session=s,
            referer=search_url
        )

    # 요약 디버그
    save_debug_json(out_dir, f"keyword_{keyword}", {
        "keyword": keyword,
        "view_url": view_url,
        "download": download,
        "found_count": len(urls)
    })
    return urls

# -------------------- 실행 예시 --------------------
if __name__ == "__main__":
    # 예) 검색어로 상세 찾아가서 '보이는 이미지' URL만 출력
    urls = scrape_nyaa_image_urls_by_keyword(
        "4017-XXX",          # 예시 키워드
        out_dir="test_images",
        download=False       # URL만(저장 X)
    )
    print("URLS ON PAGE (IMG ONLY):", *urls, sep="\n")
