"""存檔與進度

進度檔會記錄輸出檔案的位元組長度。續抓時先把輸出檔截回這個長度，
所以就算程式在「寫入章節」和「更新進度」之間當掉，也不會出現重複或寫一半的章節。
"""

import json
import os
import re
from pathlib import Path
from typing import Callable

from .models import Chapter

Converter = Callable[[str], str]


def safe_filename(name: str, max_len: int = 80) -> str:
    """Windows 檔名不能有 \\ / : * ? " < > | 和控制字元，也不能以空白或句點結尾"""
    name = re.sub(r"\s+", " ", name)   # 換行、tab 先變成空白
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return name[:max_len].rstrip(" .") or "未命名"


def load_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data):
    """先寫暫存檔再替換，避免寫到一半當掉留下壞掉的 JSON"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def make_converter(config: str | None) -> Converter:
    """回傳簡繁轉換函式；config 例如 's2twp'（簡體→台灣正體＋慣用詞）。None 表示不轉換"""
    if not config:
        return lambda text: text
    try:
        from opencc import OpenCC
    except ImportError:
        raise RuntimeError("要轉換簡繁需要安裝 opencc：pip install opencc") from None
    cc = OpenCC(config)
    return cc.convert


class Storage:
    def __init__(self, out_dir: Path, state_dir: Path):
        self.out_dir = Path(out_dir)
        self.state_dir = Path(state_dir)

    def progress_path(self, site: str, book_key: str) -> Path:
        return self.state_dir / f"progress_{safe_filename(site)}_{safe_filename(book_key)}.json"

    def load_progress(self, site: str, book_key: str) -> dict:
        return load_json(self.progress_path(site, book_key), {})

    def save_progress(self, site: str, book_key: str, progress: dict):
        save_json(self.progress_path(site, book_key), progress)

    def output_path(self, title: str) -> Path:
        return self.out_dir / f"{safe_filename(title)}.txt"

    def prepare_output(self, path: Path, size: int):
        """把輸出檔截回上次成功存檔時的長度（新書則建立空檔）"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "ab") as f:
            f.truncate(size)

    def append_chapter(self, path: Path, chapter: Chapter, convert: Converter) -> int:
        """寫入一章，回傳寫入後的檔案長度"""
        lines = [chapter.title, ""]
        for para in chapter.paragraphs:
            lines += [para, ""]
        text = convert("\n".join(lines) + "\n")
        with open(path, "ab") as f:
            f.write(text.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
            return f.tell()
