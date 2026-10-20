#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信公众号数据看板 —— 线上服务（仅标准库）
- 静态提供 index.html / data.js / vendor/（其余文件不对外）
- GET  /api/state   读取共享状态：{rev, deleted, titles, layout}（回收站/重命名/布局，全设备同步）
- POST /api/auth    管理密码验证（密码只存服务端），成功下发临时 token
- POST /api/state   写入共享状态（需 token）
- POST /api/data    上传数据文件内容（需 token），写入 data.js，全设备生效
监听 PORT 环境变量，绑定 0.0.0.0。管理员密码可用环境变量 ADMIN_PWD 覆盖。
"""

import json
import os
import re
import secrets
import threading
import time
import tempfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "3000"))
# 管理密码只保存在服务端。默认值仅为占位符，部署前必须通过环境变量 ADMIN_PWD 设置。
PWD = os.environ.get("ADMIN_PWD", "change-me")
STATE_PATH = os.path.join(BASE, "state.json")
DATA_PATH = os.path.join(BASE, "data.js")
MAX_BODY = 12 * 1024 * 1024  # 12MB
TOKEN_TTL = 8 * 60 * 60      # 解锁令牌有效期：8 小时

# 页脚版权：通过环境变量 FOOTER_TEXT 设置，例如
#   FOOTER_TEXT="© 2026 你的站名 丨 蜀ICP备XXXXXXXX号"
# 留空则不在页面注入页脚
FOOTER_TEXT = os.environ.get("FOOTER_TEXT", "")
FOOTER_HTML = (
    '<footer style="text-align:center;padding:16px 0 20px;'
    'font-size:12px;line-height:1.6;color:#9aa0a6;letter-spacing:.2px">'
    + FOOTER_TEXT + "</footer>"
) if FOOTER_TEXT else ""

_TOKENS = {}                 # token -> 过期时间戳
_TOKEN_LOCK = threading.Lock()


def issue_token():
    tok = secrets.token_hex(24)
    with _TOKEN_LOCK:
        now = time.time()
        # 顺带清理过期 token
        for k in [k for k, exp in _TOKENS.items() if exp < now]:
            _TOKENS.pop(k, None)
        _TOKENS[tok] = now + TOKEN_TTL
    return tok


def check_token(tok):
    if not isinstance(tok, str) or not tok:
        return False
    with _TOKEN_LOCK:
        exp = _TOKENS.get(tok)
    return bool(exp) and exp > time.time()


def revoke_token(tok):
    with _TOKEN_LOCK:
        _TOKENS.pop(tok, None)

STATIC_ALLOWED = {
    "/": "index.html",
    "/index.html": "index.html",
    "/data.js": "data.js",
    "/vendor/echarts.min.js": "vendor/echarts.min.js",
    "/vendor/xlsx.full.min.js": "vendor/xlsx.full.min.js",
}
CONTENT_TYPE = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8"}


def read_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            j = json.load(f)
        if isinstance(j, dict) and isinstance(j.get("rev"), (int, float)):
            if not isinstance(j.get("deleted"), dict):
                j["deleted"] = {}
            if not isinstance(j.get("titles"), dict):
                j["titles"] = {}
            if "layout" not in j:
                j["layout"] = None
            return j
    except Exception:
        pass
    return {"rev": 0, "deleted": {}, "titles": {}, "layout": None}


def valid_layout(v):
    """布局：None 表示恢复系统默认；对象需含合法的 order / span。"""
    if v is None:
        return True
    if not isinstance(v, dict):
        return False
    order = v.get("order")
    if not isinstance(order, list) or not order:
        return False
    if any(not isinstance(x, str) for x in order):
        return False
    span = v.get("span")
    if span is not None and not isinstance(span, dict):
        return False
    heights = v.get("heights")
    if heights is not None and not isinstance(heights, dict):
        return False
    return True


_GEN_RE = re.compile(r'window\.DATA_GENERATED_AT\s*=\s*"([^"]*)"')


def data_meta():
    """读取 data.js 的轻量元信息（生成时间/大小/更新时间），供各端检测是否有新数据。"""
    try:
        st = os.stat(DATA_PATH)
        with open(DATA_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 8192))
            tail = f.read().decode("utf-8", "replace")
        m = _GEN_RE.search(tail)
        return {"ok": True, "generated_at": m.group(1) if m else "",
                "size": st.st_size, "updated_at": int(st.st_mtime * 1000)}
    except Exception:
        return {"ok": True, "generated_at": "", "size": 0, "updated_at": 0}


class _Win(object):
    pass


def load_data_dict():
    """解析 data.js，返回完整数据字典（供需要回拉数据的端使用）。"""
    w = _Win()
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            src = f.read()
        # data.js 为 JSON 直出，可能含 false/true/null，需映射为 Python 字面量
        exec(compile(src, "data.js", "exec"),
             {"window": w, "false": False, "true": True, "null": None})
    except Exception:
        return None
    return getattr(w, "DATA", None)


def write_json_atomic(path, obj):
    fd, tmp = tempfile.mkstemp(dir=BASE, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def valid_data(d):
    return (
        isinstance(d, dict)
        and isinstance(d.get("articles"), dict)
        and isinstance(d.get("article_daily"), dict)
        and isinstance(d.get("biz_daily"), dict)
        and isinstance(d.get("user_daily"), dict)
        and bool(d.get("generated_at"))
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静默访问日志
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel):
        fp = os.path.join(BASE, rel)
        try:
            with open(fp, "rb") as f:
                body = f.read()
        except OSError:
            self._json({"ok": False, "message": "not found"}, code=404)
            return
        # 设置了 FOOTER_TEXT 时，在网页底部注入版权页脚
        if rel.endswith(".html") and FOOTER_HTML and b"</body>" in body:
            body = body.replace(b"</body>", FOOTER_HTML.encode("utf-8") + b"</body>", 1)
        ext = os.path.splitext(fp)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPE.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        # 数据与页面不缓存，保证各端拿到最新；库文件可缓存
        if rel.startswith("vendor/"):
            self.send_header("Cache-Control", "public, max-age=86400")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            self._json(read_state())
            return
        if path == "/api/data":
            self._json(data_meta())
            return
        if path == "/api/data/full":
            d = load_data_dict()
            if not isinstance(d, dict):
                self._json({"ok": False, "message": "数据文件不可用"}, code=500)
            else:
                self._json({"ok": True, "generated_at": d.get("generated_at", ""), "data": d})
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in STATIC_ALLOWED:
            self._static(STATIC_ALLOWED[path])
            return
        self._json({"ok": False, "message": "not found"}, code=404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._json({"ok": False, "message": "请求体无效或过大"}, code=400)
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._json({"ok": False, "message": "请求不是有效的 JSON"}, code=400)
            return
        if not isinstance(body, dict):
            self._json({"ok": False, "message": "请求格式错误"}, code=400)
            return

        # 管理密码验证：密码只在这里比对，成功下发临时 token
        if path == "/api/auth":
            if body.get("pwd") != PWD:
                time.sleep(0.4)   # 轻微延迟，增加暴力尝试成本
                self._json({"ok": False, "message": "密码不正确"}, code=403)
                return
            tok = issue_token()
            self._json({"ok": True, "token": tok, "expires_in": TOKEN_TTL})
            return
        if path == "/api/logout":
            revoke_token(body.get("token"))
            self._json({"ok": True})
            return

        if path in ("/api/state", "/api/data"):
            if not check_token(body.get("token")):
                self._json({"ok": False, "message": "未解锁或登录已过期，请重新验证"}, code=403)
                return
            if path == "/api/state":
                rev = body.get("rev")
                deleted = body.get("deleted")
                titles = body.get("titles")
                if not isinstance(rev, (int, float)) or not isinstance(deleted, dict) or not isinstance(titles, dict):
                    self._json({"ok": False, "message": "缺少 rev/deleted/titles 或格式错误"}, code=400)
                    return
                cur = read_state()
                # 布局：本次未传则保持原值；传 null 表示恢复系统默认布局
                layout = cur.get("layout")
                if "layout" in body:
                    lv = body.get("layout")
                    if not valid_layout(lv):
                        self._json({"ok": False, "message": "layout 格式错误"}, code=400)
                        return
                    layout = lv if isinstance(lv, dict) else None
                now_ms = int(time.time() * 1000)
                # 防止异常大的 rev 把状态“锁死”在将来，导致后续同步全部被判为旧数据
                if rev > now_ms + 60 * 60 * 1000:
                    rev = now_ms
                if rev < cur["rev"]:
                    # 旧数据不覆盖新数据，直接返回当前状态
                    self._json({"ok": True, "rev": cur["rev"], "stale": True})
                    return
                write_json_atomic(STATE_PATH, {"rev": rev, "deleted": deleted,
                                               "titles": titles, "layout": layout})
                self._json({"ok": True, "rev": rev, "has_layout": layout is not None})
                return
            data = body.get("data")
            gen = body.get("generated_at") or (data.get("generated_at") if isinstance(data, dict) else "")
            if not valid_data(data):
                self._json({"ok": False, "message": "数据校验未通过（缺少必要字段）"}, code=400)
                return
            if gen:
                data["generated_at"] = gen
            text = "window.DATA = " + json.dumps(data, ensure_ascii=False) + ";\n" \
                   + 'window.DATA_GENERATED_AT = "' + str(gen) + '";\n'
            fd, tmp = tempfile.mkstemp(dir=BASE, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                os.replace(tmp, DATA_PATH)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                self._json({"ok": False, "message": "写入数据文件失败"}, code=500)
                return
            self._json({"ok": True, "articles": len(data["articles"])})
            return

        self._json({"ok": False, "message": "not found"}, code=404)


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("看板服务已启动，端口 %d" % PORT, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
