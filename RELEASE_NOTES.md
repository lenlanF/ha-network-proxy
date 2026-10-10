Network Proxy 0.4.3 开发预览：修复 Ingress 管理端口 8099 被其他服务占用时的启动退出。

应用改用 HA 支持的动态 Ingress：ingress_port 设置为 0，启动时通过 Supervisor 的 /addons/self/info 读取分配的端口。无需用户选择端口，应用选项仍仅包含 controller_secret。配套集成的三个内部地址保持不变，本次应用修复无需重启 HA Core。

管理 API 和 Ingress 监听在启动 Mihomo 或启用路由前预先绑定。冲突会明确报告监听角色和端口，并释放已绑定的监听；不会停止其他服务。Supervisor 分配异常不会静默回退至 8099。TUN 管理流量绕过同步包含动态端口，Ingress 来源检查保持启用。

隔离启动验证在 8099 已被其他监听占用时启动真实管理进程及内核，检查动态端口和来源限制；API 9091 占用时必须在内核和路由启动前退出。0.4.2 的 Unicode 修复继续提供。真实 HAOS 启动仍需更新后验收。
