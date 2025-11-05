"""웹 스크래퍼 패키지"""
from .sukebei_scraper import SukebeiScraper
from .javtorrent_scraper import JAVTorrentScraper
from .torrentkitty_scraper import TorrentKittyScraper
from .scraper_manager import ScraperManager

# Selenium 스크래퍼 (선택적)
try:
    from .selenium_scraper import SeleniumSukebeiScraper
    __all__ = ['SukebeiScraper', 'JAVTorrentScraper', 'TorrentKittyScraper', 'ScraperManager', 'SeleniumSukebeiScraper']
except ImportError:
    __all__ = ['SukebeiScraper', 'JAVTorrentScraper', 'TorrentKittyScraper', 'ScraperManager']

