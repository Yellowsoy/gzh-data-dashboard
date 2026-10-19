# 贡献指南

感谢关注本项目。这是一个面向微信公众号运营数据的小型可视化看板，欢迎提交 Issue 或 Pull Request。

## 提交 Issue

请说明：

- 运行环境（操作系统、Python 版本、浏览器）
- 部署方式（本地部署 / WorkBuddy 部署）
- 复现步骤与预期结果
- 相关日志（本地版可提供 `sync.log`，注意先去除敏感信息）

## 提交 Pull Request

1. Fork 本仓库并新建分支：`git checkout -b feature/your-feature`
2. 保持改动聚焦，一个 PR 只解决一个问题
3. 前端改动请在 Chrome / Edge / 移动端分别自测，确认无控制台报错、无横向溢出
4. 后端改动请用 Python 3.8+ 验证（项目仅使用标准库，**不要引入第三方依赖**）
5. 提交前确认未包含任何真实凭据：`appid`、`secret`、`access_token`、`data.js`、`store.json`

提交信息建议使用如下前缀：`feat:` 新功能、`fix:` 修复、`docs:` 文档、`style:` 样式、`refactor:` 重构。

## 代码约定

- 前端为单文件 `index.html`，不引入构建工具；新增逻辑请沿用现有的 IIFE + 原生 DOM 写法
- Python 代码遵循 PEP 8，注释使用中文
- 所有新增的写操作接口必须校验服务端令牌（`token`），不得以明文密码作为鉴权手段

## 安全问题

如发现安全漏洞（如鉴权绕过、越权写入），请勿直接提交公开 Issue，改为通过邮件私下告知维护者。
