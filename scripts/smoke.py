"""
스모크 테스트(동작 보존 가드레일)

- GUI를 실제로 띄우지 않고, 최소한의 import/컴파일/DB 초기화를 확인합니다.
- 네트워크/VPN/브라우저 환경에 의존하지 않도록 DB는 ':memory:'를 사용합니다.
"""

from __future__ import annotations

import compileall
import os
import sys


def main() -> int:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    os.chdir(repo_root)
    # `scripts/` 아래에서 실행되므로, 리포 루트를 명시적으로 sys.path에 추가
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    # 1) 핵심 모듈 import 확인
    import database  # noqa: F401
    import scrapers  # noqa: F401
    import gui  # noqa: F401

    # 2) DB 모델/마이그레이션 경로 확인 (파일 DB를 건드리지 않기 위해 in-memory 사용)
    from database import Database

    _db = Database(":memory:")

    # 3) GUI 엔트리포인트 import만 확인 (인스턴스 생성은 하지 않음: 스레드/트레이 생성 방지)
    from gui.main_window import MainWindow  # noqa: F401

    # 4) 전체 파이썬 파일 컴파일
    ok = compileall.compile_dir(repo_root, quiet=1)
    if not ok:
        print("[SMOKE] compileall 실패")
        return 2

    print("[SMOKE] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


