# -*- coding: utf-8 -*-
"""
JAVMOST(www5.javmost.com) 검색 → '첫 번째 결과(제목 엄격 매칭)' 상세 페이지에서
🔹상세페이지에 '이미지 형태(= 실제 <img>)'로 들어있는 URL만 추출(필수)
🔹썸네일 업그레이드( *_t.jpg → .jpg ) 없음  ← 요구사항
🔹HTML 뷰어(.html) 따라가지 않음         ← 요구사항

기본 규칙
- 검색 결과가 여러 개여도 '첫 번째 결과'만 처리.
- 그 결과의 제목이 키워드와 '엄격 매칭'일 때만 진행.
  (문자+숫자가 정확히 같고, 문자/숫자 사이의 '-' 만 옵션. 예: STARS-080 ↔ STARS080)
- 상세 페이지의 '보이는 이미지(<img>)'만 수집:
  - <img>의 src / data-* / srcset
  - (설명이 마크다운 원문일 경우) [![](IMG)](...)의 IMG만 인식, LINK는 무시
- 자산/아이콘(logo/favicon/icon/ads 등) 제외, image/* 만 허용, 최소 용량(MIN_BYTES) 필터
- 디버그 JSON을 out_dir/debug_<tag>_<ts>.json 으로 저장

엔트리포인트
- scrape_javmost_image_urls_by_keyword(keyword, out_dir="downloads", download=False, **extract_opts)
  → 검색어로 상세 페이지를 찾아가고, 상세페이지에 '보이는' 이미지 URL만 리스트 반환(+선택 저장)

주의
- JAVMOST는 대표 포스터를 메타태그나 JS로만 노출하는 경우가 있어 기본 옵션으로
  og/twitter 이미지 폴백(include_meta=True)과 포스터 추정(poster_guess=True)을 활성화했습니다.
  엄격히 "<img>만" 원하시면 두 옵션을 False 로 바꿔 쓰세요.
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
BASE = "https://www5.javmost.com/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Python-requests"
MIN_BYTES = 10 * 1024  # 10KB (Content-Length 있으면 최소 용량, 없으면 무시)
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}

# 썸네일/프록시/자산/광고 필터
THUMB_HOSTS = ("i0.wp.com", "i1.wp.com", "i2.wp.com")
ASSET_SEG_RE = re.compile(
    r"(?:^|/)(?:logo|favicon|sprite|icons?|ads?|adserver|banners?|static|assets|themes|emoji|svg)(?:/|$)",
    re.I
)
AD_HOST_RE = re.compile(r"(?:exosrv|exdynsrv|syndication|doubleclick|adnxs|taboola|outbrain|histats)", re.I)

# 마크다운 이미지+링크 쌍 (IMG만 사용)
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
    pr = up.urlparse(u)
    if AD_HOST_RE.search(pr.netloc):  # 광고/트래킹 도메인 컷
        return True
    path = pr.path
    if any(host in pr.netloc for host in THUMB_HOSTS):
        return True
    return bool(ASSET_SEG_RE.search(path))

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

def normalize_code(keyword: str) -> tuple[str | None, str | None, str | None]:
    """
    키워드를 'PREFIX-NNN' 형태로 정규화하여 (prefix, num, code) 튜플 반환.
    실패 시 (None, None, None)
    """
    m = re.match(r"^\s*([A-Za-z]+)\s*-?\s*(\d+)\s*$", keyword)
    if not m:
        return None, None, None
    prefix, num = m.groups()
    code = f"{prefix.upper()}-{num}"
    return prefix.upper(), num, code

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

# -------------------- JAVMOST 검색/해결 --------------------
def try_direct_view(session: requests.Session, code: str) -> tuple[str | None, str | None]:
    """
    /<CODE>/ 직행 시도. 성공하면 (view_url, title_text) 반환.
    """
    view = urljoin(BASE, f"{code}/")
    try:
        html = get_html(view, session, referer=BASE)
        soup = BeautifulSoup(html, "lxml")
        title = ""
        if soup.title and soup.title.get_text():
            title = soup.title.get_text(" ", strip=True)
        h = soup.find(["h1","h2"])
        if not title and h:
            title = h.get_text(" ", strip=True)
        return view, (title or "")
    except Exception:
        return None, None

def find_from_tag_listing(session: requests.Session, prefix: str, kw_re: re.Pattern) -> tuple[str | None, str | None]:
    """
    /tag/<PREFIX>/ 목록에서 '첫번째 엄격매칭' 포스트 링크를 찾는다.
    """
    tag_url = urljoin(BASE, f"tag/{prefix}/")
    html = get_html(tag_url, session, referer=BASE)
    soup = BeautifulSoup(html, "lxml")

    # a[href]들 중에서 텍스트가 코드에 '엄격매칭'되는 첫 번째를 채택.
    for a in soup.select("a[href]"):
        txt = (a.get_text(" ", strip=True) or "")[:500]
        href = a.get("href") or ""
        if not href:
            continue
        # 내비/태그/배너/페이지네이션 제외
        if any(seg in href for seg in ("/tag/", "/maker/", "/director/", "/category/", "/search/", "/allcode/")):
            continue
        if kw_re.search(txt):
            return urljoin(BASE, href), txt
    return None, None

def resolve_view_url_and_title(keyword: str, session: requests.Session) -> tuple[str | None, str | None, dict]:
    """
    키워드로 상세(view) URL과 제목을 찾아준다.
    1) /<CODE>/ 직행
    2) /tag/<PREFIX>/ 목록에서 첫 매칭
    """
    debug_steps = {}
    kw_re = compile_keyword_strict(keyword)
    prefix, num, code = normalize_code(keyword)

    # 1) 직접 슬러그
    if code:
        v, t = try_direct_view(session, code)
        debug_steps["direct_code"] = {"code": code, "found": bool(v)}
        if v and kw_re.search((t or "")):
            return v, t, debug_steps

    # 2) 태그 목록 검색
    if prefix:
        v, t = find_from_tag_listing(session, prefix, kw_re)
        debug_steps["tag_listing"] = {"prefix": prefix, "found": bool(v)}
        if v and t and kw_re.search(t):
            return v, t, debug_steps

    return None, None, debug_steps

# -------------------- 설명/콘텐츠 영역 후보 --------------------
def find_description_nodes(soup: BeautifulSoup):
    """
    상세 페이지에서 주 콘텐츠 영역 후보를 찾는다.
    - JAVMOST는 명시 'description' 아이디가 없는 경우가 많으므로
      본문/아티클/메인 컨테이너 위주로 스캔
    """
    nodes = []
    # 흔한 본문 컨테이너들
    nodes.extend(soup.select("article, main, section"))
    nodes.extend(soup.select("div.post, div.single, div.entry-content, div.content, .container"))
    if not nodes:
        nodes = [soup]
    # 중복 제거
    seen, uniq = set(), []
    for n in nodes:
        k = str(n)
        if k not in seen:
            uniq.append(n); seen.add(k)
    return uniq[:5]

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
    download: bool = False,             # 기본은 URL만 출력
    session: requests.Session | None = None,
    referer: str | None = None,
    include_noscript: bool = True,      # lazy-load noscript 내 <img> 허용(권장)
    include_meta: bool = True,          # og:image 등 메타 폴백(기본 ON) - 필요시 False
    include_script: bool = False,       # <script> 내 이미지 URL 폴백(기본 OFF)
    include_video_poster: bool = False, # <video poster> 허용(기본 OFF)
    poster_guess: bool = True,          # JAVMOST 전용 포스터 추정(기본 ON)
) -> list[str]:
    """
    상세페이지에 '보이는' 이미지 URL만 반환(+선택 저장).
    - <img>의 src / data-* / srcset 대상
    - [![](IMG)](LINK) → IMG만 추출 (LINK는 무시)
    - a[href]의 .html 뷰어는 따르지 않음
    - 프록시 deproxy, 썸네일 업그레이드 같은 조작 없음
    - image/* 만 허용, MIN_BYTES 필터, 자산/광고 제외
    - (옵션) og/twitter 메타 폴백, (옵션) 포스터 추정
    """
    debug = {
        "view_url": view_url,
        "mode": "visible_imgs_only",
        "min_bytes": MIN_BYTES,
        "opts": {
            "include_noscript": include_noscript,
            "include_meta": include_meta,
            "include_script": include_script,
            "include_video_poster": include_video_poster,
            "poster_guess": poster_guess,
        }
    }
    s = session or requests.Session()
    results, raw = [], []

    def add_img_url(u, how):
        if u:
            raw.append({"url": urljoin(view_url, u), "how": how})

    # 상세 HTML 로딩
    html = get_html(view_url, s, referer=referer or BASE)
    dsoup = BeautifulSoup(html, "lxml")
    debug["detail_html_len"] = len(html)

    # 설명/본문 영역 후보
    nodes = find_description_nodes(dsoup) or [dsoup]
    extra = dsoup.select("div#main, div#primary, div#content, div.single, div.entry-content")
    nodes = (nodes + extra)[:8]

    # 1) 실제 DOM의 <img> + 광범위 data-* 속성 커버
    DATA_ATTR_HINTS = {
        "data-src", "data-original", "data-lazy", "data-lazy-src",
        "data-echo", "data-image", "data-img", "data-url", "data-srcset"
    }
    for n in nodes:
        for img in n.find_all("img"):
            # 표준
            add_img_url(img.get("src"), "img.src")
            # srcset
            srcset = img.get("srcset")
            if srcset:
                for p in [p.strip() for p in srcset.split(",") if p.strip()]:
                    add_img_url(p.split()[0], "img.srcset")
            # 포괄적 data-*
            for k, v in img.attrs.items():
                if not v or not isinstance(v, str):
                    continue
                if k in DATA_ATTR_HINTS or k.startswith("data-"):
                    add_img_url(v, f"img.{k}")

    # 2) noscript 내 <img> (lazy-load 대체)
    if include_noscript:
        for n in nodes:
            for nos in n.find_all("noscript"):
                inner = BeautifulSoup(nos.get_text() or "", "lxml")
                for img in inner.find_all("img"):
                    add_img_url(img.get("src"), "noscript.img.src")
                    for k in ("data-src", "data-original", "data-lazy", "data-lazy-src"):
                        add_img_url(img.get(k), f"noscript.img.{k}")
                    sset = img.get("srcset")
                    if sset:
                        for p in [p.strip() for p in sset.split(",") if p.strip()]:
                            add_img_url(p.split()[0], "noscript.img.srcset")

    # 3) (옵션) video poster
    if include_video_poster:
        for v in dsoup.find_all("video"):
            add_img_url(v.get("poster"), "video.poster")

    # 4) (옵션) 메타 폴백
    if include_meta:
        for m in dsoup.select('meta[property="og:image"], meta[name="twitter:image"]'):
            add_img_url(m.get("content"), "meta.og_or_twitter")
        for l in dsoup.select('link[rel="image_src"]'):
            add_img_url(l.get("href"), "link.image_src")

    # 5) (옵션) <script> 내 이미지 URL 폴백
    if include_script:
        IMG_RE = re.compile(r'https?://[^\s\'"]+\.(?:jpg|jpeg|png|webp|gif|avif)\b', re.I)
        for sc in dsoup.find_all("script"):
            txt = sc.string or sc.get_text() or ""
            for m in IMG_RE.finditer(txt):
                add_img_url(m.group(0), "script.url")

    # 6) (옵션) JAVMOST 포스터 추정: https://img{1..5}.javmost.com/images/<CODE>.webp
    if poster_guess:
        pr = up.urlparse(view_url)
        host = pr.netloc.lower()
        if host.endswith("javmost.com"):
            slug = pathlib.Path(pr.path).parts[-1].strip("/") or ""
            mcode = re.search(r"([A-Za-z]+-?\d+)", slug)
            if mcode:
                code = mcode.group(1).upper().replace("--", "-")
                for n in ("3", "2", "1", "4", "5"):  # 관측상 3이 가장 흔해 우선
                    cand = f"https://img{n}.javmost.com/images/{code}.webp"
                    add_img_url(cand, f"poster.guess.img{n}")

    # 7) 마크다운 IMG 링크 처리 (페이지가 마크다운 원문인 경우)
    for m in MD_IMG_LINK_RE.finditer(html or ""):
        img_url = m.group("img")
        if img_url:
            add_img_url(img_url, "md.img")

    # 중복 제거
    seen, cands = set(), []
    for it in raw:
        u = (it["url"] or "").strip()
        if u and u not in seen:
            cands.append(it); seen.add(u)

    debug["candidate_count"] = len(cands)
    debug["candidates_sample"] = cands[:20]

    accepted, rejected = [], []

    # 저장 파일명 prefix (코드/슬러그 기반)
    slug = pathlib.Path(up.urlparse(view_url).path).parts[-1].strip("/") or "view"
    view_id = re.search(r"([A-Za-z]+-?\d+)", slug)
    base_prefix = view_id.group(1).upper() if view_id else "view"

    for i, item in enumerate(cands, 1):
        u, how = item["url"], item["how"]

        # a) 자산/광고 제외
        if is_probably_asset(u):
            rejected.append({"url": u, "reason": "asset_or_ad", "how": how})
            continue

        # b) .html 등 이미지가 아니면 제외 (안전장치)
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
                time.sleep(0.08)
            except Exception as e:
                rejected.append({"url": u, "reason": f"download_error:{e}", "how": how})
        else:
            accepted.append({"url": u, "ct": probe["ct"], "size": probe["size"], "how": how})

    debug["accepted"] = accepted
    debug["rejected"] = rejected
    debug["returned_count"] = len(results)
    save_debug_json(out_dir, f"view_{base_prefix}", debug)
    return results

# -------------------- 엔트리: 키워드로 상세 찾아가서 '보이는 이미지' URL만 --------------------
def scrape_javmost_image_urls_by_keyword(
    keyword: str,
    out_dir: str = "downloads",
    download: bool = False,
    **extract_opts,                  # 하위 추출 옵션 전달(include_meta, poster_guess 등)
) -> list[str]:
    """
    1) 키워드를 코드로 정규화 후 상세 페이지 직행 시도
    2) 실패 시 코드 접두 태그 페이지에서 '첫 번째' 엄격 매칭 결과 선택
    3) 상세(view) 페이지에서 '보이는 이미지'만 추출(+선택 저장)
    """
    debug = {"keyword": keyword, "base": BASE, "mode": "by_keyword_visible_imgs", "min_bytes": MIN_BYTES}
    with requests.Session() as s:
        # 1~2) 상세 URL/제목 해결
        view_url, title_text, steps = resolve_view_url_and_title(keyword, s)
        debug.update({"view_url": view_url, "title_text": title_text, "steps": steps})

        if not view_url or not title_text:
            debug["error"] = "no_result_view_link"
            save_debug_json(out_dir, f"keyword_{keyword}", debug)
            return []

        # 3) 상세 페이지에서 '보이는 이미지'만 추출
        urls = extract_visible_image_urls_from_view(
            view_url,
            out_dir=out_dir,
            download=download,
            session=s,
            referer=BASE,
            **extract_opts
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
    urls = scrape_javmost_image_urls_by_keyword(
        "JUR-539",            # 예시 키워드
        out_dir="test_images",
        download=False,       # URL만(저장 X)
        include_meta=True,    # 메타 폴백
        poster_guess=True     # 포스터 추정 (img*.javmost.com/images/<CODE>.webp)
        # include_script=True  # 필요시 스크립트 내 URL까지 폴백
    )
    print("URLS ON PAGE (IMG ONLY):", *urls, sep="\n")
