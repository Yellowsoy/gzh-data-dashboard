/**
 * GET /api/data/full —— 返回完整数据（供需要回拉数据的端使用）
 *
 * data.js 是 `window.DATA = {...}` 形式，这里截取第一个 `{` 到最后一个 `}` 解析。
 */
import { getText } from "../../../lib/blob.js";
import { json } from "../../../lib/http.js";

const KEY = "data.js";

export default async function onRequest({ env }) {
  let text = null;
  try {
    text = await getText(env, KEY);
  } catch (e) {
    text = null;
  }
  if (!text) return json({ ok: false, message: "数据文件不可用" }, 500);

  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end <= start) return json({ ok: false, message: "数据文件不可用" }, 500);
  try {
    const d = JSON.parse(text.slice(start, end + 1));
    return json({ ok: true, generated_at: d.generated_at || "", data: d });
  } catch (e) {
    return json({ ok: false, message: "数据文件不可用" }, 500);
  }
}
