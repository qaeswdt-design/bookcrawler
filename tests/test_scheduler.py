import tempfile
import threading
import unittest
from pathlib import Path

from bookcrawler.errors import FetchError
from bookcrawler.scheduler import Scheduler


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        self.fail = {"https://a.example/1"}   # 這些網址會失敗

        def runner(task):
            self.calls.append(task["url"])
            if task["url"] in self.fail:
                raise FetchError("網站暫時壞了")
            return True

        self.s = Scheduler(runner, Path(self.tmp.name) / "queue.json", threading.Event(),
                           log=lambda *a: None)
        self.s.start()

    def tearDown(self):
        self.s.stop()
        self.tmp.cleanup()

    def test_failed_book_stays_pending_and_is_marked(self):
        self.s.add("https://a.example/1")
        self.s.wait_idle()
        self.assertEqual([t["url"] for t in self.s.pending()], ["https://a.example/1"])
        self.assertEqual(self.s.failed(), {"https://a.example/1"})
        self.assertEqual(self.calls, ["https://a.example/1"], "失敗不應該自動一直重試")

    def test_re_adding_failed_book_retries_it(self):
        self.s.add("https://a.example/1")
        self.s.wait_idle()
        self.fail.clear()                       # 網站修好了
        self.assertTrue(self.s.add("https://a.example/1"))
        self.s.wait_idle()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.s.pending(), [], "重試成功後應該移出排程")
        self.assertEqual(self.s.failed(), set())

    def test_retry_failed(self):
        self.fail = {"https://a.example/1", "https://a.example/2"}
        self.s.add("https://a.example/1")
        self.s.add("https://a.example/2")
        self.s.wait_idle()
        self.fail.clear()
        self.assertEqual(self.s.retry_failed(), 2)
        self.s.wait_idle()
        self.assertEqual(self.s.pending(), [])
        self.assertEqual(self.s.retry_failed(), 0)

    def test_adding_book_still_waiting_is_ignored(self):
        self.s.stop()   # 背景停住，書會一直在排隊
        s = Scheduler(lambda t: True, Path(self.tmp.name) / "q2.json", threading.Event(),
                      log=lambda *a: None)
        self.assertTrue(s.add("https://a.example/9"))
        self.assertFalse(s.add("https://a.example/9"))
        self.assertEqual(len(s.pending()), 1)


if __name__ == "__main__":
    unittest.main()
