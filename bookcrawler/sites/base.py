from abc import ABC, abstractmethod
from urllib.parse import urlsplit

from ..models import Book, Chapter, ChapterLink


class SiteAdapter(ABC):
    """一個網站的解析規則。核心程式只透過這個介面和網站打交道。

    子類別需要設定 name、domains，並實作 book_key、toc_url、parse_toc、parse_chapter。
    parse_* 只接收 HTML 字串、不自己連網，所以可以用存下來的網頁離線測試。
    """

    name: str = ""
    domains: tuple[str, ...] = ()

    def matches(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in self.domains)

    @abstractmethod
    def book_key(self, url: str) -> str:
        """同一本書不論貼目錄頁或章節頁，都要得到相同的 key（用來存進度）"""

    @abstractmethod
    def toc_url(self, url: str) -> str:
        """從使用者貼的網址（目錄頁或任一章）算出目錄頁網址"""

    @abstractmethod
    def parse_toc(self, html: str, url: str) -> Book:
        """解析目錄頁"""

    @abstractmethod
    def parse_chapter(self, html: str, url: str, link: ChapterLink) -> Chapter:
        """解析章節頁"""
