# Network Proxy

为 Home Assistant OS 提供 Mihomo 代理出口。支持节点订阅、局域网 HTTP/SOCKS5 共享代理、网段发现、节点切换和域名/IP 分流。

通过 HA Ingress 管理页面设置，不需要安装辅助集成即可使用应用。辅助集成可提供实体、自动化及实验性按集成接入功能。

当前为 0.3.1 开发预览。amd64/aarch64 镜像由 GitHub Actions 构建并测试，HA 直接下载预构建镜像；实机运行仍需验证。应用提供出口；HA Core 的流量接入需要显式代理配置或客户端适配。

