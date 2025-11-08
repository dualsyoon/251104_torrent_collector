# -*- coding: utf-8 -*-
"""
JAVBEE 검색 결과 페이지 → '첫 번째 결과 카드'의 중앙 이미지 추출/다운로드

요구사항 반영:
- 검색 결과가 여러 개여도 "첫 번째 카드"만 처리(result_card_limit=1 고정).
- 반드시 그 카드의 "제목 텍스트(오른쪽 파란 제목)"가 키워드와 엄격 매칭일 때만 진행.
  (문자+숫자가 정확히 같고, 문자/숫자 사이의 하이픈만 옵션. 예: STARS-080 ↔ STARS080 허용)
- 이미지 URL에 코드가 없어도 OK(제목으로 검증). 단, 자산/아이콘은 제외하고 image/* 만 받음.
- 후보는 카드 주변(특히 좌측 큰 썸네일 영역)의 <img>에서 수집: src / data-src / data-original / srcset.
- 파일명에 코드가 없으면 저장 시 강제로 `키워드_XX.ext` 로 이름 부여.
- 디버그 JSON을 out_dir/debug_<keyword>_<ts>.json으로 저장(선택 카드의 제목/매칭 여부 등 포함).
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

BASE_SEARCH = "https://javbee.vip/search"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Python-requests"
MIN_BYTES = 10 * 1024  # 10KB (Content-Length 있으면 최소 용량, 없으면 무시)
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}

# uploads/upload 경로 선호(필수는 아님; 자산 오탐 방지용)
UPLOADS_RE = re.compile(r"/(?:wp-content/)?uploads?/", re.I)

# 자산(logo/favicon/icon/banner/ads 등) 세그먼트 기준 필터
ASSET_SEG_RE = re.compile(
    r"(?:^|/)(?:logo|favicon|sprite|icons?|ads?|banners?|static|assets|themes|emoji|svg)(?:/|$)",
    re.I
)

def is_probably_asset(u: str) -> bool:
    path = up.urlparse(u).path
    return bool(ASSET_SEG_RE.search(path))  # 세그먼트 기준 → uploads는 통과


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

def get_search_html(keyword: str, session: requests.Session):
    url = f"{BASE_SEARCH}?{up.urlencode({'keyword': keyword})}"
    r = session.get(url, headers={"User-Agent": UA, "Referer": "https://javbee.vip/"}, timeout=25)
    r.raise_for_status()
    return r.text, url

def find_show_screenshot_anchors(soup: BeautifulSoup):
    anchors = []
    for t in soup.find_all(["a", "button"]):
        txt = t.get_text(" ", strip=True) or ""
        if re.search(r"\bshow\s*screens?hot\b", txt, re.I):
            anchors.append(t)
    # 중복 제거(문서 순서 유지)
    seen, uniq = set(), []
    for a in anchors:
        k = str(a)
        if k not in seen:
            uniq.append(a); seen.add(k)
    return uniq

# ---------- 카드 타이틀(제목) 찾기 ----------
IGNORE_TITLE_WORDS_RE = re.compile(r"(show\s*screenshot|torrent|magnet)", re.I)

def find_card_title_text(anchor, max_up=6) -> str | None:
    """
    'Show Screenshot' 앵커를 기준으로, 같은 카드의 제목 텍스트를 찾아 반환.
    - 우선: 앵커의 이전 형제들 중 텍스트가 긴 <a>/<h1..h6>.
    - 대안: 부모로 올라가며 그 안에서 후보 찾기(최대 max_up 단계).
    """
    # 1) 이전 형제 스캔
    for sib in anchor.previous_siblings:
        name = getattr(sib, "name", None)
        if name in ("a", "h1", "h2", "h3", "h4", "h5", "h6"):
            txt = sib.get_text(" ", strip=True) or ""
            if txt and not IGNORE_TITLE_WORDS_RE.search(txt) and len(txt) >= 6:
                return txt
        if getattr(sib, "find_all", None):
            for cand in sib.find_all(["a", "h1", "h2", "h3", "h4", "h5", "h6"]):
                txt = cand.get_text(" ", strip=True) or ""
                if txt and not IGNORE_TITLE_WORDS_RE.search(txt) and len(txt) >= 6:
                    return txt
    # 2) 부모로 상승하며 내부에서 찾기
    node = anchor
    for _ in range(max_up):
        node = getattr(node, "parent", None)
        if node is None: break
        for cand in node.find_all(["a", "h1", "h2", "h3", "h4", "h5", "h6"]):
            txt = cand.get_text(" ", strip=True) or ""
            if txt and not IGNORE_TITLE_WORDS_RE.search(txt) and len(txt) >= 6:
                return txt
    return None

# ---------- 이미지 후보 수집 ----------
def images_in_previous_siblings(anchor):
    imgs = []
    for sib in anchor.previous_siblings:
        if getattr(sib, "find_all", None):
            imgs.extend(sib.find_all("img"))
        elif getattr(sib, "name", None) == "img":
            imgs.append(sib)
    return imgs

def closest_container_with_images(anchor):
    node = anchor
    for _ in range(6):
        if node is None: break
        if getattr(node, "find_all", None):
            imgs = node.find_all("img")
            if imgs: return node, imgs
        node = getattr(node, "parent", None)
    return None, []

def img_candidate_urls(img_tag, base_url: str):
    cands = []
    def add(u, how):
        if u: cands.append({"url": urljoin(base_url, u), "how": how})
    add(img_tag.get("src"), "src")
    add(img_tag.get("data-src"), "data-src")
    add(img_tag.get("data-original"), "data-original")
    srcset = img_tag.get("srcset")
    if srcset:
        parts = [p.strip() for p in srcset.split(",") if p.strip()]
        for p in reversed(parts):           # 큰 해상도부터
            add(p.split()[0], "srcset")
    # dedupe
    seen, uniq = set(), []
    for it in cands:
        if it["url"] not in seen:
            uniq.append(it); seen.add(it["url"])
    return uniq

# ---------- 네트워크 프로빙/다운로드 ----------
def head_or_small_get(url: str, session: requests.Session, referer: str):
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

def ext_from_content_type(ct: str) -> str:
    ct = (ct or "").lower()
    if "jpeg" in ct: return ".jpg"
    if "png"  in ct: return ".png"
    if "webp" in ct: return ".webp"
    if "gif"  in ct: return ".gif"
    if "bmp"  in ct: return ".bmp"
    if "avif" in ct: return ".avif"
    return ".jpg"

# ---------- 핵심 파이프라인 ----------
def extract_center_image_urls_and_title(html: str, base_url: str, keyword: str, debug: dict):
    soup = BeautifulSoup(html, "lxml")
    anchors = find_show_screenshot_anchors(soup)
    debug["anchors_found"] = len(anchors)

    if not anchors:
        debug["error"] = "no_show_screenshot_anchor"
        return [], None

    # "첫 번째 카드"만 사용
    anchor = anchors[0]
    debug["used_card_index"] = 0

    # 카드 제목 추출 & 키워드 엄격 매칭
    title_text = find_card_title_text(anchor)
    debug["card_title_text"] = title_text

    kw_re = compile_keyword_strict(keyword)
    title_ok = bool(title_text and kw_re.search(title_text))
    debug["card_title_match"] = title_ok
    if not title_ok:
        # 요구사항: 제목이 키워드와 같아야만 진행
        return [], title_text

    # 카드의 좌측 이미지 영역에서 후보 수집
    all_img_tags = []
    all_img_tags.extend(images_in_previous_siblings(anchor))
    _, imgs = closest_container_with_images(anchor)
    all_img_tags.extend(imgs)

    cand_urls = []
    for img in all_img_tags:
        cand_urls.extend(img_candidate_urls(img, base_url))

    # 중복 제거
    uniq_urls, seen = [], set()
    for c in cand_urls:
        if c["url"] not in seen:
            uniq_urls.append(c); seen.add(c["url"])

    debug["raw_candidates"] = uniq_urls[:50]
    return uniq_urls, title_text

def filter_and_download(url_items, out_dir, session, referer, keyword, title_text, debug):
    os.makedirs(out_dir, exist_ok=True)
    accepted, rejected = [], []

    kw_re = compile_keyword_strict(keyword)

    for i, item in enumerate(url_items, 1):
        u, how = item["url"], item["how"]

        # 자산/아이콘 제외 (세그먼트 기준)
        if is_probably_asset(u):
            rejected.append({"url": u, "reason": "asset_segment", "how": how})
            continue

        # uploads 계열 선호: uploads가 아니더라도 image/* + 충분한 크기면 허용
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
        ext = pathlib.Path(name).suffix.lower()

        # 파일명에 키워드가 없을 수 있다 → 강제로 이름을 키워드 기반으로 부여
        if not ext or ext not in IMG_EXTS:
            ext = ext_from_content_type(probe["ct"])
        # 파일명에서 키워드 엄격매칭? 없어도 OK(제목으로 이미 검증됨)
        dest_name = name if (name and kw_re.search(name)) else f"{keyword}_{i:02d}{ext}"
        dest = os.path.join(out_dir, dest_name)

        try:
            with session.get(final_url, headers={"User-Agent": UA, "Referer": referer}, stream=True, timeout=40) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk: f.write(chunk)
            accepted.append({
                "url": final_url, "saved": dest, "size": probe["size"], "ct": probe["ct"],
                "how": how
            })
            print(f"saved: {dest}")
            time.sleep(0.2)
        except Exception as e:
            rejected.append({"url": final_url, "reason": f"download_error:{e}", "how": how})

    debug["accepted"] = accepted
    debug["rejected"] = rejected
    debug["downloaded_count"] = len(accepted)
    debug["card_title_text"] = title_text
    return accepted

def save_debug_json(out_dir: str, keyword: str, debug: dict):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"debug_{keyword}_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(debug, f, ensure_ascii=False, indent=2)
    print(f"[debug] log saved -> {path}")

def scrape_center_images(keyword: str, out_dir: str = "downloads"):
    debug = {"keyword": keyword, "min_bytes": MIN_BYTES, "base": BASE_SEARCH}
    with requests.Session() as s:
        html, search_url = get_search_html(keyword, s)
        debug["search_url"] = search_url
        debug["html_len"] = len(html)

        url_items, title_text = extract_center_image_urls_and_title(html, search_url, keyword, debug)
        debug["candidate_count"] = len(url_items)
        debug["card_title_text"] = title_text

        if not url_items:
            # 제목 불일치 또는 후보 없음
            save_debug_json(out_dir, keyword, debug)
            print("no images: either no 'Show Screenshot' or title mismatch with keyword")
            return

        filter_and_download(url_items, out_dir, s, search_url, keyword, title_text, debug)

    save_debug_json(out_dir, keyword, debug)

# -------------------------
# 실행 예시
# -------------------------
if __name__ == "__main__":
    # 예: STARS-080 → 첫 번째 카드 제목에 STARS-080(또는 STARS080) 이 포함될 때만 저장
    scrape_center_images("SDMU-963", out_dir="test_images")

    # 다른 키워드
    scrape_center_images("ZSD-74", out_dir="test_images")
    # 다른 키워드
    scrape_center_images("asdf", out_dir="test_images")
