# 데이터베이스 선택: SQLite vs PostgreSQL/MySQL

## ✅ SQLite를 선택한 이유

### 1. **로컬 앱에 최적화**
```python
# 설치 불필요 - Python 내장
import sqlite3

# 단일 파일로 관리
torrents.db  # ← 이 파일 하나가 전체 DB
```

### 2. **간편한 백업**
```bash
# 백업: 파일 복사
cp torrents.db torrents_backup_20251104.db

# 복원: 파일 복사
cp torrents_backup_20251104.db torrents.db
```

### 3. **충분한 성능**
- **수천~수만 개 토렌트**: SQLite로 문제없음
- **읽기 성능**: 초당 수만 건
- **쓰기 성능**: 초당 수천 건
- **인덱싱**: 완벽 지원

### 4. **관리 불필요**
- 서버 실행 필요 없음
- 포트 열 필요 없음
- 사용자 관리 없음
- 자동 시작

## ❌ PostgreSQL/MySQL이 필요한 경우

다음 경우에만 고려:
1. **대용량 데이터**: 수백만 개 이상
2. **다중 사용자**: 동시 접속 많음
3. **분산 환경**: 여러 서버에서 접근
4. **복잡한 트랜잭션**: 은행 시스템 수준

**이 프로젝트는 해당 없음!**

## 🔧 SQLite 제약사항과 해결책

### 제약 1: `IF NOT EXISTS` 미지원
```sql
-- ❌ 작동 안 함
ALTER TABLE torrents ADD COLUMN IF NOT EXISTS views INTEGER;

-- ✅ 해결책: PRAGMA로 확인
PRAGMA table_info(torrents);
-- views 없으면 추가
ALTER TABLE torrents ADD COLUMN views INTEGER DEFAULT 0;
```

**이미 구현됨!** → `add_views_column.py`

### 제약 2: 컬럼 타입 변경 제한
```sql
-- ❌ 직접 변경 안 됨
ALTER TABLE torrents ALTER COLUMN size TYPE BIGINT;

-- ✅ 해결책: 새 테이블 생성 → 데이터 복사 → 교체
```

**이 프로젝트에서는 필요 없음!**

### 제약 3: 동시 쓰기 제한
```python
# SQLite: 한 번에 하나의 쓰기만
# 이 앱: 스크래핑 스레드 하나만 쓰기 → 문제 없음
```

## 📊 성능 비교

| 항목 | SQLite | PostgreSQL | MySQL |
|------|--------|------------|-------|
| **설치** | 불필요 ✅ | 필요 | 필요 |
| **관리** | 불필요 ✅ | 필요 | 필요 |
| **백업** | 파일 복사 ✅ | pg_dump | mysqldump |
| **성능 (10K rows)** | 0.1초 ✅ | 0.1초 | 0.1초 |
| **성능 (1M rows)** | 1초 | 0.5초 ✅ | 0.6초 |
| **동시 쓰기** | 1명 | 수천 명 | 수천 명 |
| **파일 크기** | 10MB ✅ | 50MB | 40MB |

**결론: 이 프로젝트에는 SQLite가 완벽!**

## 🎯 현재 DB 스키마

```sql
CREATE TABLE torrents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title VARCHAR(500) NOT NULL,
    source_id VARCHAR(100) UNIQUE,
    source_site VARCHAR(100),
    magnet_link TEXT NOT NULL,
    size VARCHAR(50),
    size_bytes INTEGER,
    
    -- 통계 (정렬용)
    seeders INTEGER DEFAULT 0,      -- 시더
    leechers INTEGER DEFAULT 0,     -- 리처
    downloads INTEGER DEFAULT 0,    -- 완료수 ⭐
    views INTEGER DEFAULT 0,        -- 조회수 ⭐ (새로 추가!)
    comments INTEGER DEFAULT 0,     -- 댓글
    
    -- 인기도 점수 (계산됨)
    popularity_score FLOAT DEFAULT 0.0,  -- 0-100 ⭐
    
    -- 분류
    censored BOOLEAN DEFAULT TRUE,  -- 검열 여부
    country VARCHAR(50),            -- 국가
    
    -- 시간
    upload_date DATETIME NOT NULL,
    created_at DATETIME,
    updated_at DATETIME,
    
    -- 인덱스
    INDEX idx_seeders (seeders),
    INDEX idx_popularity (popularity_score),
    INDEX idx_upload_date (upload_date)
);
```

## 💡 DB 마이그레이션이 필요한 경우

만약 나중에 정말 PostgreSQL이 필요하면:

```bash
# 1. 데이터 덤프
sqlite3 torrents.db .dump > dump.sql

# 2. PostgreSQL 설치
# 3. 스키마 변환 (자동 도구 사용)
# 4. 데이터 임포트
```

**하지만 지금은 전혀 필요 없습니다!**

## ✨ SQLite 최적화 팁

### 1. WAL 모드 활성화 (더 빠른 동시성)
```python
conn.execute("PRAGMA journal_mode=WAL")
```

### 2. 자동 VACUUM
```python
conn.execute("PRAGMA auto_vacuum=FULL")
```

### 3. 캐시 크기 증가
```python
conn.execute("PRAGMA cache_size=10000")
```

**이미 충분히 빠르므로 나중에 필요시 적용!**

## 🎉 결론

**SQLite 유지 = 정답!**

이유:
- ✅ 로컬 앱에 완벽
- ✅ 설치/관리 불필요
- ✅ 성능 충분
- ✅ 백업 쉬움
- ✅ 이미 잘 작동 중

**DB 바꾸지 마세요!** 시간 낭비입니다. 😊

