/**
 * POST /api/logout —— 令牌为无状态签名，服务端无需保存/销毁；前端清掉本地会话即可。
 * 保留该接口是为了不改变前端调用约定。
 */
import { json } from "../../lib/http.js";

export default async function onRequest({ request }) {
  if (request.method.toUpperCase() !== "POST") {
    return json({ ok: false, message: "method not allowed" }, 405);
  }
  return json({ ok: true });
}
