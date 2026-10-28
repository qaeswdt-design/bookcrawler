"""HTTP 抓取：限速、遵守 robots.txt、自動重試、偵測機器人驗證

設計原則：
- 每次請求之間隨機等待，並且不少於 robots.txt 的 Crawl-delay
- 連續抓一段就長休息，降低對網站的負擔
- 暫時性錯誤（逾時、429、5xx）重試，等待時間逐次加倍；有 Retry-After 就照它的
- 遇到機器人驗證或 403 立刻停止，不重試、也不嘗試繞過
"""

import random
import re
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

from .errors import BlockedError, DisallowedError, FetchError, StopRequested

RETRY_STATUS = {429, 500, 502, 503, 504}
CHALLENGE_MARKERS = (
    "just a moment",
    "cf-browser-verification",
    "challenge-platform",
    "challenge_required",
    "captcha",
)
META_CHARSET = re.compile(rb'<meta[^>]+charset=["\']?([\w-]+)', re.IGNORECASE)


def decode_html(content: bytes, declared: str | None = None) -> str:
    """依序嘗試：HTTP 標頭宣告的編碼 → <meta charset> → utf-8 → gb18030 → big5

    沒宣告編碼時 requests 會預設 ISO-8859-1，中文網頁幾乎都不是，所以忽略它。
    """
    candidates = []
    if declared and declared.lower() != "iso-8859-1":
        candidates.append(declared)
    m = META_CHARSET.search(content[:4096])
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates += ["utf-8", "gb18030", "big5"]
    for enc in candidates:
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", errors="replace")


def is_challenge(resp: requests.Response) -> bool:
    """是否為機器人驗證頁（Cloudflare 等）"""
    if resp.headers.get("cf-mitigated") == "challenge":
        return True
    if resp.status_code in (403, 429, 503):
        head = resp.content[:8192].decode("utf-8", "ignore").lower()
        return any(marker in head for marker in CHALLENGE_MARKERS)
    return False


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After 可能是秒數或 HTTP 日期"""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class Fetcher:
    def __init__(
        self,
        user_agent: str,
        *,
        delay: tuple[float, float] = (5.0, 10.0),
        long_break_every: int = 50,
        long_break: tuple[float, float] = (300.0, 600.0),
        max_retry: int = 4,
        retry_wait: float = 30.0,
        retry_wait_max: float = 600.0,
        timeout: float = 20.0,
        respect_robots: bool = True,
        session: requests.Session | None = None,
        stop_event: threading.Event | None = None,
        log=print,
    ):
        self.user_agent = user_agent
        self.delay = delay
        self.long_break_every = long_break_every
        self.long_break = long_break
        self.max_retry = max_retry
        self.retry_wait = retry_wait
        self.retry_wait_max = retry_wait_max
        self.timeout = timeout
        self.respect_robots = respect_robots
        self.stop_event = stop_event or threading.Event()
        self.log = log

        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.session.headers.setdefault("Accept-Language", "zh-TW,zh;q=0.9,en;q=0.5")

        self._robots: dict[str, RobotFileParser] = {}
        self._last_request: float | None = None
        self.request_count = 0

    # ---------- 對外介面 ----------

    def get(self, url: str) -> str:
        """抓一個頁面，回傳解碼後的 HTML"""
        if self.stop_event.is_set():
            raise StopRequested()
        self._check_robots(url)

        for attempt in range(self.max_retry + 1):
            self._throttle(url)
            retry_after = None
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as e:
                problem = f"連線錯誤 {type(e).__name__}"
            else:
                if is_challenge(resp):
                    raise BlockedError(
                        f"網站要求機器人驗證（狀態碼 {resp.status_code}）：{url}\n"
                        "已停止，重試沒有用，請過一段時間再試")
                if resp.status_code == 200:
                    return decode_html(resp.content, resp.encoding)
                if resp.status_code == 403:
                    raise BlockedError(f"網站拒絕存取（403）：{url}")
                if resp.status_code not in RETRY_STATUS:
                    raise FetchError(f"狀態碼 {resp.status_code}：{url}")
                problem = f"狀態碼 {resp.status_code}"
                retry_after = parse_retry_after(resp.headers.get("Retry-After"))

            if attempt == self.max_retry:
                raise FetchError(f"重試 {self.max_retry} 次仍失敗（{problem}）：{url}")
            wait = retry_after if retry_after is not None else \
                min(self.retry_wait * 2 ** attempt, self.retry_wait_max)
            self.log(f"  {problem}，{wait:.0f} 秒後第 {attempt + 1}/{self.max_retry} 次重試")
            self._sleep(wait)

        raise AssertionError("unreachable")

    def stop(self):
        self.stop_event.set()

    # ---------- 內部 ----------

    def _sleep(self, seconds: float):
        """可被 stop() 中斷的等待。分段等，Windows 上按 Ctrl+C 才能馬上反應"""
        end = time.monotonic() + seconds
        while (remaining := end - time.monotonic()) > 0:
            if self.stop_event.wait(min(remaining, 1.0)):
                break
        if self.stop_event.is_set():
            raise StopRequested()

    def _throttle(self, url: str):
        if self._last_request is not None:
            if self.long_break_every and self.request_count % self.long_break_every == 0:
                wait = random.uniform(*self.long_break)
                self.log(f"  已連續抓了 {self.request_count} 頁，休息 {wait / 60:.1f} 分鐘")
            else:
                wait = random.uniform(*self.delay)
            wait = max(wait, self._crawl_delay(url))
            self._sleep(wait - (time.monotonic() - self._last_request))
        self._last_request = time.monotonic()
        self.request_count += 1

    def _robots_for(self, url: str) -> RobotFileParser | None:
        """依 RFC 9309：4xx 視為沒有限制；5xx 或連不上則不抓（下次再試）"""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        try:
            resp = self.session.get(origin + "/robots.txt", timeout=self.timeout)
        except requests.RequestException as e:
            raise FetchError(f"讀不到 {origin}/robots.txt（{type(e).__name__}），為了安全先不抓")
        if resp.status_code >= 500:
            raise FetchError(f"{origin}/robots.txt 回應 {resp.status_code}，為了安全先不抓")
        parser = None
        if resp.status_code == 200:
            parser = RobotFileParser()
            parser.parse(decode_html(resp.content, resp.encoding).splitlines())
        self._robots[origin] = parser
        return parser

    def _check_robots(self, url: str):
        if not self.respect_robots:
            return
        parser = self._robots_for(url)
        if parser and not parser.can_fetch(self.user_agent, url):
            raise DisallowedError(f"robots.txt 不允許抓取：{url}")

    def _crawl_delay(self, url: str) -> float:
        if not self.respect_robots:
            return 0.0
        parser = self._robots.get("{0.scheme}://{0.netloc}".format(urlsplit(url)))
        value = parser.crawl_delay(self.user_agent) if parser else None
        return float(value or 0)
