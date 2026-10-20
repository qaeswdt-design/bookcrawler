"""命令列介面

  python -m bookcrawler 網址 [--name 書名]     抓一本（前景執行，Ctrl+C 暫停）
  python -m bookcrawler                          互動模式：貼網址加入排程，list 看排程，retry 重試失敗的書，q 離開
  python -m bookcrawler --sites                  列出支援的網站
"""

import argparse
import os
import sys
import threading
from pathlib import Path

from . import __version__
from .crawler import crawl_book
from .errors import CrawlerError, StopRequested
from .fetcher import Fetcher
from .scheduler import Scheduler
from .sites import all_adapters, find_adapter, load_plugins
from .storage import Storage, make_converter

DEFAULT_UA = f"bookcrawler/{__version__} (personal archiving tool; +https://github.com/qaeswdt-design/bookcrawler)"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bookcrawler", description="有禮貌的書籍爬蟲")
    p.add_argument("url", nargs="?", help="目錄頁或任一章的網址；省略則進入互動模式")
    p.add_argument("--name", help="輸出檔名（預設用網頁上的書名）")
    p.add_argument("--out", default="output", help="輸出資料夾（預設 output）")
    p.add_argument("--state", default="state", help="進度與排程資料夾（預設 state）")
    p.add_argument("--convert", metavar="CONFIG",
                   help="OpenCC 簡繁轉換設定，例如 s2twp（需安裝 opencc）")
    p.add_argument("--delay", type=float, nargs=2, default=(5.0, 10.0), metavar=("MIN", "MAX"),
                   help="每次請求間隔秒數範圍（預設 5 10，且不少於 robots.txt 的 Crawl-delay）")
    p.add_argument("--max-chapters", type=int, help="這次最多抓幾章")
    p.add_argument("--user-agent", default=os.environ.get("BOOKCRAWLER_UA", DEFAULT_UA),
                   help="User-Agent，建議附上聯絡方式（也可設環境變數 BOOKCRAWLER_UA）")
    p.add_argument("--plugins", default="local_sites",
                   help="額外轉接器資料夾（預設 local_sites，不存在就略過）")
    p.add_argument("--sites", action="store_true", help="列出支援的網站後離開")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):   # Windows 主控台預設 cp950，印不出部分中文字
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    args = build_parser().parse_args(argv)
    if Path(args.plugins).is_dir():
        loaded = load_plugins(Path(args.plugins))
        if loaded:
            print(f"已載入額外轉接器：{', '.join(loaded)}")

    if args.sites:
        for a in all_adapters():
            print(f"{a.name:<15} {', '.join(a.domains)}")
        return 0

    try:
        convert = make_converter(args.convert)
    except RuntimeError as e:
        print(e)
        return 2

    stop_event = threading.Event()
    fetcher = Fetcher(args.user_agent, delay=tuple(args.delay), stop_event=stop_event)
    storage = Storage(Path(args.out), Path(args.state))

    def run(url: str, name: str = "") -> bool:
        adapter = find_adapter(url)
        if adapter is None:
            raise ValueError(f"不支援這個網站（用 --sites 查看）：{url}")
        result = crawl_book(url, adapter, fetcher, storage, name=name or None,
                            convert=convert, max_chapters=args.max_chapters)
        return result.finished

    if args.url:
        try:
            run(args.url, args.name or "")   # 因 --max-chapters 提早結束也算成功
            return 0
        except KeyboardInterrupt:
            print("\n已暫停，進度已保存，再執行同一個指令就會接續")
            return 130
        except (CrawlerError, ValueError) as e:
            print(e)
            return 1

    return interactive(run, stop_event, Path(args.state) / "queue.json")


def interactive(run, stop_event: threading.Event, queue_file: Path) -> int:
    scheduler = Scheduler(lambda t: run(t["url"], t["name"]), queue_file, stop_event)
    print(__doc__)
    if n := scheduler.restore():
        print(f"已接續上次未完成的排程 {n} 本")
    scheduler.start()
    print("輸入：網址 [書名]　指令：list 查看排程　retry 重試失敗的書　q 離開")

    while True:
        try:
            line = input().strip()
        except (EOFError, KeyboardInterrupt):
            line = "q"
        if not line:
            continue
        if line.lower() == "q":
            print("正在停止，請稍候...")
            scheduler.stop()
            print("已離開，未完成的書下次開啟會自動接續")
            return 0
        if line.lower() == "list":
            items, failed = scheduler.pending(), scheduler.failed()
            print("排程是空的" if not items else "\n".join(
                f"  {i}. {t['name'] or '（自動抓書名）'}  {t['url']}"
                + ("　（失敗，可用 retry 重試）" if t["url"] in failed else "")
                for i, t in enumerate(items, 1)))
            continue
        if line.lower() == "retry":
            n = scheduler.retry_failed()
            print(f"已重新排入 {n} 本" if n else "沒有失敗的書")
            continue
        for one in line.splitlines():   # 一次貼上多行時逐行加入
            parts = one.strip().split(maxsplit=1)
            if not parts:
                continue
            url, name = parts[0], (parts[1].strip() if len(parts) > 1 else "")
            if find_adapter(url) is None:
                print(f"不支援這個網站：{url}")
            elif scheduler.add(url, name):
                print(f"已排入（目前排程 {len(scheduler.pending())} 本）")
            else:
                print("這本已經在排程中，正在排隊")


if __name__ == "__main__":
    sys.exit(main())
