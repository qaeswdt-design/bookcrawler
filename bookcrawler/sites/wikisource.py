"""中文維基文庫（zh.wikisource.org）

網址格式：
  目錄  https://zh.wikisource.org/wiki/西遊記
  章節  https://zh.wikisource.org/wiki/西遊記/第001回
也接受 /zh-hant/、/zh-tw/ 等語言變體路徑，以及百分比編碼過的網址。
"""

import re
from urllib.parse import quote, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from ..errors import ParseError
from ..models import Book, Chapter, ChapterLink
from . import register
from .base import SiteAdapter

PATH_PATTERN = re.compile(r"^/(?:wiki|zh(?:-[a-z]+)?)/([^/#?]+)")
NOISE_SELECTORS = (
    "#headerContainer", ".ws-noexport", ".noprint", ".mw-editsection",
    "sup.reference", ".mw-references-wrap", "style", "script",
)
BLOCK_TAGS = ("p", "dl", "blockquote", "div", "ul", "ol", "center", "h2", "h3", "h4")


def _clean(text: str) -> str:
    return re.sub(r"[ \t ]+", " ", text).strip(" 　 \t")


def _block_text(tag: Tag) -> str:
    """區塊轉純文字，保留 <br> 和詩句（<dd>）的換行"""
    for br in tag.find_all("br"):
        br.replace_with("\n")
    lines = (_clean(line) for line in tag.get_text().split("\n"))
    return "\n".join(line for line in lines if line)


@register
class WikisourceAdapter(SiteAdapter):
    name = "wikisource"
    domains = ("zh.wikisource.org",)

    def _book_title(self, url: str) -> str:
        m = PATH_PATTERN.match(unquote(urlsplit(url).path))
        if not m or ":" in m.group(1):   # Author:、Special: 等不是作品頁
            raise ValueError(f"看不出書名，網址需要像 https://zh.wikisource.org/wiki/西遊記：{url}")
        return m.group(1).replace("_", " ")

    def book_key(self, url: str) -> str:
        return self._book_title(url)

    def toc_url(self, url: str) -> str:
        host = urlsplit(url).netloc
        return f"https://{host}/wiki/{quote(self._book_title(url).replace(' ', '_'))}"

    def parse_toc(self, html: str, url: str) -> Book:
        soup = BeautifulSoup(html, "html.parser")
        body = soup.select_one("#mw-content-text")
        if body is None:
            raise ParseError("找不到 #mw-content-text，可能不是維基文庫的條目頁")

        key = self._book_title(url)
        prefix = f"/wiki/{key.replace(' ', '_')}/"
        host = urlsplit(url).netloc
        chapters, seen = [], set()
        for a in body.find_all("a", href=True):
            full = urljoin(url, a["href"]).split("#")[0]
            parts = urlsplit(full)
            if parts.netloc != host:   # 只收同一個網站的章節，避免被頁面上的外部連結帶走
                continue
            if not unquote(parts.path).startswith(prefix) or full in seen:
                continue
            title = _clean(a.get_text())
            if not title:
                continue
            seen.add(full)
            chapters.append(ChapterLink(title=title, url=full))

        author = ""
        for a in body.find_all("a", href=True):
            if "/wiki/Author:" in unquote(a["href"]):
                author = _clean(a.get_text())
                break

        h1 = soup.find("h1")
        title = _clean(h1.get_text()) if h1 else key
        return Book(title=title, url=url, author=author, chapters=chapters)

    def parse_chapter(self, html: str, url: str, link: ChapterLink) -> Chapter:
        soup = BeautifulSoup(html, "html.parser")
        content = soup.select_one("#mw-content-text .mw-parser-output")
        if content is None:
            raise ParseError(f"找不到正文區塊：{url}")
        for selector in NOISE_SELECTORS:
            for tag in content.select(selector):
                tag.decompose()

        paragraphs = []
        for child in content.children:
            if isinstance(child, Tag) and child.name in BLOCK_TAGS:
                text = _block_text(child)
                if text:
                    paragraphs.append(text)
        if not paragraphs:
            raise ParseError(f"正文是空的：{url}")
        return Chapter(title=link.title, paragraphs=paragraphs)
