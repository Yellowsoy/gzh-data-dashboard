/**
 * GET /data.js —— 前端用 <script src="data.js"> 同步加载数据
 *
 * EdgeOne Pages 的函数文件名会剥离末尾 .js，所以要暴露 /data.js 这个路径，
 * 文件必须命名为 data.js.js（映射规则：/x.js ← edge-functions/x.js.js）。
 * 数据本身存在 Blob，仓库里不含任何真实数据（公开仓库需完全脱敏）。
 */
import { getText } from "../lib/blob.js";

const KEY = "data.js";

const EMPTY = 'window.DATA = {"generated_at":"","articles":{},"article_daily":{},"biz_daily":{},"user_daily":{}};\n' +
  'window.DATA_GENERATED_AT = "";\n';

export default async function onRequest({ env }) {
  let body = null;
  try {
    body = await getText(env, KEY);
  } catch (e) {
    body = null;
  }
  const out = body && body.indexOf("window.DATA") >= 0 ? body : EMPTY;
  return new Response(out, {
    headers: {
      "Content-Type": "application/javascript; charset=utf-8",
      "Cache-Control": "no-store"
    }
  });
}
