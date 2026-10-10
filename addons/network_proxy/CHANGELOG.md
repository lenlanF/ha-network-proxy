# Changelog

## Unreleased

## 0.4.0 — 开发预览

- 保存订阅立即拉取完整配置，保留配套策略组、规则及 DNS；网页支持 YAML 扩展和失败回滚。
- 出口按订阅配置 / LAN 共享代理选择；增加规则、全局和直连模式。
- 网页统一管理全部业务设置，应用选项仅有控制密钥，内部连接地址可复制。
- 增加整台 HAOS TUN、部分 bridge 应用 IPv4 选择、配套集成目标同步，以及可选 LAN 共享和独立出口认证。
- 双架构 CI 增加隔离 Linux TCP/UDP/DNS/IPv6 与路由恢复验证；实机验收另行进行。

- Make local outlet username/password optional in the HA connection and options forms; unauthenticated outlets can be configured without dummy credentials.

- 局域网发现增加 OpenClash 混合代理端口 7893；仍通过代理协议握手验证入口。

## 0.3.2 — 开发预览

- 出口测试返回安全的错误分类，区分认证、超时、DNS、连接和 TLS 错误，不回传原始异常或凭据。
- 增加连接诊断，检查已保存的 HTTP 上游认证及两个固定 HTTPS 目标。
- HTTP 407 提示核对上游认证；握手结果与实际 HTTPS 连通性分别显示。
- 保留现有代理源、密码和分流规则，升级不需要重新填写认证。

## 0.3.1 — 开发预览

- 增加 GitHub Actions 原生 amd64/aarch64 构建和真实代理协议验证。
- HA 从 GHCR 安装预构建镜像，Release 提供离线镜像及源码包。
- 运行镜像包含 Mihomo 许可证及对应源码说明。

## 0.3.0 — 开发预览

- 增加 HA Ingress 管理页面及桌面/手机布局。
- 管理订阅、局域网共享代理、节点、域名/IP 分流和运行记录。
- 增加出口测试及节点测速，设置热加载并持久化。
- 控制和管理 API 默认不暴露到宿主 LAN。
- 真实 Mihomo 的 HTTP、HTTPS CONNECT、认证 SOCKS5、订阅和节点切换本地测试通过。

## 0.2.0

- 增加源管理 API、配置持久化和失败回滚。
- 增加实验性按 HA 配置条目上下文的 aiohttp 路由。

## 0.1.0

- 初始内核封装、辅助集成和按需网段发现。
