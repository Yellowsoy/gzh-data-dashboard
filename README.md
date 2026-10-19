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
