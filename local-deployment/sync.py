#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信公众号数据看板 - 本地采集脚本（方案A，新接口版）
- 仅使用 Python 标准库，无需 pip 安装任何依赖
- 自动获取并缓存 access_token（中控，有效期 7200 秒）
- 新口径：仅「阅读人数 UV」，不再提供「阅读次数 PV」（2025-11-01 起）
- 数据来源：
  · getarticletotaldetail  按发表日取标题/链接 + 累计轨迹（还原每日增量）
  · getarticleread / share  按统计日取每篇文章阅读(含来源)/分享
  · getbizsummary           按统计日取账号级汇总（点赞/在看/留言/收藏等）
  · getusersummary / cumulate  粉丝增减 / 累计
- 累积合并到 store.json，生成 data.js 供 index.html 直接读取

标题采集策略（2026-10 修复，v4）：
- 每次运行都扫描最近 article_detail_days 天的「发表日」（拿新文章标题，新的日期优先）；
- 另有一个逐日台账 title_scan_dates，记录每个发表日的扫描结果：
  成功的日期不会重复扫；失败的日期（限流/系统繁忙/网络抖动）下次自动补扫，
  不再使用"只跑一次"的 full_scan_done 标记，避免标题永久缺失；
- 2025-11-01 之前发表的老文章：datacube 不返回其标题（数据从 2025-11-01 起），
  但它们至今仍会被 getarticleread 返回。v3 起用「获取已发布的消息列表」
  （freepublish/batchget，内容接口、无日期限制）补全这些老文章的标题：
  该接口返回的 news_item[].url 中含 mid（图文消息id）与 idx（篇目序号），
  官方约定 msgid = 图文消息id_index，因此 msgid = mid + "_" + idx 可直接对上；
  若 url 是短链（不带 mid/idx），则跟随 302 跳转拿到完整 URL 再解析；
- 45009（日调用量上限，按接口分别计算）：立即停止该接口的剩余扫描（当天重试
  无意义），已扫日期不重复、未扫日期明天自动继续；标题不受影响（发布列表会
  补全），累计轨迹数据会在随后几天自动补齐。也可以在公众平台后台
  「接口权限模板」查看实时调用量，或使用清零操作（每月 10 次）当天跑完；
- 以上接口都拿不到时，看板中显示"未获取到标题"，可点「命名」手动补上（最后兜底）。

删除与命名持久化（2026-10，v5）：
- 文章删除为「软删除」：store.json 的 deleted 台账记录删除时的指标快照，
  data.js 输出时过滤这些文章（重新采集不会让它们复活），回收站中可恢复
  （瞬间还原）或彻底删除（从 store 中物理移除）；
- 手动命名可存到服务端 store.json 的 manual_titles（serve.py 提供接口），
  浏览器 localStorage 作为 file:// 直开时的回退；
- serve.py 提供本地服务：静态看板 + 手动更新（跑本脚本）+ 删除/恢复/彻底删除/
  命名等 API，run.sh 一键启动。

适用：统信 UOS / 任意 Debian 系 Linux（Python 3.6+）
"""

import os
import sys
import re
import json
import time
from datetime import datetime, timedelta

try:
    from urllib.request import Request, urlopen
    from urllib.parse import urlencode, urlparse, parse_qs
    from urllib.error import URLError, HTTPError
except ImportError:
    print("当前 Python 版本过低，请使用 Python 3.6 及以上版本。")
    sys.exit(1)

# ----------------------------------------------------------------------
# 路径
# ----------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")
STORE_PATH = os.path.join(BASE_DIR, "store.json")
DATAJS_PATH = os.path.join(BASE_DIR, "data.js")

API_BASE = os.environ.get("WECHAT_API_BASE", "https://api.weixin.qq.com")

# 按「统计日」逐日采集的接口
DAILY_ENDPOINTS = {
    "article_read": "/datacube/getarticleread",    # 每篇文章当日阅读（含来源）
    "article_share": "/datacube/getarticleshare",  # 每篇文章当日分享
    "biz_summary": "/datacube/getbizsummary",      # 账号级当日汇总
    "user_summary": "/datacube/getusersummary",    # 粉丝增减
    "user_cumulate": "/datacube/getusercumulate",  # 累计粉丝
}

# 按「发表日」采集的接口（含标题/链接与累计轨迹）
ARTICLE_DETAIL_KEY = "article_total_detail"
ARTICLE_DETAIL_ENDPOINT = "/datacube/getarticletotaldetail"

# 获取已发布的消息列表（内容接口，无日期限制）：补全老文章标题的关键来源
PUBLISH_LIST_ENDPOINT = "/cgi-bin/freepublish/batchget"
PUBLISH_PAGE = 20          # 官方分页上限
PUBLISH_MAX_PAGES = 500    # 安全上限（约 1 万条消息）

# 累计口径中可做“每日增量”的指标
ADD_METRICS = [
    "read_user", "share_user", "zaikan_user", "like_user",
    "comment_count", "collection_user", "read_subscribe_user",
]

# 新接口数据起点（早于此日期无数据，且接口对之前日期返回 61501）
EARLIEST_DATE = "2025-11-01"

# 采集时值得重试的“瞬时错误”（系统繁忙 / 频率限制，稍后通常会自己恢复）
TRANSIENT_ERRCODES = (-1, 45011)

# 日调用量上限（官方：reach max api daily quota limit）。
# 配额按接口分别计算、每天 0 点重置；当天重试无意义，应停扫等明天，
# 未完成的日期由 title_scan_dates 台账在下次运行自动续上。
DAILY_QUOTA_ERRCODE = 45009


# 工具
def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg))


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def http_request(url, body=None, timeout=15):
    data = None
    headers = {"Accept": "application/json", "User-Agent": "wechat-dashboard/1.0"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers,
                  method="POST" if body is not None else "GET")

    last_err = None
    for attempt in range(3):
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                last_err = "HTTP %s: %s" % (e.code, e.reason)
        except URLError as e:
            last_err = "网络错误: %s" % e.reason
        except Exception as e:
            last_err = "请求异常: %s" % e
        time.sleep(1.5 * (attempt + 1))
    return {"errcode": -99, "errmsg": last_err or "请求失败"}


# ----------------------------------------------------------------------
# access_token 中控
# ----------------------------------------------------------------------
def fetch_new_token(appid, secret):
    url = API_BASE + "/cgi-bin/token?" + urlencode({
        "grant_type": "client_credential",
        "appid": appid,
        "secret": secret,
    })
    result = http_request(url)
    if result.get("errcode") == 40164 or "not in whitelist" in str(result.get("errmsg", "")):
        msg = result.get("errmsg", "")
        mm = re.search(r"invalid ip\s+([0-9.]+)", msg)
        ip = mm.group(1) if mm else ""
        print("\n================ IP 白名单错误 ================")
        print("微信拒绝了取 token 的请求，因为本机出口 IP 不在白名单。")
        if ip:
            print("本机当前出口公网 IP：%s" % ip)
        print("请登录「微信公众平台 → 设置与开发 → 基本配置 → IP 白名单」")
        print("把上面的 IP 加进去（支持单 IP 或 8.8.8.8/24 这种 IP 段，不支持 8.8.8.* 写法）。")
        print("保存（约 1–2 分钟生效）后重新运行本脚本。")
        print("==============================================\n")
        sys.exit(2)
    if result.get("access_token"):
        token_info = {
            "access_token": result["access_token"],
            "expires_at": int(time.time()) + int(result.get("expires_in", 7200)) - 300,
        }
        save_json(TOKEN_PATH, token_info)
        return token_info["access_token"]
    errcode = result.get("errcode")
    errmsg = result.get("errmsg")
    if errcode in (40013, 40125):
        print("AppID 或 AppSecret 不正确，请核对 config.json。（错误码 %s）" % errcode)
    else:
        print("获取 access_token 失败：%s %s" % (errcode, errmsg))
    sys.exit(2)


def get_access_token(appid, secret, force=False):
    token_info = None if force else load_json(TOKEN_PATH, None)
    if token_info and token_info.get("access_token") and \
            int(time.time()) < int(token_info.get("expires_at", 0)):
        return token_info["access_token"]
    log("access_token 已失效或不存在，重新获取 …")
    return fetch_new_token(appid, secret)


# ----------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------
def new_store():
    return {
        "generated_at": "",
        # 发表日期 -> "ok" 或 "err:<错误码>"：标题扫描台账。
        # 已成功的日期不再重复扫描；失败的日期下次自动补扫（自愈）。
        "title_scan_dates": {},
        # getarticletotaldetail 返回过 48001（无接口权限）时为 true，
        # 此时跳过逐日补扫（重试无意义），加 --full 可强制再试。
        "title_api_unauthorized": False,
        # freepublish/batchget（发布列表）最近一次运行结果：
        # "" | "ok" | "unauthorized"(48001) | "error"
        "publish_list_status": "",
        # 软删除台账（v5）：msgid -> {title, pubdate, deleted_at, read_user, ...}
        # 删除只影响看板展示（data.js 输出时过滤），store 内数据保留，
        # 因此「恢复」可瞬间还原；重新采集不会让已删除文章复活。
        "deleted": {},
        # 服务端持久化的手动命名（v5）：msgid -> title。
        # 浏览器不可用时（file:// 直开）仍回退 localStorage。
        "manual_titles": {},
        "articles": {},        # msgid -> {title, content_url, first_date}
        "article_total": {},   # msgid -> 最新累计快照
        "article_daily": {},   # stat_date -> msgid -> 当日指标
        "biz_daily": {},       # stat_date -> 账号级当日指标
        "user_daily": {},      # stat_date -> {new_user, cancel_user, cumulate_user}
    }


def ensure(d, key):
    return d.setdefault(key, {})


def upgrade_store(store):
    """兼容旧版 store.json：把 full_scan_done 迁移为逐日台账。"""
    if not isinstance(store, dict):
        return new_store()
    base = new_store()
    for k, v in base.items():
        store.setdefault(k, v)
    if "full_scan_done" in store:
        # 旧版只做了“一次性全扫”：迁移时视为全部未登记，
        # 下次运行会自动补扫一遍（代价约几分钟，只为把标题补齐）。
        del store["full_scan_done"]
    if not isinstance(store.get("title_scan_dates"), dict):
        store["title_scan_dates"] = {}
    if not isinstance(store.get("deleted"), dict):
        store["deleted"] = {}
    if not isinstance(store.get("manual_titles"), dict):
        store["manual_titles"] = {}
    # v5 起下线「合集」：旧 store 里的合集数据直接丢弃
    store.pop("collections", None)
    return store


# ----------------------------------------------------------------------
# 合并：按统计日
# ----------------------------------------------------------------------
def merge_article_read(store, rows):
    for r in rows:
        d, m = r.get("ref_date"), r.get("msgid")
        if not d or not m:
            continue
        a = ensure(store["articles"], m)
        # 发布日期取最早出现的那天（老版本 setdefault 一旦写入不再更新，
        # 会导致补扫历史后仍错标为最近日期，这里统一按最小日期修正）
        if not a.get("first_date") or d < a["first_date"]:
            a["first_date"] = d
        detail = r.get("detail", {}) or {}
        day = ensure(ensure(store["article_daily"], d), m)
        if "read_user" in detail:
            day["read_user"] = detail["read_user"]
        src = {}
        for s in detail.get("read_user_source", []) or []:
            scene = s.get("scene_desc")
            if scene and scene != "全部":
                src[scene] = s.get("user_count", 0)
        if src:
            day["read_source"] = src


def merge_article_share(store, rows):
    for r in rows:
        d, m = r.get("ref_date"), r.get("msgid")
        if not d or not m:
            continue
        detail = r.get("detail", {}) or {}
        if "share_user" in detail:
            ensure(ensure(store["article_daily"], d), m)["share_user"] = detail["share_user"]


def merge_biz_summary(store, rows):
    for r in rows:
        d = r.get("ref_date")
        if not d:
            continue
        detail = r.get("detail", {}) or {}
        day = ensure(store["biz_daily"], d)
        for k in ("read_user", "share_user", "zaikan_user", "like_user",
                  "comment_count", "collection_user", "redirect_ori_page_user",
                  "send_page_count"):
            if k in detail:
                day[k] = detail[k]
        src = {}
        for s in detail.get("read_user_source", []) or []:
            scene = s.get("scene_desc")
            if scene and scene != "全部":
                src[scene] = s.get("user_count", 0)
        if src:
            day["read_source"] = src


def merge_user_summary(store, rows):
    agg = {}
    for r in rows:
        d = r.get("ref_date")
        if not d:
            continue
        a = agg.setdefault(d, {"new_user": 0, "cancel_user": 0})
        a["new_user"] += int(r.get("new_user", 0) or 0)
        a["cancel_user"] += int(r.get("cancel_user", 0) or 0)
    for d, val in agg.items():
        day = ensure(store["user_daily"], d)
        day["new_user"] = val["new_user"]
        day["cancel_user"] = val["cancel_user"]


def merge_user_cumulate(store, rows):
    for r in rows:
        d = r.get("ref_date")
        if d and "cumulate_user" in r:
            ensure(store["user_daily"], d)["cumulate_user"] = r["cumulate_user"]


# ----------------------------------------------------------------------
# 合并：按发表日（getarticletotaldetail）
# ----------------------------------------------------------------------
def merge_article_total_detail(store, rows):
    for r in rows:
        pub, m = r.get("ref_date"), r.get("msgid")
        if not pub or not m:
            continue
        art = ensure(store["articles"], m)
        if r.get("title"):
            art["title"] = r["title"]
        if r.get("content_url"):
            art["content_url"] = r["content_url"]
        if not art.get("first_date") or pub < art["first_date"]:
            art["first_date"] = pub

        details = sorted(r.get("detail_list", []) or [],
                         key=lambda x: x.get("stat_date", ""))
        if not details:
            continue

        prev = {k: 0 for k in ADD_METRICS}
        prev_src = {}
        for dl in details:
            sd = dl.get("stat_date")
            if not sd:
                continue
            day = ensure(ensure(store["article_daily"], sd), m)
            for k in ADD_METRICS:
                if k in dl:
                    cur = int(dl[k] or 0)
                    inc = cur - prev[k]
                    if inc < 0:
                        inc = 0
                    day[k] = inc
                    prev[k] = cur
            cur_src = {}
            for s in dl.get("read_user_source", []) or []:
                scene = s.get("scene_desc")
                if scene and scene != "全部":
                    cur_src[scene] = int(s.get("user_count", 0) or 0)
            inc_src = {}
            for scene, cur in cur_src.items():
                inc = cur - prev_src.get(scene, 0)
                if inc < 0:
                    inc = 0
                if inc:
                    inc_src[scene] = inc
            if inc_src:
                day["read_source"] = inc_src
            prev_src = cur_src

        # 最新累计快照
        last = details[-1]
        snap = ensure(store["article_total"], m)
        snap["stat_date"] = last.get("stat_date")
        for k in ("read_user", "share_user", "zaikan_user", "like_user",
                  "comment_count", "collection_user", "read_subscribe_user",
                  "read_finish_rate", "read_avg_activetime"):
            if k in last:
                snap[k] = last[k]
        src = {}
        for s in last.get("read_user_source", []) or []:
            scene = s.get("scene_desc")
            if scene and scene != "全部":
                src[scene] = int(s.get("user_count", 0) or 0)
        if src:
            snap["read_source"] = src


DAILY_MERGERS = {
    "article_read": merge_article_read,
    "article_share": merge_article_share,
    "biz_summary": merge_biz_summary,
    "user_summary": merge_user_summary,
    "user_cumulate": merge_user_cumulate,
}


# ----------------------------------------------------------------------
# 调用
# ----------------------------------------------------------------------
def call_endpoint(store, token, key, endpoint, date):
    """调用单个接口一天。

    返回 (状态, 行数)：
      ("ok", n)      成功（n 可能为 0，表示当天无数据）
      ("retry", 0)   token 失效，需要换 token 后重试
      ("quota", 0)   日调用量上限（45009）：当天重试无意义，调用方应停扫等明天
      ("error", 0)   失败（已记入台账，下次自动补扫 / 已打日志）
    """
    url = API_BASE + endpoint + "?" + urlencode({"access_token": token})
    body = {"begin_date": date, "end_date": date}

    result = None
    # 瞬时错误（系统繁忙/频率限制）原地退避重试，避免把好日期误记成失败
    for attempt in range(3):
        result = http_request(url, body)
        errcode = result.get("errcode", 0)
        if errcode in TRANSIENT_ERRCODES:
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
        break

    errcode = result.get("errcode", 0)
    if errcode == 0:
        rows = result.get("list", []) or []
        if key in DAILY_MERGERS:
            DAILY_MERGERS[key](store, rows)
        elif key == ARTICLE_DETAIL_KEY:
            merge_article_total_detail(store, rows)
        return ("ok", len(rows))
    if errcode in (40001, 42001):
        return ("retry", 0)

    if errcode == DAILY_QUOTA_ERRCODE:
        # 日调用量上限：按接口分别计算，当天不会再恢复。
        # 不重试、不记台账，交给调用方停止该接口剩余扫描，明天自动继续。
        return ("quota", 0)

    if key == ARTICLE_DETAIL_KEY and errcode == 48001:
        # 无接口权限：逐日重试没有意义，标记后跳过补扫（--full 可强制再试）
        if not store.get("title_api_unauthorized"):
            store["title_api_unauthorized"] = True
            print("\n================ 标题接口无权限 ================")
            print("getarticletotaldetail 返回 48001（接口未授权）。")
            print("本脚本仍会正常采集阅读/分享/粉丝数据，但拿不到文章标题，")
            print("看板中会显示“未获取到标题”，可点击「命名」手动补上。")
            print("如确认公众号已认证且具备数据接口权限，可运行")
            print("python3 sync.py --full 强制重试一次。")
            print("================================================\n")
        return ("error", 0)

    # 接口下线 / 数据未生成 / 日期问题 / IP 白名单 → 静默跳过（与老版本一致）
    if errcode in (47009, 47011, 61503, 61500, 61501, 40164):
        if errcode == 40164:
            log("接口 %s 提示 IP 不在白名单（40164）。" % endpoint)
        # 标题扫描遇到“数据未生成”时记入台账，下次重试（可能是当天数据没出来）
        if key == ARTICLE_DETAIL_KEY and errcode == 61503:
            return ("error", 0)
        return ("ok", 0)

    log("接口 %s 在 %s 返回：%s %s（已记入台账，下次运行自动补扫）"
        % (key, date, errcode, result.get("errmsg")))
    return ("error", 0)


def fetch_publish_date(store, token, date, appid, secret, sleep_s=0.12):
    """扫描一个「发表日」的标题/累计数据，并更新台账。"""
    status, n = call_endpoint(store, token, ARTICLE_DETAIL_KEY,
                              ARTICLE_DETAIL_ENDPOINT, date)
    if status == "retry":
        token = get_access_token(appid, secret, force=True)
        status, n = call_endpoint(store, token, ARTICLE_DETAIL_KEY,
                                  ARTICLE_DETAIL_ENDPOINT, date)
    ledger = store.setdefault("title_scan_dates", {})
    if status == "quota":
        # 日配额用完：不动台账——没扫过的日期保持待扫（明天继续），
        # 已成功的日期也不降级。
        pass
    else:
        ledger[date] = "ok" if status == "ok" else "err"
    time.sleep(sleep_s)
    return token, status


def date_range(today, n):
    ds = [(today - timedelta(days=i + 1)).strftime("%Y-%m-%d") for i in range(n)]
    return sorted(set(ds))


# ----------------------------------------------------------------------
# 标题补全：获取已发布的消息列表（freepublish/batchget）
# ----------------------------------------------------------------------
def parse_mid_idx(url):
    """从文章 URL 中解析 (mid, idx)。

    mid 即「图文消息id」，idx 即篇目序号；官方文档明确
    msgid = 图文消息id_index，因此 msgid = mid + "_" + idx。
    兼容 appmsgid / itemidx 写法；解析不到返回 None。
    """
    if not url:
        return None
    try:
        q = parse_qs(urlparse(url.strip()).query)
    except Exception:
        return None
    mid = (q.get("mid") or q.get("appmsgid") or [""])[0].strip()
    idx = (q.get("idx") or q.get("itemidx") or [""])[0].strip()
    if not mid:
        return None
    return mid, (idx or "1")


def resolve_short_url(url, timeout=12):
    """跟随 302 跳转，拿到短链背后的完整文章 URL。

    有些 news_item[].url 是不带 mid/idx 的短链（mp.weixin.qq.com/s/<code>），
    urllib 默认会自动跟随重定向，resp.geturl() 即最终 URL。
    """
    if not url:
        return None
    try:
        req = Request(url.strip(), method="GET",
                      headers={"User-Agent": "wechat-dashboard/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.geturl() or url
    except Exception as e:
        log("    短链解析失败（%s）：%s" % (url, e))
        return None


def sync_publish_list(store, token, appid, secret):
    """用「获取已发布的消息列表」补全缺失的文章标题/链接。

    - 该接口是内容接口，不受 2025-11-01 数据起点限制，是补全老文章标题的关键来源；
    - news_item[].url 中的 mid/idx 与 msgid 前两段一一对应（官方：msgid=图文消息id_index）；
    - 只填充看板已知、但缺标题的 msgid，不会凭发布列表新增文章记录；
    - 48001（无权限）时打印指引并续跑，不影响其他数据采集。

    返回 (补上标题的篇数, 可能更新过的 token)。
    """
    # 1) 汇总看板已知、但没有标题的 msgid
    need = set()
    for m, a in (store.get("articles") or {}).items():
        if not (a or {}).get("title"):
            need.add(m)
    for d, arts in (store.get("article_daily") or {}).items():
        for m in (arts or {}):
            if not (store["articles"].get(m) or {}).get("title"):
                need.add(m)
    if not need:
        return 0, token

    log("开始通过「已发布消息列表」补全标题：待补全 %s 篇 …" % len(need))
    base = API_BASE + PUBLISH_LIST_ENDPOINT
    filled = 0
    offset = 0
    total = None
    pages = 0
    warned = False

    while True:
        url = base + "?" + urlencode({"access_token": token})
        body = {"offset": offset, "count": PUBLISH_PAGE, "no_content": 0}
        result = http_request(url, body)
        errcode = result.get("errcode", 0)
        if errcode in (40001, 42001):
            token = get_access_token(appid, secret, force=True)
            result = http_request(base + "?" + urlencode({"access_token": token}), body)
            errcode = result.get("errcode", 0)
        if errcode != 0:
            if errcode == DAILY_QUOTA_ERRCODE:
                store["publish_list_status"] = "quota"
                if not warned:
                    warned = True
                    log("「已发布消息列表」接口今日调用量已达上限（45009），"
                        "本次跳过标题补全；明天运行自动继续。")
                break
            if errcode == 48001:
                store["publish_list_status"] = "unauthorized"
                if not warned:
                    warned = True
                    print("\n================ 发布列表接口无权限 ================")
                    print("freepublish/batchget 返回 48001（接口未授权）。")
                    print("老文章（2025-11-01 前发表）的标题将无法自动补全，")
                    print("看板中会显示“未获取到标题”，可点「命名」手动补上。")
                    print("====================================================\n")
            else:
                store["publish_list_status"] = "error"
                if not warned:
                    warned = True
                    log("「已发布消息列表」接口返回 %s %s，本次跳过标题补全。"
                        % (errcode, result.get("errmsg")))
            break

        if total is None:
            total = int(result.get("total_count", 0) or 0)
            store["publish_list_status"] = "ok"
        items = result.get("item", []) or []
        if not items:
            break
        pages += 1
        for it in items:
            content = it.get("content") or {}
            for news in content.get("news_item", []) or []:
                title = (news.get("title") or "").strip()
                # 真实接口 news_item 同时含 url 与 content_url，个别账号只返回其中一个
                url = (news.get("url") or news.get("content_url") or "").strip()
                if not title and not url:
                    continue
                mid_idx = parse_mid_idx(url)
                final_url = url
                if not mid_idx and url:
                    # url 是短链：跟随 302 跳转后再解析
                    final = resolve_short_url(url)
                    if final:
                        final_url = final
                        mid_idx = parse_mid_idx(final)
                if not mid_idx:
                    continue
                msgid = "%s_%s" % mid_idx
                if msgid in need and title:
                    art = ensure(store["articles"], msgid)
                    art["title"] = title
                    if final_url:
                        art["content_url"] = final_url
                    art["title_src"] = "publish_list"
                    need.discard(msgid)
                    filled += 1
        if not need:
            break
        offset += len(items)
        if offset >= total or len(items) < PUBLISH_PAGE or pages >= PUBLISH_MAX_PAGES:
            break
        time.sleep(0.15)

    if filled:
        log("发布列表标题补全完成：补上 %s 篇（剩余 %s 篇缺标题）。"
            % (filled, len(need)))
    elif need:
        log("发布列表未补到标题（仍有 %s 篇缺标题）。" % len(need))
    return filled, token


# ----------------------------------------------------------------------
# 输出：data.js（应用手动命名 + 过滤软删除 + 附回收站快照）
# ----------------------------------------------------------------------
SNAP_FIELDS = ("read_user", "share_user", "zaikan_user", "like_user",
               "comment_count", "collection_user", "read_subscribe_user")


def snapshot_article(store, m):
    """取一篇文章的展示快照（供删除台账/回收站使用）。"""
    art = store.get("articles", {}).get(m, {})
    snap = store.get("article_total", {}).get(m, {})
    out = {"id": m,
           "title": art.get("title") or "",
           "pubdate": art.get("first_date") or "",
           "content_url": art.get("content_url") or ""}
    for k in SNAP_FIELDS:
        out[k] = snap.get(k, 0) or 0
    return out


def fix_first_dates(store):
    """按「文章日报」重算所有文章的首发日期。

    老版本（setdefault）把首次写入的日期固定下来，补扫历史数据后
    也不会更新，导致 47 篇老文章被错标为最近几天的日期。
    权威口径：文章在某统计日有数据 → 取最早的那天为发布日期。
    """
    fixed = 0
    daily = store.get("article_daily", {}) or {}
    arts = store.setdefault("articles", {})
    for d in sorted(daily.keys()):
        for m in daily[d]:
            a = arts.get(m)
            if a is None:
                continue
            if not a.get("first_date") or d < a["first_date"]:
                a["first_date"] = d
                fixed += 1
    if fixed:
        log("  发布日期校正：%d 篇按文章日报取最早日期修正" % fixed)


def build_payload(store):
    """store.json -> data.js 载荷：
    - 应用 manual_titles（手动命名优先，title_src=manual）
    - 过滤 deleted 台账中的文章（重扫不复活）
    - 附 deleted_articles 回收站快照（含删除时的指标）
    """
    deleted = store.get("deleted", {}) or {}
    manual = store.get("manual_titles", {}) or {}
    payload = dict(store)
    payload.pop("deleted", None)
    payload["manual_titles"] = manual

    articles = {}
    for m, art in (store.get("articles", {}) or {}).items():
        if m in deleted:
            continue
        art = dict(art)
        if m in manual and manual[m]:
            art["title"] = manual[m]
            art["title_src"] = "manual"
        articles[m] = art
    payload["articles"] = articles

    payload["article_total"] = {m: v for m, v in
                                (store.get("article_total", {}) or {}).items()
                                if m not in deleted}
    daily = {}
    for sd, rows in (store.get("article_daily", {}) or {}).items():
        daily[sd] = {m: v for m, v in rows.items() if m not in deleted}
    payload["article_daily"] = daily

    bin_list = []
    for m, rec in deleted.items():
        item = dict(rec) if isinstance(rec, dict) else {}
        item["id"] = m
        item.setdefault("title", "")
        item.setdefault("pubdate", "")
        item.setdefault("deleted_at", "")
        for k in SNAP_FIELDS:
            item.setdefault(k, 0)
        bin_list.append(item)
    bin_list.sort(key=lambda x: x.get("deleted_at", ""), reverse=True)
    payload["deleted_articles"] = bin_list
    return payload


def write_data_js(store):
    payload = build_payload(store)
    payload["generated_at"] = store.get("generated_at", "")
    out = (
        "window.DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n"
        + "window.DATA_GENERATED_AT = "
        + json.dumps(store.get("generated_at", ""), ensure_ascii=False) + ";\n"
    )
    tmp = DATAJS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
    os.replace(tmp, DATAJS_PATH)
    return payload


def main():
    config = load_json(CONFIG_PATH, {})
    appid = (config.get("appid") or "").strip()
    secret = (config.get("secret") or "").strip()
    lookback = int(config.get("lookback_days", 7))
    detail_days = int(config.get("article_detail_days", 30))

    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        try:
            val = int(sys.argv[idx + 1])
            lookback = val
            detail_days = max(detail_days, val)
        except Exception:
            pass

    if not appid or not secret or appid.startswith("wx在"):
        print("请先编辑 config.json，填入你的 appid 和 secret。")
        sys.exit(1)

    log("开始采集：统计日回溯 %s 天，发表日回溯 %s 天。" % (lookback, detail_days))
    store = upgrade_store(load_json(STORE_PATH, new_store()))

    token = get_access_token(appid, secret)
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    yesterday_s = yesterday.strftime("%Y-%m-%d")

    # 1) 最近 N 个「发表日」：新文章的标题/链接 + 累计轨迹（每次必跑）。
    #    新的日期排在前面：万一遇到日调用量上限（45009），先保住最近发表文章的标题。
    pub_dates = [d for d in date_range(today, detail_days) if d <= yesterday_s]
    pub_dates.sort(reverse=True)
    detail_quota = False      # getarticletotaldetail 今日配额是否已用完
    n_pub = 0
    for i, date in enumerate(pub_dates):
        token, status = fetch_publish_date(store, token, date, appid, secret)
        if status == "quota":
            detail_quota = True
            log("getarticletotaldetail 今日调用量已达上限（45009），发表日期扫描暂停。")
            log("  已扫描的日期不会重复；剩余 %s 个日期将在下次运行自动继续"
                "（配额每天 0 点重置，无需干预）。" % (len(pub_dates) - i))
            break
        if status == "ok":
            n_pub += 1
    if not detail_quota:
        log("最近 %s 天发表内容明细完成" % len(pub_dates))

    # 1.1) 逐日补扫：只扫台账里没成功过的日期（首次≈全量；失败日期自愈重试）
    force_full = "--full" in sys.argv
    earliest = datetime.strptime(EARLIEST_DATE, "%Y-%m-%d").date()
    start = today - timedelta(days=365)
    if start < earliest:
        start = earliest
    all_dates = []
    cur = start
    while cur <= yesterday:
        all_dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)

    ledger = store.setdefault("title_scan_dates", {})
    pending = [d for d in all_dates if force_full or ledger.get(d) != "ok"]
    if force_full:
        log("--full：强制重新扫描全部发表日期。")
    if pending and store.get("title_api_unauthorized") and not force_full:
        log("标题接口此前返回过 48001（无权限），跳过 %s 个日期的补扫；"
            "加 --full 可强制重试。" % len(pending))
        pending = []
    if detail_quota and pending:
        # 同一个接口，配额已在第 1 步用完，补扫今天不可能成功
        log("标题接口今日配额已用完（45009），跳过剩余 %s 个待补扫日期；"
            "明天运行自动继续。" % len(pending))
        pending = []
    if pending:
        log("开始标题补扫：%s 至 %s（共 %s 天，期间请勿中断）"
            % (pending[0], pending[-1], len(pending)))
        for i, date in enumerate(pending):
            token, status = fetch_publish_date(store, token, date, appid, secret,
                                               sleep_s=0.05)
            if status == "quota":
                detail_quota = True
                log("标题补扫遇到日调用量上限（45009），已暂停。")
                log("  本次完成 %s/%s，剩余 %s 个日期将在下次运行自动继续。"
                    % (i, len(pending), len(pending) - i))
                break
            if (i + 1) % 30 == 0:
                log("  补扫进度 %s/%s" % (i + 1, len(pending)))
        if not detail_quota:
            log("标题补扫完成。")
    elif not store.get("title_api_unauthorized") and not detail_quota:
        log("标题台账已是最新，无需补扫。")

    # 2) 按统计日：每日端点（45009 按接口分别计算：只跳过中招的那个接口）
    stat_dates = date_range(today, lookback)
    n_stat = 0
    quota_eps = set()
    for date in stat_dates:
        for key, endpoint in DAILY_ENDPOINTS.items():
            if key in quota_eps:
                continue
            status, n = call_endpoint(store, token, key, endpoint, date)
            if status == "retry":
                token = get_access_token(appid, secret, force=True)
                status, n = call_endpoint(store, token, key, endpoint, date)
            if status == "quota":
                quota_eps.add(key)
                log("接口 %s 今日调用量已达上限（45009），跳过其剩余日期；"
                    "明天运行自动继续。" % key)
                continue
            if isinstance(n, int):
                n_stat += n
            time.sleep(0.12)
        log("  %s 完成" % date)

    # 2.1) 发布列表补全：老文章（2025-11-01 前发表）标题的权威来源。
    #      放在每日采集之后：getarticleread 每天都会“新发现”仍有阅读的老文章，
    #      补全必须覆盖这些刚入库的记录。
    #      （内容接口，无日期限制；url 中 mid/idx 与 msgid 一一对应）
    filled, token = sync_publish_list(store, token, appid, secret)

    fix_first_dates(store)   # 保存前统一校正发布日期
    store["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(STORE_PATH, store)
    write_data_js(store)

    # 3) 标题健康检查：解释“为什么还有文章没有标题”
    deleted_ids = set(store.get("deleted", {}) or {})
    known_ids = (set(store["articles"]) | set(store["article_total"])) - deleted_ids
    for d in store["article_daily"]:
        known_ids |= (set(store["article_daily"][d]) - deleted_ids)
    manual = store.get("manual_titles", {}) or {}
    untitled = [m for m in known_ids
                if not (store["articles"].get(m, {}).get("title") or manual.get(m))]
    failed_dates = [d for d, v in ledger.items() if v != "ok"]
    log("采集完成：发表明细 %s 天、每日记录 %s 条%s。"
        % (n_pub, n_stat, "、发布列表补标题 %s 篇" % filled if filled else ""))
    log("标题情况：共 %s 篇文章，其中 %s 篇没有标题。" % (len(known_ids), len(untitled)))
    if untitled:
        if store.get("publish_list_status") == "unauthorized":
            print("  · 原因：发布列表接口无权限（48001），无法自动补全老文章标题；"
                  "可在看板中「命名」手动补上。")
        elif store.get("publish_list_status") == "quota":
            print("  · 原因：发布列表接口今日调用量已达上限（45009），"
                  "明天运行会自动补全。")
        elif store.get("publish_list_status") == "error":
            print("  · 原因：发布列表接口调用失败（详见上方日志），"
                  "下次运行会自动重试补全。")
        elif failed_dates:
            print("  · 原因：%s 个发表日采集失败（限流/网络等原因），"
                  "下次运行会自动补扫。" % len(failed_dates))
        print("  · 2025-11-01 之前发表的老文章：若发布列表也没补上（未发布过/已删除/无权限），"
              "微信不提供其标题，看板中显示为“未获取到标题”，点「命名」可手动补上。")
    if detail_quota:
        print("  · getarticletotaldetail 今日配额已用完（45009）：剩余发表日期明天自动补扫；"
              "标题以发布列表补全为准，累计轨迹数据随后几天自动补齐。")
        print("  · 如需当天跑完：公众平台后台「接口权限模板」可查看实时调用量，"
              "认证账号每月有 10 次清零机会。")
    log("已写入 store.json / data.js，用浏览器打开 index.html 即可。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(1)
    except BrokenPipeError:
        pass
