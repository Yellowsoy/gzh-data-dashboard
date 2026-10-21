/**
 * /api/state —— 共享状态（回收站 / 重命名 / 布局），全设备同步
 *
 * GET  返回 {rev, deleted, titles, layout}
 * POST {token, rev, deleted, titles, layout} 写入（需令牌）
 *
 * 语义与 Python 版 server.py 保持一致：
 * - rev 异常大（超过当前时间 1 小时）时钳制为当前时间，避免把状态锁死在“将来”
 * - rev 小于服务端已有 rev 时不算失败，返回 {ok:true, stale:true, rev: 服务端值}
 * - POST 未带 layout 键则保持原布局；带 null 表示恢复系统默认
 */
import { getJSON, setJSON } from "../../lib/blob.js";
import { verifyToken } from "../../lib/auth.js";
import { json, readJSON, tooLarge } from "../../lib/http.js";

const KEY = "state.json";

function validLayout(v) {
  if (v === null) return true;
  if (!v || typeof v !== "object" || Array.isArray(v)) return false;
  const order = v.order;
  if (!Array.isArray(order) || order.length === 0) return false;
  if (order.some((x) => typeof x !== "string")) return false;
  if (v.span !== undefined && v.span !== null && (typeof v.span !== "object" || Array.isArray(v.span))) return false;
  if (v.heights !== undefined && v.heights !== null && (typeof v.heights !== "object" || Array.isArray(v.heights))) return false;
  return true;
}

async function readState(env) {
  const s = await getJSON(env, KEY);
  if (!s || typeof s !== "object" || typeof s.rev !== "number") {
    return { rev: 0, deleted: {}, titles: {}, layout: null };
  }
  return {
    rev: s.rev,
    deleted: s.deleted && typeof s.deleted === "object" ? s.deleted : {},
    titles: s.titles && typeof s.titles === "object" ? s.titles : {},
    layout: s.layout && typeof s.layout === "object" ? s.layout : null
  };
}

export default async function onRequest({ request, env }) {
  const method = request.method.toUpperCase();

  if (method === "GET") {
    try {
      return json(await readState(env));
    } catch (e) {
      /* 存储不可用时给出可用空态，避免整个页面判定为“非云端模式” */
      return json({ rev: 0, deleted: {}, titles: {}, layout: null });
    }
  }

  if (method !== "POST") return json({ ok: false, message: "method not allowed" }, 405);
  if (tooLarge(request)) return json({ ok: false, message: "请求体无效或过大" }, 400);

  const body = await readJSON(request);
  if (!body) return json({ ok: false, message: "请求不是有效的 JSON" }, 400);
  if (!(await verifyToken(body.token, env))) {
    return json({ ok: false, message: "未解锁或登录已过期，请重新验证" }, 403);
  }

  const rev0 = body.rev;
  const deleted = body.deleted;
  const titles = body.titles;
  if (typeof rev0 !== "number" || !deleted || typeof deleted !== "object" || !titles || typeof titles !== "object") {
    return json({ ok: false, message: "缺少 rev/deleted/titles 或格式错误" }, 400);
  }

  const cur = await readState(env);

  let layout = cur.layout;
  if (Object.prototype.hasOwnProperty.call(body, "layout")) {
    const lv = body.layout;
    if (!validLayout(lv)) return json({ ok: false, message: "layout 格式错误" }, 400);
    layout = lv && typeof lv === "object" ? lv : null;
  }

  const now = Date.now();
  let rev = rev0;
  if (rev > now + 60 * 60 * 1000) rev = now;

  if (rev < cur.rev) {
    return json({ ok: true, rev: cur.rev, stale: true });
  }

  try {
    await setJSON(env, KEY, { rev, deleted, titles, layout });
  } catch (e) {
    return json({ ok: false, message: "写入状态失败：" + String((e && e.message) || e).slice(0, 120) }, 500);
  }
  return json({ ok: true, rev, has_layout: layout !== null });
}
