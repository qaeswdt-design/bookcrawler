"""套件內所有例外都繼承 CrawlerError，呼叫端可以一次攔截"""


class CrawlerError(Exception):
    """爬蟲錯誤的共同父類別"""


class FetchError(CrawlerError):
    """網路錯誤或非預期的狀態碼，重試後仍失敗"""


class BlockedError(CrawlerError):
    """網站要求機器人驗證或拒絕存取。重試沒有意義，應該停下來"""


class DisallowedError(CrawlerError):
    """robots.txt 不允許抓這個網址"""


class ParseError(CrawlerError):
    """頁面結構和轉接器預期的不同（網站改版或抓到錯誤頁）"""


class StopRequested(CrawlerError):
    """使用者要求停止"""
