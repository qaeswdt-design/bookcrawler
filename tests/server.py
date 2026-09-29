"""測試用的本機 HTTP 伺服器：依路徑回傳預先設定好的回應，並記錄每個路徑被請求幾次"""

import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote


class FakeSite:
    def __init__(self):
        self.routes: dict[str, list[tuple[int, dict, bytes]]] = {}
        self.hits: Counter[str] = Counter()
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = unquote(self.path)
                site.hits[path] += 1
                responses = site.routes.get(path)
                if not responses:
                    status, headers, body = 404, {}, b"not found"
                elif len(responses) > 1:
                    status, headers, body = responses.pop(0)   # 依序回應，最後一個一直重複
                else:
                    status, headers, body = responses[0]
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def route(self, path: str, *responses):
        """每個回應是 body 字串，或 (status, headers, body) tuple"""
        normalized = []
        for r in responses:
            if isinstance(r, str):
                r = (200, {"Content-Type": "text/html; charset=utf-8"}, r)
            status, headers, body = r
            normalized.append((status, headers, body.encode("utf-8") if isinstance(body, str) else body))
        self.routes[path] = normalized

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
