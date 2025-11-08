"""데이터베이스 모델 정의"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, Text, Table, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

# 다대다 관계를 위한 연결 테이블
torrent_genres = Table(
    'torrent_genres',
    Base.metadata,
    Column('torrent_id', Integer, ForeignKey('torrents.id'), primary_key=True),
    Column('genre_id', Integer, ForeignKey('genres.id'), primary_key=True)
)


class Torrent(Base):
    """토렌트 정보 모델"""
    __tablename__ = 'torrents'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # 기본 정보
    title = Column(String(500), nullable=False, index=True)
    source_id = Column(String(100), unique=True)  # 원본 사이트의 ID
    source_site = Column(String(100), default='sukebei.nyaa.si')
    
    # 토렌트 정보
    magnet_link = Column(Text, nullable=False)
    torrent_link = Column(String(500))
    size = Column(String(50))  # "1.5 GiB" 형식
    size_bytes = Column(Integer)  # 정렬을 위한 바이트 단위
    
    # 미디어 정보
    thumbnail_url = Column(String(500))
    snapshot_urls = Column(Text)  # JSON 배열로 저장 (여러 스냅샷)
    thumbnail_searched_servers = Column(Text, default='[]')  # JSON 배열로 저장 (탐색한 서버 목록: ['fc2ppv', 'javbee'])
    
    # 분류 정보
    category = Column(String(100))
    censored = Column(Boolean, default=True)  # True=Censored, False=Uncensored
    country = Column(String(50))  # JP, CN, KR, US 등
    
    # 통계 정보
    seeders = Column(Integer, default=0, index=True)
    leechers = Column(Integer, default=0)
    downloads = Column(Integer, default=0)  # 완료 수
    comments = Column(Integer, default=0)
    views = Column(Integer, default=0)  # 조회수
    
    # 인기도 점수 (계산된 값)
    popularity_score = Column(Float, default=0.0, index=True)
    
    # 시간 정보
    upload_date = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 관계
    genres = relationship('Genre', secondary=torrent_genres, back_populates='torrents')
    
    def __repr__(self):
        return f"<Torrent(id={self.id}, title='{self.title[:30]}...')>"
    
    def calculate_popularity(self):
        """인기도 점수 계산 (0-100)
        
        가중치:
        - 시더: 30% (최대 30점)
        - 완료수: 25% (최대 25점)
        - 조회수: 20% (최대 20점)
        - 댓글: 10% (최대 10점)
        - 리처: 15% (최대 15점)
        """
        # 시더 점수 (최대 30점)
        seeder_score = min((self.seeders or 0) / 10, 30)
        
        # 완료 수 점수 (최대 25점)
        download_score = min((self.downloads or 0) / 100, 25)
        
        # 조회수 점수 (최대 20점)
        views_score = min((self.views or 0) / 1000, 20)
        
        # 댓글 점수 (최대 10점)
        comment_score = min((self.comments or 0) / 10, 10)
        
        # 리처 점수 (최대 15점, 리처가 많으면 현재 인기있다는 의미)
        leecher_score = min((self.leechers or 0) / 20, 15)
        
        self.popularity_score = (
            seeder_score + download_score + views_score + 
            comment_score + leecher_score
        )


class Genre(Base):
    """장르 모델"""
    __tablename__ = 'genres'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False, index=True)
    name_kr = Column(String(50))  # 한국어 이름
    
    # 관계
    torrents = relationship('Torrent', secondary=torrent_genres, back_populates='genres')
    
    def __repr__(self):
        return f"<Genre(name='{self.name}')>"


class Country(Base):
    """국가 코드 참조 테이블"""
    __tablename__ = 'countries'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), unique=True, nullable=False)  # JP, CN, KR, US 등
    name = Column(String(50), nullable=False)
    name_kr = Column(String(50))
    
    def __repr__(self):
        return f"<Country(code='{self.code}', name='{self.name}')>"

