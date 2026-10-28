"""端對端測試：本機假網站 + 測試用轉接器，驗證抓取、斷點續抓、當機復原"""

import re
import tempfile
import unittest
from pathlib import Path

from bookcrawler.crawler import crawl_book
from bookcrawler.errors import BlockedError
from bookcrawler.fetcher import Fetcher
from bookcrawler.models import Book, Chapter, ChapterLink
from bookcrawler.sites.base import SiteAdapter
from bookcrawler.storage import Storage, safe_filename

from .server import FakeSite


class DemoAdapter(SiteAdapter):
    """假網站：/book/<id>/ 是目錄，/book/<id>/<n>.html 是章節"""
    name = "demo"
    domains = ("127.0.0.1",)

    def book_key(self, url):
        return re.search(r"/book/(\d+)", url).group(1)

    def toc_url(self, url):
        return re.sub(r"(/book/\d+/).*", r"\1", url)

    def parse_toc(self, html, url):
        links = re.findall(r'<a href="(\d+\.html)">(.+?)</a>', html)
        return Book("示範小說", url, chapters=[ChapterLink(t, url + h) for h, t in links])

    def parse_chapter(self, html, url, link):
        return Chapter(link.title, re.findall(r"<p>(.+?)</p>", html))


def toc(n: int) -> str:
    return "".join(f'<a href="{i}.html">第{i}章</a>' for i in range(1, n + 1))


class CrawlTests(unittest.TestCase):
    def setUp(self):
        self.site = FakeSite().__enter__()
        self.site.route("/robots.txt", (404, {}, ""))
        self.site.route("/book/7/", toc(3))
        for i in range(1, 6):
            self.site.route(f"/book/7/{i}.html", f"<p>第{i}章第一段</p><p>第{i}章第二段</p>")
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "out", Path(self.tmp.name) / "state")
        self.fetcher = Fetcher("test", delay=(0, 0), retry_wait=0, log=lambda *a: None)
        self.url = self.site.base + "/book/7/2.html"   # 貼任一章的網址也可以

    def tearDown(self):
        self.site.__exit__(None, None, None)
        self.tmp.cleanup()

    def crawl(self, **kwargs):
        return crawl_book(self.url, DemoAdapter(), self.fetcher, self.storage, log=lambda *a: None, **kwargs)

    def test_full_crawl(self):
        result = self.crawl()
        self.assertTrue(result.finished)
        text = result.output.read_text(encoding="utf-8")
        self.assertEqual(text.count("第一段"), 3)
        self.assertLess(text.index("第1章"), text.index("第2章"))
        self.assertLess(text.index("第2章"), text.index("第3章"))

    def test_resume_after_pause(self):
        first = self.crawl(max_chapters=2)
        self.assertFalse(first.finished)
        second = self.crawl()
        self.assertEqual(second.new_chapters, 1)
        text = second.output.read_text(encoding="utf-8")
        self.assertEqual([text.count(f"第{i}章第一段") for i in (1, 2, 3)], [1, 1, 1])
        self.assertEqual(self.site.hits["/book/7/1.html"], 1, "已抓過的章節不應重抓")

    def test_crash_between_write_and_progress_leaves_no_duplicate(self):
        result = self.crawl(max_chapters=1)
        with open(result.output, "a", encoding="utf-8") as f:
            f.write("第2章\n\n寫到一半就當機")   # 模擬寫入後、更新進度前當機
        final = self.crawl()
        text = final.output.read_text(encoding="utf-8")
        self.assertNotIn("寫到一半", text)
        self.assertEqual(text.count("第2章第一段"), 1)

    def test_new_chapters_of_ongoing_novel(self):
        self.crawl()
        self.site.route("/book/7/", toc(5))   # 連載更新了兩章
        result = self.crawl()
        self.assertEqual(result.new_chapters, 2)
        self.assertEqual(result.output.read_text(encoding="utf-8").count("第一段"), 5)

    def test_does_not_overwrite_existing_file(self):
        existing = self.storage.output_path("示範小說")
        existing.parent.mkdir(parents=True)
        existing.write_text("別人的檔案", encoding="utf-8")
        result = self.crawl()
        self.assertEqual(existing.read_text(encoding="utf-8"), "別人的檔案")
        self.assertNotEqual(result.output, existing)

    def test_blocked_keeps_progress(self):
        self.crawl(max_chapters=1)
        self.site.route("/book/7/2.html", (403, {"cf-mitigated": "challenge"}, "Just a moment"))
        with self.assertRaises(BlockedError):
            self.crawl()
        progress = self.storage.load_progress("demo", "7")
        self.assertEqual(progress["next_index"], 1)


class StorageTests(unittest.TestCase):
    def test_safe_filename(self):
        self.assertEqual(safe_filename('a/b:c*?"<>|d'), "a_b_c______d")
        self.assertEqual(safe_filename("  書名\n第二行 . "), "書名 第二行")
        self.assertEqual(safe_filename(""), "未命名")
        self.assertLessEqual(len(safe_filename("長" * 200)), 80)


if __name__ == "__main__":
    unittest.main()
