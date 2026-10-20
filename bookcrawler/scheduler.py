"""背景排程：一次一本依序抓，佇列存檔，下次開程式自動接續

沒抓完的書（被擋、網站錯誤）留在排程裡並標記為失敗，不會自動重試，
避免對擋住我們的網站一直重複請求。要重試就再貼一次同網址，或用 retry_failed()。
"""

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
        self._save_lock = threading.Lock()   # 主執行緒和背景都會存檔，共用同一個暫存檔，要排隊
        self._pending: list[dict] = []
        self._failed: set[str] = set()   # 處理過但沒完成、目前不在佇列裡的網址
        self._queue: queue.Queue[dict] = queue.Queue()
        self._thread: threading.Thread | None = None

    def restore(self) -> int:
        """載入上次沒完成的排程，回傳本數"""
        for task in load_json(self.queue_file, []):
            self.add(task["url"], task.get("name", ""), save=False)
        return len(self._pending)

    def add(self, url: str, name: str = "", save: bool = True) -> bool:
        """加入排程。已經在排程中：之前失敗的會重新排入（回傳 True），還在排隊的略過（回傳 False）"""
        with self._lock:
            existing = next((t for t in self._pending if t["url"] == url), None)
            if existing is not None:
                if url not in self._failed:
                    return False
                self._failed.discard(url)
                task, changed = existing, False   # 排程內容沒變，不用存檔
            else:
                task, changed = {"url": url, "name": name}, True
                self._pending.append(task)
        self._queue.put(task)
        if save and changed:
            self._save()
        return True

    def pending(self) -> list[dict]:
        with self._lock:
            return list(self._pending)

    def failed(self) -> set[str]:
        with self._lock:
            return set(self._failed)

    def retry_failed(self) -> int:
        """把所有失敗的書重新排入佇列，回傳本數"""
        with self._lock:
            tasks = [t for t in self._pending if t["url"] in self._failed]
            self._failed.clear()
        for task in tasks:
            self._queue.put(task)
        return len(tasks)

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
        with self._save_lock:
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
            elif not self.stop_event.is_set():
                with self._lock:
                    self._failed.add(task["url"])
                self.log("  要重試這本：輸入 retry，或再貼一次同一個網址")
            self._queue.task_done()
            if self._queue.empty() and not self.stop_event.is_set():
                self.log("排程中的書都處理過了，可以繼續輸入新的")
