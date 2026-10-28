"""背景排程：一次一本依序抓，佇列存檔，下次開程式自動接續"""

import queue
import threading
from pathlib import Path
from typing import Callable

from .errors import BlockedError, CrawlerError, StopRequested
from .storage import load_json, save_json

# runner(task) 回傳 True 表示這本已完成、可以移出排程
Runner = Callable[[dict], bool]


class Scheduler:
    def __init__(self, runner: Runner, queue_file: Path, stop_event: threading.Event, log=print):
        self.runner = runner
        self.queue_file = Path(queue_file)
        self.stop_event = stop_event
        self.log = log
        self._lock = threading.Lock()
        self._pending: list[dict] = []
        self._queue: queue.Queue[dict] = queue.Queue()
        self._thread: threading.Thread | None = None

    def restore(self) -> int:
        """載入上次沒完成的排程，回傳本數"""
        for task in load_json(self.queue_file, []):
            self.add(task["url"], task.get("name", ""), save=False)
        return len(self._pending)

    def add(self, url: str, name: str = "", save: bool = True) -> bool:
        with self._lock:
            if any(t["url"] == url for t in self._pending):
                return False
            task = {"url": url, "name": name}
            self._pending.append(task)
        self._queue.put(task)
        if save:
            self._save()
        return True

    def pending(self) -> list[dict]:
        with self._lock:
            return list(self._pending)

    def start(self):
        self._thread = threading.Thread(target=self._work, daemon=True)
        self._thread.start()

    def stop(self):
        self.stop_event.set()
        if self._thread:
            self._thread.join()

    def wait_idle(self):
        """等排程裡的書都處理過一次（測試用）"""
        self._queue.join()

    def _save(self):
        save_json(self.queue_file, self.pending())

    def _remove(self, task: dict):
        with self._lock:
            self._pending = [t for t in self._pending if t["url"] != task["url"]]
        self._save()

    def _work(self):
        while not self.stop_event.is_set():
            try:
                task = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            finished = False
            try:
                finished = self.runner(task)
            except StopRequested:
                self.log("已暫停，進度已保存")
            except BlockedError as e:
                self.log(f"{e}\n這本保留在排程中")
            except (CrawlerError, ValueError) as e:
                self.log(f"《{task['name'] or task['url']}》失敗：{e}\n這本保留在排程中，換下一本")
            except Exception as e:   # 單本的意外錯誤不讓整個排程停掉
                self.log(f"《{task['name'] or task['url']}》發生未預期的錯誤：{type(e).__name__}: {e}")
            if finished:
                self._remove(task)
            self._queue.task_done()
            if self._queue.empty() and not self.stop_event.is_set():
                self.log("排程中的書都處理過了，可以繼續輸入新的")
