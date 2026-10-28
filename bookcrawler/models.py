from dataclasses import dataclass, field


@dataclass
class ChapterLink:
    """目錄上的一個章節"""
    title: str
    url: str


@dataclass
class Book:
    """目錄頁解析結果"""
    title: str
    url: str
    author: str = ""
    chapters: list[ChapterLink] = field(default_factory=list)


@dataclass
class Chapter:
    """章節頁解析結果"""
    title: str
    paragraphs: list[str]
