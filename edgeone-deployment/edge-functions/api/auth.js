/**
 * POST /api/auth —— 管理密码验证，成功下发临时令牌
 * POST /api/logout —— 无状态令牌无需服务端销毁，返回成功即可（前端清本地会话）
 *
 * 密码只存在于环境变量 ADMIN_PWD，仓库与代码内不含明文。
 */
import { checkPassword, issueToken, TOKEN_TTL_MS } from "../../lib/auth.js";
import { json, readJSON, tooLarge } from "../../lib/http.js";

export default async function onRequest({ request, env }) {
  if (request.method.toUpperCase() !== "POST") {
    return json({ ok: false, message: "method not allowed" }, 405);
  }
  if (tooLarge(request)) return json({ ok: false, message: "请求体无效或过大" }, 400);

  const body = await readJSON(request);
  if (!body) return json({ ok: false, message: "请求不是有效的 JSON" }, 400);

  const ok = await checkPassword(body.pwd, env);
  if (!ok) {
    /* 轻微延迟，增加暴力尝试成本（与原 Python 版一致） */
    await new Promise((r) => setTimeout(r, 400));
    return json({ ok: false, message: "密码不正确" }, 403);
  }
  const token = await issueToken(env);
  return json({ ok: true, token, expires_in: TOKEN_TTL_MS });
}
