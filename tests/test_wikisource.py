import unittest
from pathlib import Path

from bookcrawler.errors import ParseError
from bookcrawler.models import ChapterLink
from bookcrawler.sites import find_adapter
from bookcrawler.sites.wikisource import WikisourceAdapter

SAMPLES = Path(__file__).parent / "samples"
TOC_URL = "https://zh.wikisource.org/wiki/%E8%A5%BF%E9%81%8A%E8%A8%98"


def sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


class UrlTests(unittest.TestCase):
    def setUp(self):
        self.adapter = WikisourceAdapter()

    def test_registered(self):
        self.assertIsInstance(find_adapter(TOC_URL), WikisourceAdapter)
        self.assertIsNone(find_adapter("https://example.com/book/1"))

    def test_toc_and_chapter_urls_give_same_key(self):
        urls = [
            "https://zh.wikisource.org/wiki/西遊記",
            "https://zh.wikisource.org/wiki/西遊記/第001回",
            "https://zh.wikisource.org/zh-hant/西遊記/第050回",
            TOC_URL,
        ]
        self.assertEqual({self.adapter.book_key(u) for u in urls}, {"西遊記"})
        self.assertEqual(self.adapter.toc_url(urls[1]), TOC_URL)

    def test_rejects_non_work_pages(self):
        with self.assertRaises(ValueError):
            self.adapter.book_key("https://zh.wikisource.org/wiki/Author:吳承恩")


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.adapter = WikisourceAdapter()

    def test_parse_toc(self):
        book = self.adapter.parse_toc(sample("wikisource_toc.html"), TOC_URL)
        self.assertEqual(book.title, "西遊記")
        self.assertEqual(book.author, "吳承恩")
        self.assertEqual(len(book.chapters), 101)   # 100 回＋附錄
        self.assertEqual(book.chapters[0].title, "第一回")
        self.assertEqual(book.chapters[99].title, "第一百回")
        self.assertEqual(book.chapters[-1].title, "附錄")
        self.assertTrue(book.chapters[0].url.endswith("/wiki/%E8%A5%BF%E9%81%8A%E8%A8%98/%E7%AC%AC001%E5%9B%9E"))
        self.assertEqual(len({c.url for c in book.chapters}), 101, "章節網址不應重複")

    def test_toc_ignores_links_to_other_hosts(self):
        html = """<h1>西遊記</h1><div id="mw-content-text">
          <a href="/wiki/西遊記/第001回">第一回</a>
          <a href="https://evil.example/wiki/西遊記/第002回">第二回</a>
        </div>"""
        book = self.adapter.parse_toc(html, TOC_URL)
        self.assertEqual([c.title for c in book.chapters], ["第一回"])

    def test_parse_chapter(self):
        link = ChapterLink("第一回", "https://zh.wikisource.org/wiki/西遊記/第001回")
        chapter = self.adapter.parse_chapter(sample("wikisource_chapter.html"), link.url, link)
        self.assertEqual(chapter.title, "第一回")
        self.assertEqual(chapter.paragraphs[0], "詩曰：")
        self.assertEqual(chapter.paragraphs[1].split("\n")[0], "混沌未分天地亂，茫茫渺渺無人見。",
                         "詩句要保留換行")
        self.assertTrue(chapter.paragraphs[-1].startswith("畢竟不知向後修些甚麼道果"))
        text = "\n".join(chapter.paragraphs)
        self.assertNotIn("返回頂部", text, "頁尾導覽不應混進正文")
        self.assertNotIn("姊妹计划", text, "頁首資訊不應混進正文")

    def test_missing_content_raises(self):
        link = ChapterLink("x", "https://zh.wikisource.org/wiki/x/1")
        with self.assertRaises(ParseError):
            self.adapter.parse_chapter("<html><body>驗證頁</body></html>", link.url, link)


if __name__ == "__main__":
    unittest.main()
