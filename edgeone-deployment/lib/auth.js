/**
 * 管理密码验证 —— 无状态令牌（Edge Runtime / Web Crypto 实现）
 *
 * 运行环境没有 Node 的 crypto.randomBytes / Buffer，也没有内存态（多实例不共享），
 * 所以令牌不存服务端，而是用 ADMIN_PWD 派生密钥做 HMAC-SHA256 签名：
 *   token = base64url(payload) + "." + base64url(HMAC-SHA256(payload))
 * payload = {"exp": 过期毫秒时间戳, "rnd": 随机串}
 * 校验时重算签名并比对（定长比较），同时检查 exp。服务端无需保存任何会话。
 */
const TTL_MS = 8 * 60 * 60 * 1000; // 8 小时，与原 Python 版一致

const encoder = new TextEncoder();

function b64u(bytes) {
  let bin = "";
  const arr = new Uint8Array(bytes);
  for (let i = 0; i < arr.length; i++) bin += String.fromCharCode(arr[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64uDecode(str) {
  const s = String(str).replace(/-/g, "+").replace(/_/g, "/");
  const pad = s.length % 4 ? "=".repeat(4 - (s.length % 4)) : "";
  const bin = atob(s + pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function hmacKey(secret) {
  return crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
}

async function sign(payloadB64, secret) {
  const key = await hmacKey(secret);
  const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(payloadB64));
  return b64u(sig);
}

/* 管理密码来自环境变量；未配置时返回空串，调用方据此拒绝所有写操作 */
export function adminPwd(env) {
  const v = (env && (env.ADMIN_PWD || env.admin_pwd)) || "";
  return String(v);
}

/* 定长比较，避免通过响应时间差猜签名 */
function safeEqual(a, b) {
  const x = String(a);
  const y = String(b);
  let diff = x.length ^ y.length;
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) diff |= (x.charCodeAt(i) || 0) ^ (y.charCodeAt(i) || 0);
  return diff === 0;
}

function randomHex(nBytes) {
  const buf = new Uint8Array(nBytes);
  crypto.getRandomValues(buf);
  let s = "";
  for (let i = 0; i < buf.length; i++) s += buf[i].toString(16).padStart(2, "0");
  return s;
}

export async function issueToken(env) {
  const payload = JSON.stringify({ exp: Date.now() + TTL_MS, rnd: randomHex(8) });
  const pb = b64u(encoder.encode(payload));
  const sig = await sign(pb, adminPwd(env));
  return pb + "." + sig;
}

export async function verifyToken(tok, env) {
  const pwd = adminPwd(env);
  if (!pwd || typeof tok !== "string") return false;
  const dot = tok.indexOf(".");
  if (dot <= 0) return false;
  const pb = tok.slice(0, dot);
  const sig = tok.slice(dot + 1);
  let expected;
  try {
    expected = await sign(pb, pwd);
  } catch (e) {
    return false;
  }
  if (!safeEqual(sig, expected)) return false;
  try {
    const payload = JSON.parse(new TextDecoder().decode(b64uDecode(pb)));
    return typeof payload.exp === "number" && payload.exp > Date.now();
  } catch (e) {
    return false;
  }
}

export async function checkPassword(input, env) {
  const pwd = adminPwd(env);
  if (!pwd || typeof input !== "string") return false;
  return safeEqual(input, pwd);
}

export const TOKEN_TTL_MS = TTL_MS;
