"""
토렌트 데이터 모델 정의
"""
from datetime import datetime
from enum import Enum
from typing import Optional, List


class ContentType(Enum):
    """컨텐츠 타입"""
    CENSORED = "censored"
    UNCENSORED = "uncensored"


class Country(Enum):
    """제작 국가"""
    JAPAN = "japan"
    KOREA = "korea"
    CHINA = "china"
    USA = "usa"
    EUROPE = "europe"
    OTHER = "other"


class Genre(Enum):
    """장르"""
    DRAMA = "drama"
    ROMANCE = "romance"
    COMEDY = "comedy"
    ACTION = "action"
    FANTASY = "fantasy"
    OTHER = "other"


class TorrentItem:
    """토렌트 아이템 모델"""
    
    def __init__(
        self,
        title: str,
        magnet: str,
        size: str,
        content_type: ContentType,
        country: Country,
        upload_date: datetime,
        thumbnail_url: str = "",
        views: int = 0,
        downloads: int = 0,
        seeders: int = 0,
        leechers: int = 0,
        genres: Optional[List[Genre]] = None,
        description: str = "",
        torrent_id: Optional[str] = None
    ):
        self.torrent_id = torrent_id
        self.title = title
        self.magnet = magnet
        self.size = size
        self.thumbnail_url = thumbnail_url
        self.content_type = content_type
        self.country = country
        self.upload_date = upload_date
        self.views = views
        self.downloads = downloads
        self.seeders = seeders
        self.leechers = leechers
        self.genres = genres or []
        self.description = description
    
    def to_dict(self):
        """딕셔너리 형태로 변환"""
        return {
            "title": self.title,
            "magnet": self.magnet,
            "size": self.size,
            "thumbnail_url": self.thumbnail_url,
            "content_type": self.content_type.value,
            "country": self.country.value,
            "upload_date": self.upload_date.isoformat(),
            "views": self.views,
            "downloads": self.downloads,
            "seeders": self.seeders,
            "leechers": self.leechers,
            "genres": [g.value for g in self.genres],
            "description": self.description
        }
    
    @staticmethod
    def from_dict(data: dict, torrent_id: str = None):
        """딕셔너리로부터 객체 생성"""
        return TorrentItem(
            torrent_id=torrent_id,
            title=data.get("title", ""),
            magnet=data.get("magnet", ""),
            size=data.get("size", ""),
            thumbnail_url=data.get("thumbnail_url", ""),
            content_type=ContentType(data.get("content_type", "censored")),
            country=Country(data.get("country", "other")),
            upload_date=datetime.fromisoformat(data.get("upload_date", datetime.now().isoformat())),
            views=data.get("views", 0),
            downloads=data.get("downloads", 0),
            seeders=data.get("seeders", 0),
            leechers=data.get("leechers", 0),
            genres=[Genre(g) for g in data.get("genres", [])],
            description=data.get("description", "")
        )


class FilterOptions:
    """필터링 옵션"""
    
    def __init__(
        self,
        content_types: Optional[List[ContentType]] = None,
        countries: Optional[List[Country]] = None,
        genres: Optional[List[Genre]] = None,
        min_size_mb: Optional[float] = None,
        max_size_mb: Optional[float] = None,
        min_seeders: int = 0,
        search_keyword: str = ""
    ):
        self.content_types = content_types or []
        self.countries = countries or []
        self.genres = genres or []
        self.min_size_mb = min_size_mb
        self.max_size_mb = max_size_mb
        self.min_seeders = min_seeders
        self.search_keyword = search_keyword

