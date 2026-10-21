/**
 * GET /api/status —— 前端用它探测“本机服务模式”（采集/同步任务状态）。
 * EdgeOne 版没有本地采集进程，返回 job:null 表示无后台任务，前端会保持云端模式。
 */
import { json } from "../../lib/http.js";

export default async function onRequest() {
  return json({ ok: true, job: null, mode: "edgeone" });
}
