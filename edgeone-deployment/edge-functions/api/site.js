/**
 * GET /api/site —— 站点级配置（页脚版权等）
 *
 * EdgeOne Pages 是静态托管，无法像 Python 服务那样在返回 HTML 时注入页脚，
 * 因此把版权文案放在环境变量 FOOTER_TEXT，由前端取回后渲染到页面底部。
 * 好处：公开仓库里不含具体文案，换站/换备案号只需改变量、不必改代码。
 */
import { json } from "../../lib/http.js";

export default async function onRequest({ env }) {
  const footer = (env && (env.FOOTER_TEXT || env.footer_text)) || "";
  return json({ ok: true, footer: String(footer) });
}
