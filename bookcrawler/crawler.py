"""把一本書從目錄抓到最後一章，支援斷點續抓"""

from dataclasses import dataclass
from pathlib import Path

from .errors import ParseError
from .fetcher import Fetcher
from .models import ChapterLink
from .sites.base import SiteAdapter
from .storage import Converter, Storage


@dataclass
class CrawlResult:
    title: str
    output: Path
    new_chapters: int
    done: int
    total: int

    @property
    def finished(self) -> bool:
        return self.done >= self.total


def _resume_index(chapters: list[ChapterLink], progress: dict, log) -> int:
    """找出要從第幾章繼續。連載小說目錄可能變動，所以用上次最後一章的網址對位"""
    next_index = progress.get("next_index", 0)
    last_url = progress.get("last_url")
    if not last_url:
        return 0
    if 0 < next_index <= len(chapters) and chapters[next_index - 1].url == last_url:
        return next_index
    for i, link in enumerate(chapters):
        if link.url == last_url:
            return i + 1
    log(f"  目錄裡找不到上次抓到的章節（{last_url}），改用記錄的序號 {next_index} 接續")
    return min(next_index, len(chapters))


def _unique_path(path: Path) -> Path:
    n = 2
    candidate = path
    while candidate.exists() and candidate.stat().st_size > 0:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def crawl_book(
    url: str,
    adapter: SiteAdapter,
    fetcher: Fetcher,
    storage: Storage,
    *,
    name: str | None = None,
    convert: Converter = lambda text: text,
    max_chapters: int | None = None,
    log=print,
) -> CrawlResult:
    key = adapter.book_key(url)
    toc_url = adapter.toc_url(url)
    book = adapter.parse_toc(fetcher.get(toc_url), toc_url)
    if not book.chapters:
        raise ParseError(f"目錄裡找不到任何章節：{toc_url}")

    progress = storage.load_progress(adapter.name, key)
    title = name or progress.get("title") or book.title
    total = len(book.chapters)

    if progress.get("output"):
        output = Path(progress["output"])
        size = progress.get("size", 0)
        start = _resume_index(book.chapters, progress, log)
        actual = output.stat().st_size if output.exists() else 0
        if actual < size:
            log(f"  輸出檔 {output} 比進度記錄短（可能被移動或刪除），從頭開始")
            start, size = 0, 0
    else:
        output = _unique_path(storage.output_path(title))   # 不覆蓋別人的同名檔案
        start, size = 0, 0

    storage.prepare_output(output, size)
    if start >= total:
        log(f"《{title}》已經是最新的（{total} 章）")
        return CrawlResult(title, output, 0, start, total)
    log(f"《{title}》{book.author and book.author + ' 著，'}共 {total} 章，從第 {start + 1} 章開始")

    new = 0
    for i in range(start, total):
        if max_chapters is not None and new >= max_chapters:
            break
        link = book.chapters[i]
        chapter = adapter.parse_chapter(fetcher.get(link.url), link.url, link)
        size = storage.append_chapter(output, chapter, convert)
        storage.save_progress(adapter.name, key, {
            "title": title,
            "url": toc_url,
            "output": str(output),
            "next_index": i + 1,
            "last_url": link.url,
            "size": size,
            "total": total,
        })
        new += 1
        log(f"《{title}》{i + 1}/{total} {chapter.title}（{len(chapter.paragraphs)} 段）")

    result = CrawlResult(title, output, new, start + new, total)
    if result.finished:
        log(f"《{title}》全部完成：{output}")
    return result
