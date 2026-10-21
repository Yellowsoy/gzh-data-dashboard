/**
 * Blob 存储封装（EdgeOne Pages Blob）
 *
 * 平台在构建时会把 PAGES_BLOB_DEPLOY_CREDENTIAL / PAGES_PROJECT_ID 注入运行环境，
 * 函数内直接 getStore(name) 即可读写，无需在代码里放任何密钥。
 * 注意：必须静态 import（Edge Runtime 不支持动态 import()）。
 */
import { getStore } from "@edgeone/pages-blob";

const DEFAULT_STORE = "gzh-data";

export function storeName(env) {
  return (env && (env.GZH_STORE_NAME || env.gzh_store_name)) || DEFAULT_STORE;
}

function store(env) {
  return getStore(storeName(env));
}

/* 读文本；不存在返回 null */
export async function getText(env, key) {
  const v = await store(env).get(key, { type: "text" });
  if (v === null || v === undefined) return null;
  return typeof v === "string" ? v : String(v);
}

export async function getJSON(env, key) {
  const t = await getText(env, key);
  if (!t) return null;
  try {
    return JSON.parse(t);
  } catch (e) {
    return null;
  }
}

/* 写文本：强一致 + 不缓存，保证写完立刻读到新值 */
export async function setText(env, key, value, contentType = "text/plain; charset=utf-8") {
  await store(env).set(key, value, { contentType, cacheControl: "no-store" });
}

export async function setJSON(env, key, obj) {
  await setText(env, key, JSON.stringify(obj), "application/json; charset=utf-8");
}

export async function del(env, key) {
  try {
    await store(env).delete(key);
  } catch (e) {
    /* 删除失败不影响主流程 */
  }
}
