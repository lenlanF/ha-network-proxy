# HA Network Proxy — 0.3.1 开发预览

Home Assistant OS 代理应用，配一个可选的轻量集成。订阅和局域网共享代理的管理放在应用中；集成提供实体、自动化及有明确覆盖限制的实验接入。

## 功能

- HA Ingress 管理页面，无需在网页再次填写密钥。
- Clash/Mihomo YAML 节点订阅，定时更新和手动刷新。
- LAN HTTP / SOCKS5 共享代理及用户名密码认证。
- 用户触发的私有 IPv4 网段发现，验证代理握手后选择保存。
- 节点切换、逐节点测速、出口连通性测试。
- 域名和 IPv4/IPv6 CIDR 分流，私有地址优先直连。
- 设置热加载、持久化、失败回滚，以及不包含凭据的管理操作记录。
- 可选 HA 实体与按集成实例选择的实验性 aiohttp 路由。

## 安装

在 HA 应用商店的“仓库”中添加：

`https://github.com/lenlanF/ha-network-proxy`

刷新后安装 Network Proxy。镜像由 GitHub Actions 构建并发布到 GHCR，
HA OS 直接下载对应架构的版本镜像，无需在 HA 上构建。
首次发布后需要将 GHCR 包设为 Public；HA 必须能够访问 GitHub 和 GHCR。

也可以使用 Release 中的源码包进行本地安装，目录结构：

```text
addons/network_proxy/                 → HA /addons/network_proxy/
custom_components/network_proxy/      → HA /config/custom_components/network_proxy/（可选）
```

1. 从上述仓库安装；或复制应用目录，刷新本地应用列表并安装。两种方式都拉取 GHCR 预构建镜像。
2. 设置至少 16 字符的随机 `controller_secret`，启动应用。
3. 点击“打开 Web 界面”，按需启用侧边栏入口。
4. 添加订阅或共享代理，保存，选择节点，再测试出口。
5. 需要实体和自动化时，安装可选集成并重启 HA。

本地安装的集成默认使用内部主机名 `local-network-proxy`，端口为控制 9090、管理 9091、出口 7890。从仓库安装时须改用对应的内部名称。管理与控制端口默认不映射到宿主 LAN。

完整安装、配置和排错步骤见 [应用说明](addons/network_proxy/DOCS.md)。

## 重要范围

**应用提供代理出口，不自动接管 HA Core 的网络请求。** 域名/IP 分流只作用于已经经过出口的连接。未实现宿主 TUN 透明转发。

集成的实验性按集成路由默认关闭，依赖 aiohttp 私有请求钩子和 HA 配置条目任务上下文。仅捕获符合条件的 aiohttp 请求，不覆盖 HTTPX、requests、原始 TCP/UDP 或无上下文请求。选择集成实例不能保证其全部流量都被覆盖；捕获计数也不能证明最终公网出口。

普通订阅以 Clash/Mihomo YAML 节点格式为测试范围，不提供其他格式转换。局域网发现为用户触发的扫描，并非后台 mDNS 广播发现；免认证 SOCKS5 与 HTTP CONNECT 入口可探测，认证 SOCKS5 及非标准端口可手动设置。

## 验证

详见 [TESTING.md](TESTING.md)。已进行本地 Python 测试、真实 Mihomo 协议转发与订阅测试，以及桌面/手机浏览器交互检查。Linux 镜像验证结果见仓库 Actions。Supervisor Ingress 和 HA 集成生命周期仍需实机确认。

管理页面截图使用本地测试代理和测试订阅节点，不是用户真实 HA 的运行截图。

## 开发

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\ruff.exe check addons custom_components tests tools
```

真实内核测试需要官方 Mihomo v1.19.12 的 Windows amd64-v1 二进制放在 `.tools/mihomo-windows-amd64-v1.exe`。没有该二进制时三项真实内核测试会跳过，不代表它们通过。浏览器检查另外需要 `python -m playwright install chromium`，随后运行 `python tools/verify_ui.py`。浏览器正向发现结果使用测试替身，不扫描真实 LAN。

打包：`python tools/package.py`。输出仅包含源码和文档，不含配置凭据、虚拟环境或内核二进制。

## 自定义集成显式接入

```python
from custom_components.network_proxy.proxy import async_request
async with async_request(hass, proxy_entry_id, "GET", "https://example.com") as response:
    response.raise_for_status()
    data = await response.text()
```

[架构评估](ARCHITECTURE.md) · [HA 应用](https://developers.home-assistant.io/docs/apps/) · [HA 内部通信](https://developers.home-assistant.io/docs/apps/communication/) · [Mihomo](https://github.com/MetaCubeX/mihomo/tree/v1.19.12)
