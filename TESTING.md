# 测试与验证范围

安装开发依赖后运行 `python -m unittest discover -s tests -v` 和
`ruff check addons custom_components tests tools`。

26 项测试覆盖配置生成、输入验证、凭据保留、热加载回滚、管理请求鉴权、
实验路由的上下文与覆盖限制，以及真实 Mihomo 的 HTTP、HTTPS CONNECT、
认证 SOCKS5、节点订阅刷新和切换。

真实协议测试需要 Mihomo v1.19.12。设置 `MIHOMO_BINARY` 为二进制的绝对路径；
Windows 默认读取 `.tools/mihomo-windows-amd64-v1.exe`。
缺少二进制时真实内核测试会跳过，跳过不能视为通过。

GitHub Actions 分别在原生 amd64 和 aarch64 Linux 上构建容器，提取容器内核，
执行测试及容器内的证书、模块导入检查；通过后才发布对应镜像。
两种架构都通过后生成预览 Release、源码包、离线镜像包和 SHA256 校验文件。
具体运行结果以仓库 Actions 为准。

浏览器检查：先运行 `python -m playwright install chromium`，再运行
`python tools/verify_ui.py`。该检查使用真实后端和本地测试代理，检查桌面、
手机布局与交互；局域网发现的正向结果使用测试替身。

这些测试不证明 HA Core 的所有请求被代理。真实 HA OS 安装、Ingress、
应用持久化和辅助集成生命周期需要另外实机检查。
