Network Proxy 开发预览：订阅、LAN HTTP/SOCKS5 共享代理、网段发现、节点切换和域名/IP 分流。

镜像由 GitHub Actions 分别在 amd64/aarch64 原生机器构建，并运行真实 Mihomo 协议测试。HA 使用 GHCR 预构建镜像，无需本机 Docker 构建工具。首次发布的 GHCR 包必须设为 Public 才能匿名拉取。

源码包可安装辅助集成；镜像压缩包用于网络故障下的人工导入，并附 SHA256。应用并不自动接管 HA Core 全部流量；实验按集成路由默认关闭且覆盖有限。真实 HA OS Ingress 和生命周期验收仍需完成。
