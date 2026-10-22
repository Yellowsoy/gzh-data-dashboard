#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信公众号数据看板 - 本地服务（v5，仅标准库）
- 静态提供 index.html / data.js（config.json、token.json、store.json 不对外）
- POST /api/sync        手动更新：子进程运行 sync.py（单航班，不会并发重入）
- GET  /api/status      更新任务状态 + 日志尾部 + 数据概况
- POST /api/delete      软删除文章 {ids:[...]}（进回收站，可恢复）
- POST /api/restore     从回收站恢复 {ids:[...]}
- POST /api/purge       彻底删除 {ids:[...]}（物理移除，不可恢复）
- POST /api/rename      手动命名 {id, title}（存 store.json，全设备生效）
- POST /api/unrename    取消手动命名 {id}
- GET  /api/config      config.json 是否已配置（不返回任何密钥内容）
- POST /api/data        上传数据文件内容（需 pwd），写入 data.js 并同步到线上
- POST /api/push        手动把本机数据同步到线上站点（需 pwd）
- GET  /api/upstream    线上同步配置与最近一次同步结果
- POST /api/upstream    修改线上同步配置（需 pwd）

用法：
    python3 serve.py [--port 8000] [--no-browser]
"""

import os
import sys
import json
import time
import signal
import atexit
import secrets
import socket
import threading
import tempfile
import subprocess
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import sync  # noqa: E402  复用 store 读写 / data.js 生成逻辑

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STORE_PATH = os.path.join(BASE_DIR, "store.json")
LOG_PATH = os.path.join(BASE_DIR, "sync.log")
PID_PATH = os.path.join(BASE_DIR, "serve.pid")

# 写操作密码（删除/还原/彻底删除/重命名/采集更新）。内网开放后，接口同样需要密码。
# 页面不再保存密码：POST /api/auth 由本服务比对后下发临时 token，写操作凭 token 放行。
ACCESS_PWD = os.environ.get("ADMIN_PWD", "change-me")
TOKEN_TTL = 8 * 60 * 60
_TOKENS = {}

def _issue_token():
    tok = secrets.token_hex(24)
    _TOKENS[tok] = time.time() + TOKEN_TTL
    return tok

def _check_token(tok):
    if not isinstance(tok, str) or not tok:
        return False
    exp = _TOKENS.get(tok)
    return bool(exp) and exp > time.time()

# 不对外提供的文件（含密钥/中间态/脚本自身）
BLOCKED_NAMES = {"config.json", "token.json", "store.json", "sync.py",
                 "serve.py", "run.sh", "stop.sh", "install.sh", "sync.log",
                 "store.json.tmp", "data.js.tmp", "serve.pid"}
SAFE_EXTS = {".html", ".js", ".css", ".svg", ".png", ".jpg", ".jpeg",
             ".webp", ".ico", ".woff", ".woff2", ".txt", ".md"}

# ----------------------------------------------------------------------
# 更新任务（单航班）
# ----------------------------------------------------------------------
_job_lock = threading.Lock()
_job = {
    "running": False,
    "started_at": "",
    "finished_at": "",
    "returncode": None,
    "error": "",
}


def _job_snapshot():
    with _job_lock:
        return dict(_job)


def _read_log_tail(n=4000):
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - n))
            return f.read()
    except Exception:
        return ""


LOG_MAX_BYTES = 2 * 1024 * 1024   # sync.log 超过 2MB 触发滚动
LOG_KEEP_LINES = 3000             # 滚动后保留最近 3000 行


def _rotate_log():
    """sync.log 过大时截断到最近 N 行（本地桌面长期部署，避免日志无限增长）。"""
    try:
        if not os.path.isfile(LOG_PATH) \
                or os.path.getsize(LOG_PATH) < LOG_MAX_BYTES:
            return
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("===== 日志滚动 %s（保留最近 %d 行）=====\n"
                    % (_now(), LOG_KEEP_LINES))
            f.writelines(lines[-LOG_KEEP_LINES:])
    except Exception:
        pass


def _sync_worker():
    """后台线程：子进程跑 sync.py，全程记录日志。"""
    try:
        _rotate_log()
        with open(LOG_PATH, "a", encoding="utf-8") as lf:
            lf.write("\n===== 手动更新 %s =====\n" % _now())
            lf.flush()
            proc = subprocess.Popen(
                [sys.executable, os.path.join(BASE_DIR, "sync.py")],
                stdout=lf, stderr=subprocess.STDOUT, cwd=BASE_DIR)
            rc = proc.wait()
        # 采集成功后自动把新数据同步到线上（可关：config.json 的 upstream_auto_push = false）
        if rc == 0:
            try:
                if _upstream_conf()["auto"]:
                    push_to_upstream("采集完成")
            except Exception as e:
                _log_push("自动同步异常：%s" % e)
        with _job_lock:
            _job.update({"running": False, "finished_at": _now(),
                         "returncode": rc})
    except Exception as e:  # 进程启动失败等
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as lf:
                lf.write("更新任务异常：%s\n" % e)
        except Exception:
            pass
        with _job_lock:
            _job.update({"running": False, "finished_at": _now(),
                         "returncode": -1, "error": str(e)})


def _run_sync_job():
    """触发一次更新（单航班）。已在运行时返回 False。"""
    with _job_lock:
        if _job["running"]:
            return False
        _job.update({"running": True, "started_at": _now(),
                     "finished_at": "", "returncode": None, "error": ""})
    threading.Thread(target=_sync_worker, daemon=True).start()
    return True


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------
# 数据变更（删除 / 恢复 / 彻底删除 / 命名）
# ----------------------------------------------------------------------
def _load_store():
    store = sync.upgrade_store(sync.load_json(STORE_PATH, sync.new_store()))
    return store


def _save_and_refresh(store):
    store["generated_at"] = _now()
    sync.save_json(STORE_PATH, store)
    sync.write_data_js(store)


def api_delete(ids):
    store = _load_store()
    done, already, unknown = [], [], []
    for m in ids:
        if not m:
            continue
        if m in store["deleted"]:
            already.append(m)
        elif m in store["articles"] or m in store["article_total"] \
                or any(m in rows for rows in store["article_daily"].values()):
            rec = sync.snapshot_article(store, m)
            rec["deleted_at"] = _now()
            store["deleted"][m] = rec
            done.append(m)
        else:
            unknown.append(m)
    _save_and_refresh(store)
    _push_state_async("删除文章")
    return {"ok": True, "deleted": done, "already": already,
            "unknown": unknown}


def api_restore(ids):
    store = _load_store()
    done = []
    for m in ids:
        if m in store["deleted"]:
            del store["deleted"][m]
            done.append(m)
    _save_and_refresh(store)
    _push_state_async("恢复文章")
    return {"ok": True, "restored": done}


def api_purge(ids):
    store = _load_store()
    done = []
    for m in ids:
        hit = False
        store["deleted"].pop(m, None)
        store["manual_titles"].pop(m, None)
        if m in store["articles"]:
            del store["articles"][m]
            hit = True
        if m in store["article_total"]:
            del store["article_total"][m]
            hit = True
        for sd in list(store["article_daily"].keys()):
            if m in store["article_daily"][sd]:
                del store["article_daily"][sd][m]
                hit = True
        if hit:
            done.append(m)
    _save_and_refresh(store)
    _push_state_async("彻底删除")
    return {"ok": True, "purged": done}


def api_rename(mid, title):
    store = _load_store()
    title = (title or "").strip()
    if title:
        store["manual_titles"][mid] = title[:120]
    else:
        store["manual_titles"].pop(mid, None)
    _save_and_refresh(store)
    _push_state_async("重命名")
    return {"ok": True, "title": title}


# ----------------------------------------------------------------------
# 本机 → 线上 同步（数据与回收站状态推送到线上站点，线上所有页面随之更新）
# ----------------------------------------------------------------------
# 线上站点地址：留空则不同步；在 config.json 的 upstream 填入你自己的线上地址
# 若线上同时提供「自定义域名 + 平台预设域名」两套入口，可把预设域名写进 UPSTREAM_FALLBACKS，
# 主域名不可达时会自动回退，保证同步不中断。
# 典型配置（EdgeOne Pages）：
#   DEFAULT_UPSTREAM = "https://你的域名.example.com"
#   UPSTREAM_FALLBACKS = ["https://你的项目名.edgeone.cool"]
DEFAULT_UPSTREAM = ""
UPSTREAM_FALLBACKS = []

_push_lock = threading.Lock()
_push_state = {"running": False, "last_at": "", "last_ok": None, "last_message": ""}
_PULL_STATE = {"last_at": "", "last_ok": None, "last_message": ""}


def _upstream_conf():
    """读取线上同步配置；未配置时使用默认线上地址，默认自动同步。"""
    try:
        cfg = sync.load_json(CONFIG_PATH, {}) or {}
    except Exception:
        cfg = {}
    url = str(cfg.get("upstream") or DEFAULT_UPSTREAM).strip().rstrip("/")
    pwd = str(cfg.get("upstream_pwd") or ACCESS_PWD).strip()
    auto = cfg.get("upstream_auto_push")
    return {"url": url, "pwd": pwd, "auto": True if auto is None else bool(auto)}


def load_data_dict():
    """解析本机 data.js，返回数据字典。"""
    class _W(object):
        pass
    w = _W()
    try:
        with open(os.path.join(BASE_DIR, "data.js"), "r", encoding="utf-8") as f:
            src = f.read()
        # data.js 为 JSON 直出，可能含 false/true/null，需映射为 Python 字面量
        exec(compile(src, "data.js", "exec"),
             {"window": w, "false": False, "true": True, "null": None})
    except Exception:
        return None
    return getattr(w, "DATA", None)


def write_data_js_from_upload(data, gen=""):
    """把上传的数据直接写成本机 data.js（覆盖），返回生成时间。"""
    gen = gen or data.get("generated_at") or _now()
    data = dict(data)
    data["generated_at"] = gen
    text = ("window.DATA = " + json.dumps(data, ensure_ascii=False) + ";\n"
            + 'window.DATA_GENERATED_AT = "' + str(gen) + '";\n')
    fd, tmp = tempfile.mkstemp(dir=BASE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, os.path.join(BASE_DIR, "data.js"))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return gen


def _http_post_json(url, obj, timeout=60):
    req = Request(url, data=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except HTTPError as e:
        # 线上鉴权失败/令牌过期会返回 403/401，仍需解析响应体，否则上层无法判断回退
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        try:
            j = json.loads(raw)
            if isinstance(j, dict):
                j.setdefault("ok", False)
                return j
        except Exception:
            pass
        return {"ok": False, "message": "HTTP %s" % getattr(e, "code", "错误")}
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": False, "message": "响应解析失败"}


def _log_push(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as lf:
            lf.write("%s  [上行同步] %s\n" % (_now(), msg))
    except Exception:
        pass


# 线上 token 缓存：推送前用线上密码换取临时 token，过期自动刷新
_UP_TOKEN_CACHE = {"key": "", "token": "", "exp": 0, "base": ""}


def _upstream_candidates(conf):
    """上行候选地址：主用地址 +（仅当使用默认地址时）备用域名。"""
    url = conf["url"]
    if not url:
        return []
    cands = [url]
    if url == DEFAULT_UPSTREAM:
        for u in UPSTREAM_FALLBACKS:
            if u and u not in cands:
                cands.append(u)
    return cands


def _upstream_token(conf):
    """向线上换取临时令牌；主用地址不可用时自动回退到备用域名。"""
    key = conf["url"] + "|" + conf["pwd"]
    now = time.time()
    if (_UP_TOKEN_CACHE["key"] == key and _UP_TOKEN_CACHE["exp"] > now + 60
            and _UP_TOKEN_CACHE["base"]):
        return _UP_TOKEN_CACHE["token"]

    errs = []
    for base in _upstream_candidates(conf):
        try:
            j = _http_post_json(base + "/api/auth",
                                {"pwd": conf["pwd"]}, timeout=30)
        except (URLError, OSError) as e:
            errs.append("%s 不可达（%s）" % (base, str(e)[:60]))
            continue
        if j.get("ok") and j.get("token"):
            ttl = int(j.get("expires_in") or 28800)
            # 兼容线上误把毫秒当秒下发的情况，避免本端缓存过期时间过长
            if ttl > 7 * 24 * 3600:
                ttl = ttl // 1000
            _UP_TOKEN_CACHE.update({"key": key, "token": j["token"],
                                    "exp": now + ttl, "base": base})
            return j["token"]
        errs.append("%s：%s" % (base, j.get("message") or "密码校验失败"))
    raise RuntimeError("线上密码验证失败：" + "；".join(errs))


def _up_post(conf, api_path, payload, timeout=30):
    """上行请求：凭临时 token 调线上接口；token 失效自动刷新重试一次。"""
    payload = dict(payload)
    payload.pop("pwd", None)
    payload["token"] = _upstream_token(conf)
    base = _UP_TOKEN_CACHE.get("base") or conf["url"]
    j = _http_post_json(base + api_path, payload, timeout=timeout)
    if not j.get("ok"):
        msg = str(j.get("message") or "")
        if "过期" in msg or "未解锁" in msg:
            _UP_TOKEN_CACHE["exp"] = 0
            payload["token"] = _upstream_token(conf)
            base = _UP_TOKEN_CACHE.get("base") or conf["url"]
            j = _http_post_json(base + api_path, payload,
                                timeout=timeout)
    return j


def upstream_snapshot():
    conf = _upstream_conf()
    return {
        "configured": bool(conf["url"]),
        "url": conf["url"],
        "active_url": (_UP_TOKEN_CACHE.get("base") or "")
        if _UP_TOKEN_CACHE.get("key") == conf["url"] + "|" + conf["pwd"] else "",
        "auto_push": conf["auto"],
        "running": _push_state.get("running", False),
        "last_push_at": _push_state.get("last_at", ""),
        "last_push_ok": _push_state.get("last_ok", None),
        "last_push_message": _push_state.get("last_message", ""),
        "last_pull_at": _PULL_STATE.get("last_at", ""),
        "last_pull_ok": _PULL_STATE.get("last_ok", None),
        "last_pull_message": _PULL_STATE.get("last_message", ""),
    }


def push_to_upstream(reason="", with_data=True, with_state=True):
    """把本机数据与回收站状态推送到线上站点。返回 (是否全部成功, 说明)。"""
    with _push_lock:
        if _push_state["running"]:
            return False, "上一次同步尚未结束"
        _push_state["running"] = True
    try:
        conf = _upstream_conf()
        if not conf["url"]:
            return False, "未配置线上地址"
        msgs, ok_all = [], True
        if with_data:
            data = load_data_dict()
            if not data:
                ok_all = False
                msgs.append("读取本机 data.js 失败")
            else:
                try:
                    j = _up_post(conf, "/api/data", {
                        "data": data,
                        "generated_at": data.get("generated_at", ""),
                    }, timeout=180)
                    if j.get("ok"):
                        msgs.append("数据已同步（%d 篇）" % len(data.get("articles", {}) or {}))
                    else:
                        ok_all = False
                        msgs.append("数据同步失败：%s" % (j.get("message") or "未知原因"))
                except Exception as e:
                    ok_all = False
                    msgs.append("数据同步失败：%s" % e)
        if with_state:
            try:
                store = sync.load_json(STORE_PATH, {}) or {}
                deleted = {}
                for k, v in (store.get("deleted") or {}).items():
                    v = v or {}
                    deleted[str(k)] = {"title": v.get("title", ""),
                                       "deleted_at": v.get("deleted_at", "")}
                rev_sent = int(time.time() * 1000)
                j = _up_post(conf, "/api/state", {
                    "rev": rev_sent,
                    "deleted": deleted,
                    "titles": store.get("manual_titles") or {},
                }, timeout=30)
                if j.get("ok"):
                    msgs.append("回收站状态已同步")
                    # 记录本次成功推送的 rev：从线上拉回时若线上 rev 不超过它，
                    # 说明线上没有更新的改动（或推送曾失败），合并需保留本机独有条目
                    try:
                        store["_last_push_rev"] = int(j.get("rev") or rev_sent)
                        sync.save_json(STORE_PATH, store)
                    except Exception:
                        pass
                else:
                    msgs.append("回收站状态同步失败：%s" % (j.get("message") or "未知原因"))
            except Exception as e:
                msgs.append("回收站状态同步失败：%s" % e)
        msg = "；".join(msgs) or "无内容"
        _push_state.update({"last_at": _now(), "last_ok": ok_all, "last_message": msg})
        _log_push("%s → %s（%s）" % (reason or "手动",
                                     _UP_TOKEN_CACHE.get("base") or conf["url"],
                                     msg))
        return ok_all, msg
    finally:
        with _push_lock:
            _push_state["running"] = False


def _log_pull(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as lf:
            lf.write("%s  [下行拉取] %s\n" % (_now(), msg))
    except Exception:
        pass


def _http_get_json(url, timeout=30):
    """GET 一个 JSON 接口；非 2xx 或响应不是 JSON 时抛异常，由调用方决定回退。"""
    req = Request(url, method="GET")
    with urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw)


def pull_from_upstream(reason="页面拉取"):
    """线上 → 本机：把云端的 删除/重命名/布局 状态拉回，合并进 store.json 并刷新 data.js。

    背景：线上看板（cloudMode）的删除/恢复/重命名/布局只写入云端 Blob，
    本机 store.json 原先只能推、不能拉，导致线上改动本机看不到，
    且下次推送还会用本机旧状态覆盖线上。

    合并规则（按 rev 水位判断，兼顾「云端权威」与「本机未推送改动不丢」）：
    - 线上 rev > 本机 _last_push_rev：线上有更新改动，整体采用线上的
      deleted/titles（线上做的删除、恢复、重命名都会带回本机）
    - 线上 rev <= 本机 _last_push_rev：线上不超过本机最后一次成功推送，
      以上次推送后的线上状态为基底，补回本机独有的 deleted/manual_titles
      条目（推送失败期间的本机改动不被覆盖丢失）

    布局随状态一起返回，由页面决定是否采用（线上为空时不清空本机布局，避免误伤）。
    返回 (ok, message, state)；无实质变化时不落盘、不重写 data.js。
    """
    conf = _upstream_conf()
    if not conf["url"]:
        return False, "未配置线上地址", None
    up, errs = None, []
    for base in _upstream_candidates(conf):
        try:
            j = _http_get_json(base + "/api/state", timeout=30)
        except Exception as e:
            errs.append("%s 不可读（%s）" % (base, str(e)[:60]))
            continue
        if (isinstance(j, dict) and isinstance(j.get("rev"), (int, float))
                and isinstance(j.get("deleted"), dict)
                and isinstance(j.get("titles"), dict)):
            up = j
            break
        errs.append("%s 返回格式异常" % base)
    if up is None:
        msg = "拉取失败：" + "；".join(errs or ["线上无响应"])
        _PULL_STATE.update({"last_at": _now(), "last_ok": False, "last_message": msg})
        return False, msg, None

    store = _load_store()
    try:
        local_rev = int(store.get("_last_push_rev") or 0)
    except Exception:
        local_rev = 0
    up_rev = int(up.get("rev") or 0)
    up_deleted = up.get("deleted") or {}
    up_titles = up.get("titles") or {}

    if up_rev > local_rev:
        deleted, titles = dict(up_deleted), dict(up_titles)
        mode = "采用线上状态"
    else:
        deleted = dict(up_deleted)
        for k, v in (store.get("deleted") or {}).items():
            deleted.setdefault(k, v)
        titles = dict(up_titles)
        for k, v in (store.get("manual_titles") or {}).items():
            titles.setdefault(k, v)
        mode = "合并（保留本机未推送改动）"

    state = {"rev": up_rev, "deleted": deleted, "titles": titles,
             "layout": up.get("layout") or None,
             "added_deleted": 0, "added_titles": 0}
    old_del = store.get("deleted") or {}
    old_tit = store.get("manual_titles") or {}
    if deleted == old_del and titles == old_tit:
        # 无实质变化：不落盘、不重写 data.js，避免每次打开页面都重写大数据文件
        msg = "线上暂无新改动"
        _PULL_STATE.update({"last_at": _now(), "last_ok": True, "last_message": msg})
        return True, msg, state

    state["added_deleted"] = len([k for k in deleted if k not in old_del])
    state["added_titles"] = len([k for k in titles if k not in old_tit])
    store["deleted"] = deleted
    store["manual_titles"] = titles
    store["_last_pull_at"] = _now()
    sync.save_json(STORE_PATH, store)
    sync.write_data_js(store)

    msg = "已从线上拉回（%s）：删除 %d 项、重命名 %d 项" % (mode, len(deleted), len(titles))
    _PULL_STATE.update({"last_at": _now(), "last_ok": True, "last_message": msg})
    _log_pull("%s → %s（%s，删除 %+d、重命名 %+d）"
              % (reason, conf["url"], mode, state["added_deleted"], state["added_titles"]))
    return True, msg, state


def _push_state_async(reason):
    """删除/恢复/彻底删除/命名等轻量变更：后台线程只推回收站状态，不阻塞页面操作。"""
    def _run():
        try:
            if _upstream_conf()["auto"]:
                push_to_upstream(reason, with_data=False, with_state=True)
        except Exception as e:
            _log_push("状态同步异常：%s" % e)
    threading.Thread(target=_run, daemon=True).start()


# ----------------------------------------------------------------------
# HTTP 处理
# ----------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "WechatDashboard/5.0"
    protocol_version = "HTTP/1.1"

    # ---- 工具 ----
    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > 24 * 1024 * 1024:   # 放宽：上传数据文件可能较大
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, fmt, *args):  # 静默默认访问日志
        pass

    # ---- GET ----
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            job = _job_snapshot()
            store = sync.load_json(STORE_PATH, {})
            self._send_json({
                "job": job,
                "log_tail": _read_log_tail(),
                "generated_at": store.get("generated_at", ""),
                "articles": len(store.get("articles", {}) or {}),
                "deleted": len(store.get("deleted", {}) or {}),
                "server_time": _now(),
                "upstream": upstream_snapshot(),
            })
            return
        if path == "/api/upstream":
            self._send_json(upstream_snapshot())
            return
        if path == "/api/config":
            cfg = sync.load_json(CONFIG_PATH, {})
            appid = (cfg.get("appid") or "").strip()
            self._send_json({
                "configured": bool(appid) and not appid.startswith("wx在"),
                "has_appid": bool(appid),
            })
            return
        self._serve_static(path)

    # ---- POST ----
    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()
        # 管理密码验证：密码只在本服务比对，成功下发临时 token
        if path == "/api/auth":
            if (body or {}).get("pwd") != ACCESS_PWD:
                time.sleep(0.4)
                self._send_json({"ok": False, "message": "密码不正确"}, code=403)
                return
            self._send_json({"ok": True, "token": _issue_token(),
                             "expires_in": TOKEN_TTL})
            return
        if path == "/api/logout":
            _TOKENS.pop((body or {}).get("token") or "", None)
            self._send_json({"ok": True})
            return
        # 写操作必须携带服务端下发的临时 token
        if path in ("/api/sync", "/api/delete", "/api/restore",
                    "/api/purge", "/api/rename", "/api/unrename",
                    "/api/data", "/api/push", "/api/pull", "/api/upstream"):
            if not _check_token((body or {}).get("token")):
                self._send_json({"ok": False, "message": "未解锁或登录已过期，请重新验证"}, code=403)
                return
        try:
            if path == "/api/sync":
                started = _run_sync_job()
                if not started:
                    self._send_json({"ok": False, "running": True,
                                     "message": "更新已在进行中"}, code=409)
                else:
                    self._send_json({"ok": True, "started": True}, code=202)
                return
            if path == "/api/delete":
                ids = body.get("ids") or []
                if not isinstance(ids, list) or not ids:
                    self._send_json({"ok": False, "message": "ids 不能为空"},
                                    code=400)
                    return
                self._send_json(api_delete([str(i) for i in ids]))
                return
            if path == "/api/restore":
                ids = body.get("ids") or []
                self._send_json(api_restore([str(i) for i in ids]))
                return
            if path == "/api/purge":
                ids = body.get("ids") or []
                self._send_json(api_purge([str(i) for i in ids]))
                return
            if path == "/api/rename":
                mid = (body.get("id") or "").strip()
                if not mid:
                    self._send_json({"ok": False, "message": "id 不能为空"},
                                    code=400)
                    return
                self._send_json(api_rename(mid, body.get("title") or ""))
                return
            if path == "/api/unrename":
                mid = (body.get("id") or "").strip()
                self._send_json(api_rename(mid, ""))
                return
            if path == "/api/data":
                data = body.get("data")
                ok_shape = isinstance(data, dict) and data.get("articles") \
                    and data.get("article_daily") and data.get("biz_daily") \
                    and data.get("user_daily")
                if not ok_shape:
                    self._send_json({"ok": False,
                                     "message": "数据校验未通过（缺少必要字段）"},
                                    code=400)
                    return
                try:
                    gen = write_data_js_from_upload(data, body.get("generated_at") or "")
                except Exception as e:
                    self._send_json({"ok": False,
                                     "message": "写入数据文件失败：%s" % e}, code=500)
                    return
                pushed, pmsg = None, ""
                try:
                    if _upstream_conf()["auto"]:
                        pushed, pmsg = push_to_upstream("上传数据")
                except Exception as e:
                    pushed, pmsg = False, "同步异常：%s" % e
                self._send_json({"ok": True, "generated_at": gen,
                                 "articles": len(data.get("articles") or {}),
                                 "pushed": pushed, "push_message": pmsg})
                return
            if path == "/api/push":
                ok, msg = push_to_upstream("手动同步")
                self._send_json({"ok": ok, "message": msg,
                                 "push_message": msg,
                                 "pushed_at": _push_state.get("last_at", "")})
                return
            if path == "/api/pull":
                ok, msg, state = pull_from_upstream("页面拉取")
                self._send_json({"ok": ok, "message": msg, "state": state})
                return
            if path == "/api/upstream":
                try:
                    cfg = sync.load_json(CONFIG_PATH, {}) or {}
                except Exception:
                    cfg = {}
                if "upstream" in body:
                    cfg["upstream"] = str(body.get("upstream") or "").strip().rstrip("/")
                if "upstream_pwd" in body:
                    cfg["upstream_pwd"] = str(body.get("upstream_pwd") or "").strip()
                if "upstream_auto_push" in body:
                    cfg["upstream_auto_push"] = bool(body.get("upstream_auto_push"))
                try:
                    sync.save_json(CONFIG_PATH, cfg)
                except Exception as e:
                    self._send_json({"ok": False, "message": "保存配置失败：%s" % e},
                                    code=500)
                    return
                self._send_json({"ok": True, "upstream": upstream_snapshot()})
                return
            self._send_json({"ok": False, "message": "未知接口"}, code=404)
        except Exception as e:
            self._send_json({"ok": False, "message": "服务器错误：%s" % e},
                            code=500)

    # ---- 静态文件 ----
    def _serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        # 防目录穿越
        rel = os.path.normpath(path).lstrip("/\\")
        full = os.path.abspath(os.path.join(BASE_DIR, rel))
        if not full.startswith(BASE_DIR + os.sep) and full != BASE_DIR:
            self._send_json({"ok": False, "message": "forbidden"}, code=403)
            return
        name = os.path.basename(full)
        if name in BLOCKED_NAMES:
            self._send_json({"ok": False, "message": "forbidden"}, code=403)
            return
        ext = os.path.splitext(name)[1].lower()
        if ext not in SAFE_EXTS or not os.path.isfile(full):
            self._send_json({"ok": False, "message": "not found"}, code=404)
            return
        ctype = {".html": "text/html; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".svg": "image/svg+xml",
                 ".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".webp": "image/webp",
                 ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8",
                 ".md": "text/markdown; charset=utf-8",
                 ".woff": "font/woff", ".woff2": "font/woff2"}.get(
                     ext, "application/octet-stream")
        try:
            with open(full, "rb") as f:
                data = f.read()
        except Exception:
            self._send_json({"ok": False, "message": "not found"}, code=404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if ext in (".html", ".js", ".css"):
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


# ----------------------------------------------------------------------
# 本地部署辅助：实例探测 / 端口自适应 / PID / 信号 / 静默断连
# ----------------------------------------------------------------------
def find_existing_instance(port):
    """探测端口上是否已有本服务的实例（GET /api/status 返回 job 字段）。"""
    try:
        with urlopen("http://127.0.0.1:%d/api/status" % port, timeout=1.5) as r:
            if r.status != 200:
                return False
            data = json.loads(r.read().decode("utf-8", "replace"))
            return isinstance(data, dict) and "job" in data
    except Exception:
        return False


def pick_port(preferred, strict=False):
    """首选端口被占用（且非本服务实例）时，顺延尝试 8001-8020。

    strict=True（用户在命令行显式指定 --port）时不顺延：返回 None，
    由调用方明确报错——显式端口被静默更换会破坏用户的书签/预期。
    """
    candidates = [preferred] if strict else \
        [preferred] + [p for p in range(8001, 8021) if p != preferred]
    for p in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
            return p
    return None


def write_pid_file():
    try:
        with open(PID_PATH, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def remove_pid_file():
    try:
        if os.path.isfile(PID_PATH):
            with open(PID_PATH, "r", encoding="utf-8") as f:
                if f.read().strip() == str(os.getpid()):
                    os.remove(PID_PATH)
    except Exception:
        pass


def _open_browser_later(url):
    try:
        import webbrowser
        threading.Thread(target=lambda: (time.sleep(0.8),
                                         webbrowser.open(url)),
                         daemon=True).start()
    except Exception:
        pass


class Server(ThreadingHTTPServer):
    """静默客户端提前断开（页面刷新/关闭时的常见噪音，本地部署无需刷屏）。"""
    daemon_threads = True

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if isinstance(e, (BrokenPipeError, ConnectionResetError,
                          ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def _lan_ip():
    """取本机内网 IP（不会真的发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        s.close()


def main():
    args = sys.argv[1:]
    preferred = 8000
    explicit_port = False
    cfg = sync.load_json(CONFIG_PATH, {})
    cfg_port = cfg.get("serve_port")
    if isinstance(cfg_port, int) and 1024 < cfg_port < 65536:
        preferred = cfg_port
    if "--port" in args:
        try:
            preferred = int(args[args.index("--port") + 1])
            explicit_port = True
        except Exception:
            pass
    # 双击启动器/再次运行 run.sh 时，若已有实例则只打开浏览器，不重复起服务
    if "--no-browser" not in args and find_existing_instance(preferred):
        print("看板已在运行：http://127.0.0.1:%d/（本次仅打开浏览器）" % preferred, flush=True)
        _open_browser_later("http://127.0.0.1:%d/" % preferred)
        time.sleep(2.5)
        return
    port = pick_port(preferred, strict=explicit_port)
    if port is None:
        print("端口 %d 被占用。可换端口：./run.sh --port 8080，"
              "或先停止已运行的服务（./stop.sh）" % preferred, flush=True)
        sys.exit(1)
    if port != preferred:
        print("端口 %d 被其他程序占用，已自动改用 %d。" % (preferred, port), flush=True)

    srv = Server(("0.0.0.0", port), Handler)   # 对内网开放，手机/其他电脑可访问
    url = "http://127.0.0.1:%d/" % port
    lan_ip = _lan_ip()
    if lan_ip:
        print("局域网访问地址（手机/其他电脑连同一 Wi-Fi 后打开）：http://%s:%d/" % (lan_ip, port), flush=True)

    def _shutdown(signum, frame):
        # 在子线程里 shutdown，避免信号回调中直接停造成死锁
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    try:
        signal.signal(signal.SIGHUP, _shutdown)
    except (ValueError, AttributeError, OSError):
        pass

    write_pid_file()
    atexit.register(remove_pid_file)

    print("看板已启动：%s" % url, flush=True)
    print("按 Ctrl+C 或运行 ./stop.sh 停止。"
          "数据更新请使用页面上的「更新数据」按钮。", flush=True)
    if "--no-browser" not in args:
        _open_browser_later(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            srv.server_close()
        except Exception:
            pass
        remove_pid_file()
        print("\n已停止。", flush=True)


if __name__ == "__main__":
    main()
