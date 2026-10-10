# HA Network Proxy — 0.4.0 开发预览

Home Assistant OS 应用 + 配套集成。应用运行 Mihomo，网页统一管理代理；配套集成提供出口实体、订阅刷新和按集成请求接入。

## 功能

- 保存订阅时立即拉取 Clash / Mihomo YAML 或 JSON 完整配置，保留策略组、规则、DNS 与提供者。只有节点的订阅自动生成选择组。
- 网页 YAML 扩展：对象递归合并，列表整体替换；`prepend-rules` 放在订阅规则前，`append-rules` 放在 MATCH 前。
- 出口只选择“订阅配置”或“局域网共享代理”。实际节点由订阅策略组决定。
- 按需发现局域网 HTTP / SOCKS5 入口，包含 OpenClash 常用 7893。
- 分别设置接入范围（显式 / 整台 HAOS / 部分应用与集成）和内核模式（规则 / 全局 / 直连）。
- 两种来源均可向局域网分享 HTTP / SOCKS5 出口，可设置独立出口认证。
- 应用选项只保留 `controller_secret`；订阅、扩展、上游认证、目标和共享都在网页配置。

## 范围与边界

| 接入范围 | 覆盖 |
| --- | --- |
| 显式代理出口 | 主动指定此代理的客户端；不透明接入 HAOS |
| 整台 HAOS | 通过主机 TUN 接入 Core 与应用的公网 TCP / UDP；包含 IPv6；私有地址保留直连 |
| 部分应用 | 按 Supervisor bridge 容器 IPv4 来源接入 TCP / UDP；host-network 应用无法单独隔离 |
| 部分集成 | 带 HA config-entry 上下文的 aiohttp 请求；HTTPX、requests、原始套接字和丢失上下文的请求不覆盖 |

按集成功能仍使用私有 aiohttp 钩子，需观察 HA 捕获计数核验覆盖。UDP 还取决于上游能力：HTTP 上游不承载 UDP。透明代理需要 `/dev/net/tun`、NET_ADMIN 和 NET_RAW；不需要 Docker API、主机 PID、Supervisor manager/admin 权限。

0.4.0 仍是开发预览。Linux 网络验证结果以对应提交的 GitHub Actions 为准，隔离环境通过不等于实机 HAOS 验收。

## 安装与配置

1. HA → 设置 → 应用 → 应用商店 → 仓库，添加 `https://github.com/lenlanF/ha-network-proxy`。
2. 安装 Network Proxy，选项里设置自己生成的至少 16 字符随机 `controller_secret`。
3. 启动并打开 Web 界面，保存订阅或选择发现的 LAN 代理，完成出口测试。
4. 将 `custom_components/network_proxy` 复制到 HA 的 `/config/custom_components/`，在合适时间重启 Core，添加 Network Proxy 集成。
5. 从网页“连接配套 HA 集成”复制三个内部地址，密钥填写步骤 2 自己设置的值。无需在集成连接表单重复填写出口用户名/密码。
6. 在网页选择流量范围、内核模式和目标。配套集成每 10 秒同步网页设置，不会自动重载其他集成。

内部地址是 `http://127.0.0.1:9090`（控制）、`:9091`（管理）和 `:7890`（出口），Core 与应用使用主机网络。控制和管理 API 只监听回环。共享开启时，其他设备用 HA 局域网 IP 的 7890 端口。

首次订阅保存执行“拉取 → 合并 → 内核校验 → 加载 → 持久化”。失败保留原配置和快照；启动优先读取私有快照。提供者仍按订阅自己的 interval 更新；完整订阅默认每小时更新，可在网页设置间隔或关闭自动更新；“更新订阅”可立即重新拉取。

## YAML 示例

假设订阅有策略组 `自动选择`：

```yaml
prepend-rules:
  - DOMAIN-SUFFIX,example.cn,DIRECT
append-rules:
  - DOMAIN-SUFFIX,example.com,自动选择
dns:
  nameserver:
    - system
```

规则中的策略组必须存在。端口、TUN、控制 API、认证等由应用管理，扩展不能覆盖。仅支持 HTTP / inline 提供者；文件路径限定在应用数据目录。

## 从 0.3.x 升级

保留 `/data/sources.json` 中的上游、认证和简易规则。旧 Supervisor 选项的业务字段一次迁移到网页存储；选项最终仅有密钥。默认仍是显式模式，升级不会自动接管整台 HAOS。网页保留“已有的简易分流规则”编辑区。

应用改为 host-network，需检查端口 7890、8099、9090、9091、1053 没有冲突。辅助集成也要升级至 0.4.0；常见旧应用主机名会迁移到回环地址，其他旧连接地址需更新为网页提供的地址；原来在集成选项中选择的目标需改在网页重新选择。备份配置，并在可接受的维护时间升级和重启。

## 构建

GitHub Actions 原生构建 amd64 / aarch64；HAOS 安装 GHCR 预构建镜像。PR 测试不发布镜像；发布流程通过真实协议和隔离 Linux 网络测试后才推送镜像。详情见 [TESTING.md](TESTING.md)。

透明接入时应用固定使用 redir-host DNS 模式，避免局域网名称被转换为虚拟地址；订阅的上游 DNS 与其他 DNS 策略仍保留，常见本地名称默认使用系统 DNS。
