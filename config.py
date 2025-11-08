"""설정 파일"""
import os
from dotenv import load_dotenv

load_dotenv()

# UI 설정
PAGE_SIZE = int(os.getenv('PAGE_SIZE', '50'))  # 페이지당 표시할 아이템 수
IMAGE_CACHE_SIZE = int(os.getenv('IMAGE_CACHE_SIZE', '200'))  # 이미지 메모리 캐시 크기

# 스크래핑 설정
MAX_SCRAPE_PAGES = int(os.getenv('MAX_SCRAPE_PAGES', '100'))  # 최대 스크래핑 페이지 수
ENABLE_THUMBNAIL = os.getenv('ENABLE_THUMBNAIL', 'true').lower() == 'true'  # 썸네일 검색 활성화
MAX_CONSECUTIVE_DUPLICATES = int(os.getenv('MAX_CONSECUTIVE_DUPLICATES', '3'))  # 중복 발견 시 중단할 연속 페이지 수
ENABLE_JAVDB_FALLBACK = os.getenv('ENABLE_JAVDB_FALLBACK', 'true').lower() == 'true'  # JAVDB 보조 검색 사용
ENABLE_SELENIUM_FOR_IMAGES = os.getenv('ENABLE_SELENIUM_FOR_IMAGES', 'true').lower() == 'true'  # 이미지 검색에 셀레니움 사용
IMAGE_HTTP_TIMEOUT = int(os.getenv('IMAGE_HTTP_TIMEOUT', '10'))  # 이미지/검색 HTTP 타임아웃(초)
IMAGE_HTTP_RETRIES = int(os.getenv('IMAGE_HTTP_RETRIES', '2'))  # 검색 요청 재시도 횟수
THUMBNAIL_SERVER_BLOCK_THRESHOLD = int(os.getenv('THUMBNAIL_SERVER_BLOCK_THRESHOLD', '200'))  # 썸네일 서버 정지 기준 (연속 실패 횟수)
PROXY_URL = os.getenv('PROXY_URL', '')  # 셀레니움/요청용 프록시 (예: http://127.0.0.1:7890 또는 socks5://127.0.0.1:1080)

# 스크래핑 설정
SCRAPE_SOURCES = [
    'https://sukebei.nyaa.si'
]

# 카테고리 및 필터
CATEGORIES = {
    'all': 'All categories',
    'art_anime': 'Art - Anime',
    'art_doujinshi': 'Art - Doujinshi',
    'art_games': 'Art - Games',
    'art_manga': 'Art - Manga',
    'art_pictures': 'Art - Pictures',
    'real_photobooks': 'Real Life - Photobooks and Pictures',
    'real_videos': 'Real Life - Videos'
}

# 필터 옵션
CENSORSHIP_FILTERS = ['All', 'Censored', 'Uncensored']

COUNTRY_FILTERS = ['All', 'Japan', 'China', 'Korea', 'USA', 'Europe', 'Other']

# 성인 장르 (예시)
GENRE_FILTERS = [
    'All',
    'Amateur',
    'Anal',
    'Asian',
    'BBW',
    'BDSM',
    'Big Tits',
    'Blowjob',
    'Bukkake',
    'Cosplay',
    'Creampie',
    'Cumshot',
    'Facial',
    'Fetish',
    'Gangbang',
    'Handjob',
    'Hardcore',
    'Hentai',
    'Interracial',
    'Lesbian',
    'MILF',
    'Masturbation',
    'Orgy',
    'POV',
    'Schoolgirl',
    'Softcore',
    'Solo',
    'Squirting',
    'Threesome',
    'Toys',
    'Uniform',
]

# 시간 범위 (키: 표시명, 값: 일수)
TIME_RANGES = {
    'all': '전체',
    '1day': '최근 1일',
    '3days': '최근 3일',
    '7days': '최근 7일',
    '14days': '최근 2주',
    '1month': '최근 1개월',
    '3months': '최근 3개월',
    '6months': '최근 6개월',
    '1year': '최근 1년',
    '2years': '최근 2년'
}

# 시간 범위를 일수로 변환하는 매핑
TIME_RANGE_DAYS = {
    'all': None,
    '1day': 1,
    '3days': 3,
    '7days': 7,
    '14days': 14,
    '1month': 30,
    '3months': 90,
    '6months': 180,
    '1year': 365,
    '2years': 730
}

# 정렬 옵션
SORT_OPTIONS = {
    'seeders': '시더 많은순',
    'date': '최신순',
    'size': '용량순',
    'downloads': '다운로드순'
}
