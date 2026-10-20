import threading
import unittest

from bookcrawler.errors import BlockedError, DisallowedError, FetchError, StopRequested
from bookcrawler.fetcher import Fetcher, decode_html, parse_retry_after

from .server import FakeSite

UA = "bookcrawler-test/1.0"


def make_fetcher(**kwargs) -> Fetcher:
    options = dict(delay=(0, 0), long_break_every=0, retry_wait=0, max_retry=2, log=lambda *a: None)
    options.update(kwargs)
    return Fetcher(UA, **options)


class DecodeTests(unittest.TestCase):
    def test_encodings(self):
        text = "第一章　天下大勢"
        self.assertEqual(decode_html(text.encode("utf-8")), text)
        self.assertEqual(decode_html(text.encode("gb18030")), text)
        self.assertEqual(decode_html(text.encode("big5"), "big5"), text)

    def test_undeclared_big5_and_gbk(self):
        # Big5 的位元組幾乎都是合法 GB18030，沒有自動偵測的話 Big5 會被誤解成亂碼
        big5 = "這是一段台灣小說網站常見的繁體中文內容，主角走進了古老的圖書館。"
        gbk = "这是一段大陆小说网站常见的简体中文内容，主角走进了古老的图书馆。"
        self.assertEqual(decode_html(big5.encode("big5")), big5)
        self.assertEqual(decode_html(gbk.encode("gbk")), gbk)
        self.assertEqual(decode_html("第一章　天下大勢".encode("big5")), "第一章　天下大勢")

    def test_meta_charset_beats_guessing(self):
        html = '<meta charset="gbk"><p>天下</p>'
        self.assertIn("天下", decode_html(html.encode("gbk")))

    def test_ignores_requests_default_latin1(self):
        self.assertEqual(decode_html("中文".encode("utf-8"), "ISO-8859-1"), "中文")

    def test_retry_after(self):
        self.assertEqual(parse_retry_after("120"), 120)
        self.assertIsNone(parse_retry_after(None))
        self.assertIsNone(parse_retry_after("soon"))


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.site = FakeSite().__enter__()
        self.site.route("/robots.txt", "User-agent: *\nDisallow: /private/\n")

    def tearDown(self):
        self.site.__exit__(None, None, None)

    def test_ok(self):
        self.site.route("/a", "<p>hello</p>")
        self.assertEqual(make_fetcher().get(self.site.base + "/a"), "<p>hello</p>")

    def test_retries_server_errors(self):
        self.site.route("/flaky", (500, {}, "oops"), (503, {}, "busy"), "<p>ok</p>")
        self.assertEqual(make_fetcher().get(self.site.base + "/flaky"), "<p>ok</p>")
        self.assertEqual(self.site.hits["/flaky"], 3)

    def test_gives_up_after_max_retry(self):
        self.site.route("/down", (500, {}, "oops"))
        with self.assertRaises(FetchError):
            make_fetcher(max_retry=2).get(self.site.base + "/down")
        self.assertEqual(self.site.hits["/down"], 3)

    def test_retry_after_capped(self):
        self.site.route("/slow", (503, {"Retry-After": "86400"}, "busy"), "<p>ok</p>")
        waits = []
        f = make_fetcher(retry_wait_max=0)
        f._sleep = waits.append
        self.assertEqual(f.get(self.site.base + "/slow"), "<p>ok</p>")
        self.assertEqual(max(waits), 0, "網站要求等一整天也不應該超過 retry_wait_max")

    def test_challenge_stops_without_retry(self):
        self.site.route("/cf", (403, {"cf-mitigated": "challenge"}, "<title>Just a moment...</title>"))
        with self.assertRaises(BlockedError):
            make_fetcher().get(self.site.base + "/cf")
        self.assertEqual(self.site.hits["/cf"], 1, "遇到驗證不應該重試")

    def test_challenge_detected_from_body(self):
        self.site.route("/gate", (503, {}, '<div id="challenge-platform"></div>'))
        with self.assertRaises(BlockedError):
            make_fetcher().get(self.site.base + "/gate")
        self.assertEqual(self.site.hits["/gate"], 1)

    def test_404_not_retried(self):
        with self.assertRaises(FetchError):
            make_fetcher().get(self.site.base + "/missing")
        self.assertEqual(self.site.hits["/missing"], 1)

    def test_robots_disallow(self):
        self.site.route("/private/x", "secret")
        with self.assertRaises(DisallowedError):
            make_fetcher().get(self.site.base + "/private/x")
        self.assertEqual(self.site.hits["/private/x"], 0, "被 robots.txt 禁止的頁面一次都不應該請求")
        self.assertEqual(self.site.hits["/robots.txt"], 1)

    def test_robots_fetched_once_per_host(self):
        self.site.route("/a", "a")
        f = make_fetcher()
        f.get(self.site.base + "/a")
        f.get(self.site.base + "/a")
        self.assertEqual(self.site.hits["/robots.txt"], 1)

    def test_missing_robots_means_allowed(self):
        self.site.routes.pop("/robots.txt")
        self.site.route("/private/x", "ok")
        self.assertEqual(make_fetcher().get(self.site.base + "/private/x"), "ok")

    def test_crawl_delay_respected(self):
        self.site.route("/robots.txt", "User-agent: *\nCrawl-delay: 7\n")
        self.site.route("/a", "a")
        f = make_fetcher()
        f.get(self.site.base + "/a")
        self.assertEqual(f._crawl_delay(self.site.base + "/a"), 7.0)

    def test_stop_interrupts_wait(self):
        self.site.route("/a", "a")
        stop = threading.Event()
        f = make_fetcher(delay=(30, 30), stop_event=stop)
        f.get(self.site.base + "/a")
        threading.Timer(0.2, stop.set).start()
        with self.assertRaises(StopRequested):
            f.get(self.site.base + "/a")   # 第二次請求前要等 30 秒，應該被 stop 打斷
        self.assertEqual(self.site.hits["/a"], 1)


if __name__ == "__main__":
    unittest.main()
