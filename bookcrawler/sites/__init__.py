"""網站轉接器註冊表

內建轉接器在這個資料夾；不想公開的轉接器可以放在另外的資料夾，
用 load_plugins() 載入（CLI 預設會載入 local_sites/，該資料夾已列入 .gitignore）。
"""

import importlib.util
import sys
from pathlib import Path

from .base import SiteAdapter

_REGISTRY: list[SiteAdapter] = []


def register(cls: type[SiteAdapter]) -> type[SiteAdapter]:
    """類別裝飾器：@register 之後就能被 find_adapter 找到"""
    if not any(type(a) is cls for a in _REGISTRY):
        _REGISTRY.append(cls())
    return cls


def all_adapters() -> list[SiteAdapter]:
    return list(_REGISTRY)


def find_adapter(url: str) -> SiteAdapter | None:
    for adapter in _REGISTRY:
        if adapter.matches(url):
            return adapter
    return None


def load_plugins(directory: Path) -> list[str]:
    """載入資料夾內每個 .py，回傳載入的模組名稱"""
    loaded = []
    for path in sorted(Path(directory).glob("*.py")):
        if path.name.startswith("_"):
            continue
        mod_name = f"bookcrawler_plugin_{path.stem}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        loaded.append(path.stem)
    return loaded


from . import wikisource  # noqa: E402,F401  內建轉接器，匯入時自動註冊
