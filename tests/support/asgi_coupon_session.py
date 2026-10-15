"""离线 HTTP 协议测试传输：真实 FastAPI 路由/SQLite，不绑定网络端口。"""

import asyncio
import json
from urllib.parse import urlsplit, urlencode

import requests

class ASGICouponSession(requests.Session):
    def __init__(self):
        super().__init__()
        from coupon_api.database import initialize_database
        initialize_database()

    def request(self, method, url, **kwargs):
        from coupon_api.main import app
        parsed = urlsplit(url)
        assert parsed.scheme == "http" and parsed.netloc == "127.0.0.1:8000"
        assert kwargs.get("allow_redirects", False) is False
        payload = json.dumps(kwargs["json"]).encode() if "json" in kwargs else b""
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
                 "scheme": "http", "path": parsed.path, "raw_path": parsed.path.encode(),
                 "query_string": urlencode(kwargs.get("params") or {}).encode(), "root_path": "",
                 "headers": [(b"content-type", b"application/json")], "server": ("127.0.0.1", 8000),
                 "client": ("127.0.0.1", 12345)}
        messages = []
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        asyncio.run(app(scope, receive, send))
        response = requests.Response()
        response.status_code = next(m["status"] for m in messages if m["type"] == "http.response.start")
        response._content = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        response.url = url
        return response
