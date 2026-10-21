# 微信公众号数据看板 · EdgeOne Pages 部署

静态前端 + Edge Functions + Blob 存储，全部跑在腾讯云 EdgeOne Pages 免费版上，不需要自己的服务器。

## 目录结构

```
edgeone-deployment/
├── index.html                    前端（与 workbuddy-deployment 同源，脱敏版）
├── vendor/                       图表与 Excel 库（本地引入，不依赖公网 CDN）
├── package.json                  函数依赖：@edgeone/pages-blob
├── lib/                          函数共用代码
│   ├── blob.js                   Blob 读写封装
│   ├── auth.js                   管理密码 → 无状态 HMAC 令牌
│   └── http.js                   JSON 响应/请求体工具
└── edge-functions/               路由（Edge Runtime，Web 标准 API）
    ├── data.js.js                → /data.js      前端 <script src> 取数据
    └── api/
        ├── auth.js               → /api/auth     密码换令牌
        ├── logout.js             → /api/logout   登出
        ├── state.js              → /api/state    回收站/重命名/布局
        ├── data.js               → /api/data     数据元信息 / 写入
        ├── data/full.js          → /api/data/full 全量数据
        ├── status.js             → /api/status   任务状态（此版恒为空）
        └── site.js               → /api/site     页脚等站点配置
```

## 为什么函数放在 `edge-functions/` 而不是 `functions/`

实测（EdgeOne Pages，2026-10）：

| 写法 | 结果 |
| --- | --- |
| `edge-functions/api/x.js` | ✅ 映射到 `/api/x`，**只有它稳定生效** |
| `functions/api/x.js` | ❌ 同项目内一起放时全部 404 |
| `functions/api/[[default]].js` | ❌ 目录整体不生效 |
| `edge-functions/api/[[default]].js` | ✅ 兜底路由正常 |

文件名末尾的 `.js` 会被剥离，所以要让前端拿到 `/data.js`，文件必须叫 `data.js.js`。

## 为什么鉴权用签名令牌而不是服务端会话

Edge Runtime 没有 Node 的 `crypto.randomBytes` / `Buffer`，也没有可依赖的内存态（多实例不共享）。因此：

```
token = base64url(payload) + "." + base64url(HMAC-SHA256(payload, ADMIN_PWD))
payload = {"exp": 过期毫秒, "rnd": 随机串}
```

校验时用 Web Crypto 重算签名、定长比较、再查 `exp`。服务端不保存任何会话，天然支持多实例与冷启动。

## 环境变量

在 EdgeOne 控制台 → 项目 → 设置 → 环境变量中添加：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `ADMIN_PWD` | ✅ | 管理密码。**不配置则所有写操作被拒绝**（不会退化成默认密码） |
| `FOOTER_TEXT` | 可选 | 页脚版权文案，如 `© 2026 你的站名 丨 某ICP备XXXXXXXX号` |
| `GZH_STORE_NAME` | 可选 | Blob 存储名，默认 `gzh-data` |

## 数据存在哪里

真实数据**不进仓库**：`data.js` 与 `state.json` 都存在 EdgeOne Blob，键名分别是 `data.js`、`state.json`（另有 `data.meta.json` 存生成时间等元信息，避免每次轮询都读近 1MB 的全量数据）。

首次部署后需要用带写入凭证的脚本灌一次初始数据；之后所有更新都走页面上的「更新数据」，由 `/api/data` 写回 Blob。

仓库内不含任何真实统计数据、单位名称或备案号。

## 本地预览

```bash
npm i -g edgeone
edgeone pages dev
```

需要本机有 Blob 凭证，或改用 `workbuddy-deployment/`（Python 标准库实现，零依赖）在本地运行。
