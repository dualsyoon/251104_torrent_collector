"""데이터베이스 연결 및 세션 관리"""
import os
from datetime import datetime, timedelta
from typing import List, Optional
import requests
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, desc, and_
from sqlalchemy.orm import sessionmaker, Session
from .models import Base, Torrent, Genre, Country


class Database:
    """데이터베이스 관리 클래스"""
    
    def __init__(self, db_path: str = "./torrents.db"):
        """데이터베이스 초기화
        
        Args:
            db_path: 데이터베이스 파일 경로 (기본값: 현재 디렉토리)
        """
        import os
        
        self.db_path = db_path
        
        # 데이터베이스 파일 존재 여부 확인
        db_exists = os.path.exists(db_path)
        
        if not db_exists:
            print(f"[DB] 데이터베이스 파일이 없습니다. 새로 생성합니다: {db_path}")
        else:
            print(f"[DB] 기존 데이터베이스 로드: {db_path}")
        
        # 엔진 생성 (DB lock 방지를 위한 timeout 설정)
        self.engine = create_engine(
            f'sqlite:///{db_path}',
            echo=False,
            connect_args={
                'check_same_thread': False,  # 멀티스레드 지원
                'timeout': 30  # DB lock 대기 시간 30초
            }
        )
        self.SessionLocal = sessionmaker(bind=self.engine)
        
        # 테이블 생성 (없으면)
        Base.metadata.create_all(self.engine)
        
        if not db_exists:
            print("[DB] 테이블 생성 완료")
        
        # 초기 데이터 추가 (장르, 국가)
        self._initialize_data()
        
        if not db_exists:
            print("[DB] 데이터베이스 초기화 완료!")
    
    def get_session(self) -> Session:
        """새로운 데이터베이스 세션 반환"""
        return self.SessionLocal()
    
    def _initialize_data(self):
        """초기 장르 및 국가 데이터 추가"""
        session = self.get_session()
        
        try:
            # 장르 초기화 (이미 있으면 스킵)
            if session.query(Genre).count() == 0:
                genres_data = [
                    ('Blowjob', 'BJ/펠라치오'),
                    ('Handjob', '핸드잡'),
                    ('Threesome', '쓰리썸'),
                    ('Creampie', '크림파이'),
                    ('Anal', '항문'),
                    ('BDSM', 'BDSM'),
                    ('Bondage', '속박'),
                    ('Cosplay', '코스프레'),
                    ('Schoolgirl', '여학생'),
                    ('MILF', '숙녀'),
                    ('Amateur', '아마추어'),
                    ('POV', 'POV'),
                    ('Gangbang', '갱뱅'),
                    ('Lesbian', '레즈비언'),
                    ('Solo', '솔로'),
                    ('Masturbation', '자위'),
                    ('Toy', '도구'),
                    ('Squirting', '분수'),
                    ('Bukkake', '부카케'),
                    ('Outdoor', '야외'),
                    ('Massage', '마사지'),
                    ('Office', '오피스'),
                ]
                
                for name, name_kr in genres_data:
                    genre = Genre(name=name, name_kr=name_kr)
                    session.add(genre)
            
            # 국가 초기화 (이미 있으면 스킵)
            if session.query(Country).count() == 0:
                countries_data = [
                    ('JP', 'Japan', '일본'),
                    ('CN', 'China', '중국'),
                    ('KR', 'Korea', '한국'),
                    ('US', 'United States', '미국'),
                    ('EU', 'Europe', '유럽'),
                    ('TH', 'Thailand', '태국'),
                    ('TW', 'Taiwan', '대만'),
                    ('OTHER', 'Other', '기타'),
                ]
                
                for code, name, name_kr in countries_data:
                    country = Country(code=code, name=name, name_kr=name_kr)
                    session.add(country)
            
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"초기 데이터 추가 실패: {e}")
        finally:
            session.close()
    
    def add_torrent(self, session: Session, torrent_data: dict) -> Optional[Torrent]:
        """토렌트 추가 또는 업데이트
        
        Args:
            session: 데이터베이스 세션
            torrent_data: 토렌트 데이터 딕셔너리
            
        Returns:
            추가/업데이트된 Torrent 객체
        """
        try:
            # 중복 확인: source_id 또는 제목으로 확인
            existing = None
            source_id = torrent_data.get('source_id')
            source_site = torrent_data.get('source_site')
            title = torrent_data.get('title')
            
            # 1순위: source_id로 확인
            if source_id and source_site:
                existing = session.query(Torrent).filter_by(
                    source_id=source_id,
                    source_site=source_site
                ).first()
            
            # 2순위: 제목으로 확인 (source_id가 없거나 못 찾은 경우)
            if not existing and title:
                existing = session.query(Torrent).filter_by(
                    title=title
                ).first()
            
            if existing:
                # 이미 존재하면 업데이트만 (시더/리처 등 최신 정보)
                # 통계 정보만 업데이트
                existing.seeders = torrent_data.get('seeders', existing.seeders)
                existing.leechers = torrent_data.get('leechers', existing.leechers)
                existing.downloads = torrent_data.get('downloads', existing.downloads)
                existing.comments = torrent_data.get('comments', existing.comments)
                
                # 썸네일이 없었는데 새로 생겼으면 업데이트
                if not existing.thumbnail_url and torrent_data.get('thumbnail_url'):
                    existing.thumbnail_url = torrent_data.get('thumbnail_url')
                    existing.snapshot_urls = torrent_data.get('snapshot_urls', '')
                
                torrent = existing
            else:
                # 새로 추가
                genres_data = torrent_data.pop('genres', [])
                torrent = Torrent(**torrent_data)
                session.add(torrent)
            
            # 인기도 점수 계산
            torrent.calculate_popularity()
            
            session.commit()
            return torrent
        except Exception as e:
            session.rollback()
            # 오류만 출력 (개별 항목 출력 제거)
            return None
    
    def get_torrents(
        self,
        session: Session,
        period_days: Optional[int] = None,
        censored: Optional[bool] = None,
        country: Optional[str] = None,
        genres: Optional[List[str]] = None,
        search_query: Optional[str] = None,
        sort_by: str = 'popularity_score',
        sort_order: str = 'desc',
        limit: int = 100,
        offset: int = 0
    ) -> List[Torrent]:
        """토렌트 목록 조회 (필터링 및 정렬)
        
        Args:
            session: 데이터베이스 세션
            period_days: 기간 (1, 7, 30, None=전체)
            censored: 검열 여부 (True, False, None=전체)
            country: 국가 코드
            genres: 장르 목록
            search_query: 검색어
            limit: 최대 결과 수
            offset: 시작 위치
            
        Returns:
            Torrent 객체 리스트
        """
        query = session.query(Torrent)
        
        # 기간 필터
        if period_days:
            since_date = datetime.now() - timedelta(days=period_days)
            # NULL 날짜도 제외
            query = query.filter(
                Torrent.upload_date != None,
                Torrent.upload_date >= since_date
            )
        
        # 검열 필터
        if censored is not None:
            query = query.filter(Torrent.censored == censored)
        
        # 국가 필터
        if country:
            query = query.filter(Torrent.country == country)
        
        # 장르 필터
        if genres:
            for genre_name in genres:
                query = query.join(Torrent.genres).filter(Genre.name == genre_name)
        
        # 검색어 필터
        if search_query:
            query = query.filter(Torrent.title.contains(search_query))
        
        # 정렬 (파라미터에 따라)
        # size 필드 정렬 시 size_bytes를 사용 (단위 고려)
        if sort_by == 'size':
            sort_column = Torrent.size_bytes
        else:
            sort_column = getattr(Torrent, sort_by, Torrent.upload_date)

        if sort_order == 'desc':
            query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(sort_column)
        
        # 제한 및 오프셋
        query = query.limit(limit).offset(offset)
        
        return query.all()
    
    def get_total_count(
        self,
        session: Session,
        period_days: Optional[int] = None,
        censored: Optional[bool] = None,
        country: Optional[str] = None,
        genres: Optional[List[str]] = None,
        search_query: Optional[str] = None
    ) -> int:
        """필터링된 토렌트 총 개수 반환 (페이지네이션용)"""
        query = session.query(Torrent)
        
        # 기간 필터
        if period_days:
            since_date = datetime.now() - timedelta(days=period_days)
            # NULL 날짜도 제외
            query = query.filter(
                Torrent.upload_date != None,
                Torrent.upload_date >= since_date
            )
        
        # 검열 필터
        if censored is not None:
            query = query.filter(Torrent.censored == censored)
        
        # 국가 필터
        if country and country != 'ALL':
            query = query.filter(Torrent.country == country)
        
        # 검색어 필터
        if search_query:
            query = query.filter(Torrent.title.contains(search_query))
        
        return query.count()
    
    def get_all_genres(self, session: Session) -> List[Genre]:
        """모든 장르 조회"""
        return session.query(Genre).order_by(Genre.name).all()
    
    def get_all_countries(self, session: Session) -> List[Country]:
        """모든 국가 조회"""
        return session.query(Country).order_by(Country.code).all()
    
    def has_torrents_from_source(self, session: Session, source_site: str) -> bool:
        """특정 소스에 기존 토렌트가 있는지 확인
        
        Args:
            session: 데이터베이스 세션
            source_site: 소스 사이트 이름
            
        Returns:
            기존 토렌트가 있으면 True, 없으면 False
        """
        count = session.query(Torrent).filter_by(source_site=source_site).count()
        return count > 0
    
    def get_existing_source_ids(self, session: Session, source_site: str) -> set:
        """특정 소스의 기존 source_id 집합 반환 (중복 체크용)
        
        Args:
            session: 데이터베이스 세션
            source_site: 소스 사이트 이름
            
        Returns:
            source_id 집합
        """
        torrents = session.query(Torrent.source_id).filter_by(source_site=source_site).all()
        return {t.source_id for t in torrents if t.source_id}

    def backfill_missing_dates(self, session: Session, limit: int = 500) -> int:
        """업로드 날짜가 비어있는 항목 보정 (sukebei.nyaa.si 전용)
        
        Returns:
            보정된 레코드 수
        """
        fixed = 0
        try:
            missing = (
                session.query(Torrent)
                .filter((Torrent.upload_date == None) | (Torrent.upload_date == ''))  # noqa: E711
                .limit(limit)
                .all()
            )
            if not missing:
                return 0
            s = requests.Session()
            s.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            })
            for t in missing:
                try:
                    if t.source_site != 'sukebei.nyaa.si' or not t.source_id:
                        continue
                    url = f"https://sukebei.nyaa.si/view/{t.source_id}"
                    resp = s.get(url, timeout=10)
                    if resp.status_code != 200:
                        continue
                    soup = BeautifulSoup(resp.content, 'lxml')
                    # view 페이지의 time 태그 또는 data-timestamp 추출
                    ts = None
                    time_tag = soup.find('time')
                    if time_tag and time_tag.has_attr('datetime'):
                        try:
                            # '2025-11-04 12:34' 형태를 처리
                            text = time_tag.get('datetime')
                            from datetime import datetime
                            for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S'):
                                try:
                                    ts = datetime.strptime(text[:16], '%Y-%m-%d %H:%M')
                                    break
                                except:
                                    pass
                        except:
                            ts = None
                    if ts is None:
                        td = soup.find(attrs={'data-timestamp': True})
                        if td:
                            try:
                                from datetime import datetime
                                ts = datetime.fromtimestamp(int(td.get('data-timestamp')))
                            except:
                                ts = None
                    if ts:
                        t.upload_date = ts
                        fixed += 1
                except Exception:
                    continue
            if fixed:
                session.commit()
            return fixed
        except Exception:
            session.rollback()
            return fixed

