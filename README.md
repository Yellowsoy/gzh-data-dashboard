# 微信公众号数据看板

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/backend%20deps-zero-brightgreen)](https://docs.python.org/3/library/)
[![ECharts](https://img.shields.io/badge/ECharts-5-red)](https://echarts.apache.org/)
[![Version](https://img.shields.io/badge/version-1.0.0-informational)](CHANGELOG.md)

面向党政机关、企事业单位的微信公众号运营数据可视化看板：从微信官方接口采集文章阅读、分享、粉丝等数据，在浏览器中以图表呈现，支持多端同步、回收站管理、布局自定义与服务端鉴权。

纯标准库实现，**零第三方后端依赖**；前端仅依赖 ECharts 与 SheetJS 两个本地化 JS 库（已内置，无需 CDN，可在内网环境运行）。

> **两个部署版本**
> - [`local-deployment/`](local-deployment/) —— 在单位内网电脑上运行，自动采集公众号数据，并同步到线上
> - [`workbuddy-deployment/`](workbuddy-deployment/) —— 部署到 [WorkBuddy](https://www.workbuddy.cn) 云端，获得固定公网链接，各端直接访问

## 功能特性

- **关键指标**：累计粉丝、最新日净增、近 7 天阅读/分享人数、文章总数，以及可切换口径的**周发文数 / 周阅读数**
  - `近7天`：含最新数据日在内的过去 7 天（滚动窗口）
  - `本周`：最新数据日所在自然周（周一至周日）
- **阅读与分享趋势**：折线图，底部比例尺 + 滚轮/双指缩放，附「全部 / 近90天 / 近30天 / 近7天」快捷按钮
- **阅读来源、粉丝趋势、文章排行**：饼图、面积图、条形图
- **文章明细**：搜索、日期筛选、排序、分页、导出 Excel（支持按最近一天/七天/一个月/自定义时间导出）
- **回收站**：删除 / 还原 / 彻底删除，**云端全设备同步**
- **布局自定义**：模块可拖拽调整位置与大小，临时布局（免密，仅本次查看）与默认布局（需密码，云端同步）两级保存
- **数据自动采集**（本地版）：调用微信公众平台官方接口，按日采集文章与粉丝数据
- **本地 → 线上自动同步**（本地版）：采集完成后自动把数据与回收站状态推送到线上站点
- **服务端密码验证**：管理密码只保存在服务端，页面源码中不含密码；验证通过后下发 8 小时有效的临时令牌，所有写操作凭令牌放行
- **发布日期自动校正**：以文章日报中的最早出现日期为权威口径，防止历史补扫导致的日期错标
- **响应式**：桌面 / 平板 / 手机自适应，无横向溢出

## 目录

- [技术栈](#技术栈)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [获取公众号凭据（AppID / AppSecret）](#获取公众号凭据appid--appsecret)
- [数据流](#数据流)
- [数据结构](#数据结构datajs)
- [API 接口](#api-接口)
- [安全机制](#安全机制)
- [已知限制](#已知限制)
- [常见问题](#常见问题)
- [参与贡献](#参与贡献)
- [开源协议](#开源协议)

## 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| 前端 | 原生 HTML / CSS / JavaScript | 单文件 `index.html`（约 130KB），无构建步骤 |
| 图表 | ECharts 5（本地化） | Canvas 渲染，动画关闭以保证流畅 |
| 导出 | SheetJS（本地化） | 前端直接生成 .xlsx |
| 本地服务 | Python 3 标准库 | `serve.py`（HTTP 服务）+ `sync.py`（数据采集） |
| 线上服务 | Python 3 标准库 | `server.py`（静态资源 + 同步 API） |

## 目录结构

```
gzh-data-dashboard/
├── README.md                     # 本文档
├── LICENSE                       # MIT 开源协议
├── local-deployment/             # 版本一：本地电脑部署
│   ├── index.html                # 看板页面
│   ├── serve.py                  # 本地 HTTP 服务（含上行同步）
│   ├── sync.py                   # 微信官方接口采集脚本
│   ├── config.example.json       # 配置模板（复制为 config.json 后填写）
│   ├── run.sh / stop.sh          # 一键启动 / 停止
│   ├── README.md                 # 本地部署详细说明
│   └── vendor/                   # 本地化的前端库
│       ├── echarts.min.js
│       └── xlsx.full.min.js
└── workbuddy-deployment/         # 版本二：WorkBuddy 云端部署
    ├── index.html                # 看板页面（与本地版同一份）
    ├── server.py                 # 云端服务（静态资源 + 同步 API + 令牌鉴权）
    └── vendor/
```

## 快速开始

### 版本一：本地电脑部署

适用于：在单位内网电脑上运行，通过浏览器访问，数据保存在本机，可自动同步到线上站点。

> 开始前需要先取得公众号的 AppID 与 AppSecret，并配置 IP 白名单（约 5 分钟，需管理员扫码）。完整步骤见 [获取公众号凭据](#获取公众号凭据appid--appsecret)。

```bash
cd local-deployment
cp config.example.json config.json
# 编辑 config.json，填入公众号的 appid 与 secret
./run.sh            # 启动，默认 http://127.0.0.1:8000
```

启动后：

1. 打开看板页面，点击顶栏「锁定」按钮，输入管理密码解锁（默认 `change-me`，见下方「安全机制」）
2. 点击「数据更新」触发采集（子进程运行 `sync.py`，单航班防重入）
3. 采集完成后数据自动写入本机 `data.js`；若配置了 `upstream`，会自动推送到线上站点

### 版本二：WorkBuddy 云端部署

适用于：通过 WorkBuddy（或其他支持 Python 服务的平台）发布为固定链接，所有设备直接访问，数据与状态云端共享。

```bash
cd workbuddy-deployment
python3 server.py          # 本地预览（默认端口 3000，可用 PORT 环境变量覆盖）
```

在 WorkBuddy 中发布时，将 `workbuddy-deployment` 目录作为站点目录，启动命令设为 `python3 server.py`。发布后链接固定，重复部署（更新代码）链接不变。

首次部署后需要导入一份 `data.js`（由本地版采集生成，或在页面上传），数据格式见下文「数据结构」。

## 获取公众号凭据（AppID / AppSecret）

本地版的自动采集需要调用微信公众平台官方接口，因此必须准备两个凭据：**AppID**（账号标识）与 **AppSecret**（接口密钥）。二者的关系可以理解成「账号 + 密码」——AppID 是公开的，AppSecret 等同于密码，**一旦泄露，他人就能以你的公众号身份调用接口**。

只有本地部署版需要这两个凭据；WorkBuddy 部署版只做展示，不需要任何微信凭据。

### 前置条件

| 条件 | 要求 |
|---|---|
| 公众号类型 | **已认证的订阅号或服务号** |
| 操作人员 | 需要**管理员**（或具有开发权限的运营者）扫码确认 |
| 接口权限 | 需具备「数据统计」类接口权限（认证后默认开通） |

> 未认证的个人号无法调用数据接口，会返回 `48001`（无接口权限）。如果你不确定，可在公众平台「设置与开发 → 接口权限」中查看「数据统计」一栏是否为已获得。

### 第一步：获取 AppID 与 AppSecret

1. 用管理员账号登录 [微信公众平台](https://mp.weixin.qq.com)
2. 进入 **设置与开发 → 基本配置**（旧版菜单为「开发 → 基本配置」）
3. 在页面右侧找到 **公众号开发信息** 区块：
   - **开发者ID（AppID）**：直接可见，形如 `wx` 开头、后跟 16 位小写字母与数字（共 18 位）
   - **开发者密码（AppSecret）**：点击 **重置** 或 **查看**
4. 此时会弹出二维码，**必须用管理员微信扫码**
5. 扫码后页面显示完整的 AppSecret（32 位字符串），**请立即复制保存**

> **重要**：AppSecret 只在这一个页面显示一次。关闭弹窗后无法再次查看，只能重新重置。如果忘了，重复上述步骤重置即可，但重置后**旧的 AppSecret 会立即失效**，正在运行的采集服务需要同步更新配置。

### 第二步：配置 IP 白名单（最容易踩的坑）

微信出于安全考虑，只允许白名单内的服务器调用接口。**这是新手最常遇到的失败原因**——凭据填对了，但 IP 没加，接口会返回：

```json
{"errcode": 40164, "errmsg": "invalid ip 1.2.3.4, not in whitelist"}
```

配置方法：

1. 仍在 **设置与开发 → 基本配置** 页面，找到 **IP白名单** 区块，点击 **查看 / 修改**
2. 用管理员微信扫码
3. 填入运行采集脚本的那台电脑的**公网出口 IP**

查询本机公网 IP 的方式：

- 浏览器打开 [https://ip.cn](https://ip.cn) 或 [https://myip.ipip.net](https://myip.ipip.net)
- 命令行执行：`curl https://api.ipify.org`
- 或者不必手动查——直接运行采集，脚本报错时会把当前出口 IP 打印出来，照提示填即可

填写格式：

| 写法 | 是否支持 | 示例 |
|---|---|---|
| 单个 IP | ✅ | `1.2.3.4` |
| CIDR 网段 | ✅ | `1.2.3.4/24` |
| 通配符 | ❌ | `1.2.3.*` 不支持 |

> 家庭宽带的公网 IP 会不定期变化。IP 变了之后采集会失败并提示新 IP，把它补进白名单即可。单位固定专线 IP 一般不变。

### 第三步：填入配置文件

```bash
cd local-deployment
cp config.example.json config.json
```

编辑 `config.json`：

```json
{
  "appid": "wx你的18位AppID",
  "secret": "你的32位AppSecret",
  "lookback_days": 7,
  "article_detail_days": 30,
  "upstream": "",
  "upstream_pwd": "你的线上管理密码",
  "upstream_auto_push": true
}
```

保存后启动服务，页面点「数据更新」即可开始采集。

### 凭据保管的安全要求

- `config.json` 已被 `.gitignore` 排除，**永远不会进入版本库**，切勿手动提交或截图外发
- 运行目录下的 `token.json` 是接口访问令牌的缓存，同样不要外发
- 如果需要交接给同事，建议由对方用自己的管理员账号重新生成 AppSecret，而不是传递同一个密钥
- 怀疑泄露时，第一时间去公众平台**重置 AppSecret**

## 数据流

```
微信公众平台 API
      │  sync.py 按日采集（文章明细 / 来源 / 粉丝）
      ▼
本地 store.json ──生成──> data.js（页面数据源）
      │                      │
      │                serve.py 上行同步（自动）
      ▼                      ▼
  本机浏览器  <─────>  线上 server.py  <─────>  各设备浏览器
                          │
                    state.json（回收站 / 重命名 / 布局，全设备同步）
```

## 数据结构（data.js）

```js
window.DATA = {
  generated_at: "2026-10-19 15:42:24",   // 生成时间
  articles:      { "文章ID": { title, content_url, first_date } },
  article_total: { "文章ID": { read_user, share_user, ... } },   // 累计指标
  article_daily: { "日期": { "文章ID": { read_user, read_source, share_user, ... } } },
  biz_daily:     { "日期": { int_page_read_user, ... } },        // 公众号日报
  user_daily:    { "日期": { cancel, cumulate_user, new_user } },// 粉丝日报
  manual_titles: { "文章ID": "手动命名" },
  deleted_articles: [ ... ]                                       // 回收站快照
};
```

## API 接口

### 公共（读）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/state` | 共享状态：`{rev, deleted, titles, layout}` |
| GET | `/api/data` | 数据元信息（生成时间 / 大小 / 更新时间），供各端检测新数据 |
| GET | `/api/data/full` | 完整数据回拉 |

### 鉴权（写）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth` | `{pwd}` → `{ok, token, expires_in}`；密码错误返回 403 |
| POST | `/api/state` | `{token, rev, deleted, titles, layout}` 写共享状态 |
| POST | `/api/data` | `{token, data, generated_at}` 上传新数据 |

所有写接口只认 `token`；直接传明文密码一律 403。令牌有效期 8 小时，过期后页面自动提示重新解锁。

### 本地版额外接口

`/api/sync`（触发采集）、`/api/delete`、`/api/restore`、`/api/purge`、`/api/rename`、`/api/push`（手动同步线上）、`/api/upstream`（同步配置）、`/api/status`（采集任务状态）——均需令牌。

## 安全机制

1. **密码只在服务端**：页面源码不含密码；解锁时密码通过 `POST /api/auth` 发到服务端比对（比对失败有 0.4s 延迟，增加暴力尝试成本），成功后仅在 `sessionStorage` 保存临时令牌，关闭页面即失效
2. **令牌鉴权**：所有写操作凭令牌放行，令牌 8 小时过期；服务端密码可用环境变量 `ADMIN_PWD` 覆盖（线上版），默认 `change-me`，**部署后请立即修改**
3. **密钥隔离**：公众号 AppID / AppSecret 只保存在本机 `config.json`（已列入 `.gitignore`），服务不对外提供该文件；`token.json`（access_token 缓存）同样不对外
4. **上传校验**：数据上传时服务端对数据结构做完整性校验，非法数据拒绝写入
5. **门禁可见性**：未解锁时回收站、删除、重命名、布局保存等管理入口在 DOM 层面隐藏（`body:not(.admin)` 规则），而非仅视觉隐藏

## 已知限制

- 微信内打开 WorkBuddy 托管链接会被平台网关按 UA 拦截（返回 403 引导页），与本项目代码无关；请复制链接到浏览器打开，或使用版本一本地部署
- `article_daily` 等接口受微信官方回溯窗口限制（如 `datacube` 类接口一般只能取最近 7 天），采集脚本按日增量入库以累积历史
- 布局的「仅本次查看」保存于 `sessionStorage`，「保存为默认布局」写入云端 `state.json`

## 常见问题

**Q：如何更换管理密码？**
线上版：设置环境变量 `ADMIN_PWD` 后重启服务。本地版：修改 `serve.py` 中 `ACCESS_PWD`；同时修改 `config.json` 的 `upstream_pwd`（用于本地推送线上时的鉴权）。

**Q：采集时报 `40164 invalid ip not in whitelist` 怎么办？**
说明运行采集的电脑公网 IP 没加进白名单。按提示中的 IP 去「设置与开发 → 基本配置 → IP白名单」添加即可，详见[第二步](#第二步配置-ip-白名单最容易踩的坑)。

**Q：报 `48001`（无接口权限）怎么办？**
通常是公众号未认证，或没有「数据统计」接口权限。确认已做微信认证，并在「设置与开发 → 接口权限」中确认已获得数据统计类接口。

**Q：AppSecret 忘了 / 泄露了怎么办？**
在「设置与开发 → 基本配置」点击 AppSecret 的「重置」，管理员扫码后生成新的。注意重置后旧密钥立即失效，需同步更新 `config.json`。

**Q：更换了 AppSecret 后采集失败？**
检查 `config.json` 里的 `secret` 是否已更新为新值；同时确认 `token.json` 中缓存的旧访问令牌已失效——直接删除 `token.json` 后重新采集即可。

**Q：其他电脑怎么访问本地版？**
同一局域网内打开 `http://<本机IP>:8000`（`run.sh` 启动时会打印局域网地址）。

**Q：线上数据如何更新？**
两种方式：① 本地版采集后自动推送（推荐）；② 任意设备解锁后在页面「数据更新」上传 data.js 文件。

**Q：文章发布日期的统计口径？**
以文章在日报数据中**最早出现的日期**为准（页面加载时自动校正，采集端入库时按最小日期回填），可避免历史补扫造成的错标。

## 参与贡献

欢迎提交 Issue 与 Pull Request。请先阅读 [贡献指南](CONTRIBUTING.md) 与 [变更日志](CHANGELOG.md)。

提交前请确认代码中不含任何真实凭据（`appid`、`secret`、`access_token`）与业务数据。

## 开源协议

[MIT](LICENSE)。可自由使用、修改、分发与商用，需保留版权声明。

本项目内置的第三方库版权归原作者所有：

| 库 | 协议 |
|---|---|
| [Apache ECharts](https://echarts.apache.org/) | Apache-2.0 |
| [SheetJS Community Edition](https://sheetjs.com/) | Apache-2.0 |

## 致谢

本项目在开发过程中使用了 WorkBuddy（https://www.workbuddy.cn ）作为云端部署与测试平台。
