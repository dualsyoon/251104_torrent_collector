"""GUI 백그라운드 워커(QThread) 모음

`gui/main_window.py`에 있던 워커 클래스를 분리하여 파일 비대/결합도를 낮춥니다.
동작은 동일해야 하므로, 클래스 구현은 그대로 이동합니다.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from database import Database
from scrapers import ScraperManager


class SingleThumbnailReplaceThread(QThread):
    """단일 항목 썸네일 교체 스레드"""

    updated = Signal(int, str)  # (torrent_id, new_thumbnail_url)
    error = Signal(str)

    def __init__(self, db: Database, torrent_id: int, image_finder=None, db_writer=None):
        super().__init__()
        self.db = db
        self.torrent_id = torrent_id
        self.image_finder = image_finder  # 재사용할 ImageFinder
        self.db_writer = db_writer  # DB Writer Thread

    def run(self):
        try:
            from urllib.parse import urlparse

            session = self.db.get_session()
            try:
                from database.models import Torrent

                try:
                    # SQLAlchemy 1.4/2.0 호환 조회
                    t = session.get(Torrent, self.torrent_id)
                except Exception:
                    t = session.query(Torrent).get(self.torrent_id)
                if not t:
                    self.error.emit("항목을 찾을 수 없습니다.")
                    return
                title = t.title or ""
                current_url = (t.thumbnail_url or "").strip()
                exclude_hosts = []
                if current_url:
                    try:
                        exclude_hosts.append(urlparse(current_url).netloc.lower())
                    except Exception:
                        pass

                # DB에서 이미 탐색한 서버 목록 확인
                import json

                searched_servers = []
                if t.thumbnail_searched_servers:
                    try:
                        searched_servers = json.loads(t.thumbnail_searched_servers)
                    except (json.JSONDecodeError, TypeError):
                        searched_servers = []

                # 탐색하지 않은 서버만 검색 (교체 시 우선 탐색)
                exclude_servers = searched_servers.copy()  # 이미 탐색한 서버는 제외

                # ImageFinder 재사용 (없으면 새로 생성)
                if self.image_finder is None:
                    from scrapers.image_finder import ImageFinder

                    self.image_finder = ImageFinder()

                result = self.image_finder.search_images(
                    title,
                    max_images=5,
                    exclude_hosts=exclude_hosts or None,
                    exclude_servers=exclude_servers if exclude_servers else None,
                )
                new_url = (result.get("thumbnail") or "").strip()
                if new_url and new_url != current_url:
                    # DB 저장 (DB_writer 사용)
                    if self.db_writer:
                        # DB_writer를 통해 비동기 저장
                        self.db_writer.update_thumbnail(self.torrent_id, new_url)
                        session.close()
                        self.updated.emit(self.torrent_id, new_url)
                    else:
                        # DB_writer가 없으면 직접 저장 (기존 방식)
                        t.thumbnail_url = new_url

                        # DB 저장 재시도 (lock 방지)
                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                session.commit()
                                self.updated.emit(self.torrent_id, new_url)
                                break
                            except Exception as commit_error:
                                if attempt < max_retries - 1:
                                    # 재시도
                                    import time

                                    time.sleep(0.5)
                                    session.rollback()
                                    # 다시 조회
                                    try:
                                        t = session.get(Torrent, self.torrent_id)
                                    except Exception:
                                        t = session.query(Torrent).get(self.torrent_id)
                                    if t:
                                        t.thumbnail_url = new_url
                                else:
                                    # 최종 실패
                                    raise commit_error
                else:
                    self.error.emit("대체 가능한 썸네일을 찾지 못했습니다.")
            finally:
                session.close()
        except Exception as e:
            self.error.emit(str(e))


class ScraperThread(QThread):
    """스크래핑 작업을 위한 스레드"""

    progress = Signal(int, str)  # (진행률, 메시지)
    finished = Signal(int, int, bool)  # (새로 추가된 수, 업데이트된 수, 중단 여부)
    error = Signal(str)

    def __init__(
        self,
        db: Database,
        scraper_manager: ScraperManager,
        source_key: str,
        pages: int = 5,
        enable_thumbnail: bool = False,
        query: str = None,
        db_writer=None,
    ):
        super().__init__()
        self.db = db
        self.scraper_manager = scraper_manager
        self.source_key = source_key
        self.pages = pages
        self.enable_thumbnail = enable_thumbnail
        self.query = query  # 검색어
        self.db_writer = db_writer  # DB Writer Thread (비동기 저장용)
        self.db_writer_stats = {"added": 0, "updated": 0, "duplicate": 0}  # 통계 추적
        self._stop_requested = False

        # db_writer가 있으면 배치 완료 시그널 연결
        if self.db_writer:
            self.db_writer.batch_completed.connect(self._on_db_batch_completed)

    def stop(self):
        """스크래핑 중단 요청"""
        self._stop_requested = True

    def _on_db_batch_completed(self, stats: dict):
        """DB 배치 저장 완료 시그널 처리"""
        added = stats.get("added", 0)
        updated = stats.get("updated", 0)
        duplicate = stats.get("duplicate", 0)

        self.db_writer_stats["added"] += added
        self.db_writer_stats["updated"] += updated
        self.db_writer_stats["duplicate"] += duplicate

        # 디버그: 시그널이 제대로 전달되는지 확인

    def run(self):
        """스크래핑 실행"""
        try:
            self._stop_requested = False
            # 통계 초기화
            self.db_writer_stats = {"added": 0, "updated": 0, "duplicate": 0}
            total_added = 0
            total_updated = 0

            # 썸네일 검색 비활성화 - 백그라운드에서 별도 처리

            # 모든 소스에서 수집
            if self.source_key == "all":
                sources = self.scraper_manager.get_available_sources()
                enabled_sources = [(k, v) for k, v in sources.items() if v["enabled"]]
                num_sources = len(enabled_sources)

                for source_idx, (key, source_info) in enumerate(enabled_sources):
                    if self._stop_requested:
                        print("[스크래핑] 사용자에 의해 중단됨")
                        break

                    # 진행률 콜백: 전체 소스와 페이지를 고려 (closure 문제 해결)
                    def make_progress_cb(idx, info, total):
                        def progress_cb(page, max_pages, message):
                            # 소스별 진행률 + 페이지 진행률
                            source_progress = (idx / total) * 100
                            page_progress = (page / max_pages) * (100 / total)
                            total_progress = int(source_progress + page_progress)
                            self.progress.emit(total_progress, f"[{info['name']}] {message}")

                        return progress_cb

                    # 스마트 스크래핑 사용 (중복 최소화, db_writer로 실시간 저장)
                    torrents = self.scraper_manager.scrape_source_smart(
                        key,
                        self.db,
                        max_pages=self.pages,
                        stop_on_duplicate=True,
                        stop_callback=lambda: self._stop_requested,
                        progress_callback=make_progress_cb(source_idx, source_info, num_sources),
                        db_writer=self.db_writer,
                    )

                    # db_writer를 사용하면 이미 실시간으로 저장되었으므로 추가 저장 불필요
                    # 큐에 남은 작업이 완료될 때까지 대기
                    if self.db_writer:
                        self.db_writer.queue.join()
                    print(f"[스크래핑] [{source_info['name']}] 스크래핑 완료: {len(torrents)}개 수집됨 (DB 저장 완료)")

                    # 정지 요청 시 루프 중단
                    if self._stop_requested:
                        break

                # 모든 소스 처리 완료 후 최종 통계
                total_added = self.db_writer_stats.get("added", 0)
                total_updated = self.db_writer_stats.get("updated", 0)
                print(f"[스크래핑] 전체 통계: 신규 {total_added}개, 업데이트 {total_updated}개")

            # 특정 소스에서만 수집
            else:
                # 진행률 콜백: 페이지 기준으로 진행률 표시
                def progress_cb(page, max_pages, message):
                    progress = int((page / max_pages) * 100)
                    self.progress.emit(progress, message)

                # 스마트 스크래핑 사용 (db_writer로 실시간 비동기 저장)
                torrents = self.scraper_manager.scrape_source_smart(
                    self.source_key,
                    self.db,
                    max_pages=self.pages,
                    query=self.query,
                    stop_on_duplicate=True,
                    stop_callback=lambda: self._stop_requested,
                    progress_callback=progress_cb,
                    db_writer=self.db_writer,
                )

                # db_writer를 사용하면 이미 실시간으로 저장되었으므로 추가 저장 불필요
                # 통계 초기화
                self.db_writer_stats = {"added": 0, "updated": 0, "duplicate": 0}

                # 큐에 남은 작업이 완료될 때까지 대기
                if self.db_writer:
                    print(f"[스크래핑] DB 저장 큐 완료 대기 중... (큐 크기: {self.db_writer.queue.qsize()})")
                    self.db_writer.queue.join()
                    print(
                        f"[스크래핑] DB 저장 완료 (최종 통계: 추가={self.db_writer_stats.get('added', 0)}, 업데이트={self.db_writer_stats.get('updated', 0)})"
                    )

                # 통계 사용 (시그널로 받은 통계 누적값)
                total_added = self.db_writer_stats.get("added", 0)
                total_updated = self.db_writer_stats.get("updated", 0)
                total_duplicate = self.db_writer_stats.get("duplicate", 0)

                # 통계가 0이면 경고 출력
                if total_added == 0 and total_updated == 0 and len(torrents) > 0:
                    print(
                        f"[스크래핑] ⚠️ 경고: {len(torrents)}개 수집했지만 DB 통계가 0입니다. 시그널이 제대로 전달되지 않았을 수 있습니다."
                    )

                print(
                    f"[스크래핑] 스크래핑 완료: {len(torrents)}개 수집됨 (DB 저장: 신규 {total_added}개, 업데이트 {total_updated}개, 중복 {total_duplicate}개)"
                )

            # 정지 여부와 관계없이 완료 시그널 발생 (지금까지 수집한 데이터 저장 완료)
            was_stopped = self._stop_requested
            self.finished.emit(total_added, total_updated, was_stopped)

        except Exception as e:
            self.error.emit(str(e))


__all__ = ["ScraperThread", "SingleThumbnailReplaceThread"]


