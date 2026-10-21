/**
 * 响应与请求体小工具（Edge Runtime）
 */

export function json(obj, code = 200) {
  return new Response(JSON.stringify(obj), {
    status: code,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store"
    }
  });
}

export async function readJSON(request) {
  try {
    const t = await request.text();
    if (!t) return null;
    const o = JSON.parse(t);
    return o && typeof o === "object" && !Array.isArray(o) ? o : null;
  } catch (e) {
    return null;
  }
}

/* 请求体上限，与原 Python 版 MAX_BODY 一致 */
export const MAX_BODY = 12 * 1024 * 1024;

export function tooLarge(request) {
  const len = Number(request.headers.get("content-length") || 0);
  return len > MAX_BODY;
}
