"use strict";

const base = document.querySelector('meta[name="proxy-base"]').content;
const $ = (id) => document.getElementById(id);
let settings = {};
let ready = false;
const errors = {
  invalid_settings: "设置无效。请检查代理地址、端口、域名和网段；修改认证代理地址后需重新填写密码。",
  operation_failed: "操作失败，请检查代理源、认证信息和应用运行状态。",
  proxy_auth_required: "代理认证被拒绝（HTTP 407）。请核对共享代理的用户名和密码。",
  http_error: "代理或测试目标返回 HTTP 错误，请运行连接诊断。",
  tls_certificate_error: "HTTPS 证书验证失败。请检查上游代理或证书信任配置。",
  tls_error: "HTTPS 握手失败，请检查上游代理的 TLS 连接。",
  network_timeout: "连接超时。请检查上游节点和目标站点的连通性。",
  dns_error: "无法解析代理或目标主机名。",
  connection_refused: "代理连接被拒绝，请检查监听地址、端口和 LAN 共享。",
  connection_closed: "代理或目标提前关闭了连接。",
  network_error: "网络请求失败，请运行连接诊断并查看应用日志。",
  core_unavailable: "暂时无法连接 Mihomo 内核，请检查应用日志。",
  core_incompatible: "内核响应不兼容，请检查应用版本。",
  ingress_only: "请通过 Home Assistant 的应用页面打开。",
};

function notice(message, error = false) {
  const element = $("notice");
  element.textContent = message;
  element.className = error ? "notice error" : "notice";
  element.hidden = false;
}

async function api(path, method = "GET", data) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 90000);
  try {
    const response = await fetch(`${base}/api/${path}`, {
      method, signal: controller.signal, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Proxy-Request": "1" },
      body: data === undefined ? undefined : JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(errors[result.error] || "操作未完成，请检查应用状态。");
    return result;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时，请稍后刷新状态。");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

async function action(button, label, operation) {
  if (button.disabled) return;
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    await operation();
  } catch (error) {
    notice(error.message || "无法连接管理服务。", true);
  } finally {
    button.disabled = false;
    button.textContent = previous;
  }
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function drawNodes(status) {
  const container = $("nodes");
  container.replaceChildren();
  const nodes = status.nodes.filter((node) => node.name !== "REJECT");
  $("node-count").textContent = String(nodes.length);
  $("current-node").textContent = status.current === "REJECT" ? "未配置代理源" : (status.current || "—");
  if (!nodes.length) {
    container.append(element("p", "empty", "还没有可用节点。先添加订阅或局域网共享代理。"));
    return;
  }
  for (const node of nodes) {
    const selected = status.current === node.name;
    const row = element("div", selected ? "node selected" : "node");
    const main = element("div", "node-main");
    main.append(element("span", "node-name", node.name));
    const meta = element("span", "node-meta", `${node.type} · ${node.delay > 0 ? `${node.delay} ms` : "尚未测速"}`);
    main.append(meta);
    const actions = element("div", "node-actions");
    const speed = element("button", "secondary", "测速");
    speed.setAttribute("aria-label", `测速 ${node.name}`);
    speed.addEventListener("click", () => action(speed, "测试中", async () => {
      const result = await api("delay", "POST", { name: node.name });
      meta.textContent = `${node.type} · ${result.delay > 0 ? `${result.delay} ms` : "未能完成测试"}`;
    }));
    const select = element("button", selected ? "secondary" : "", selected ? "使用中" : "使用");
    select.setAttribute("aria-label", `使用 ${node.name}`);
    select.disabled = selected;
    select.addEventListener("click", () => action(select, "切换中", async () => {
      await api("select", "POST", { name: node.name });
      notice(`已切换到 ${node.name}`);
      await refreshStatus();
    }));
    actions.append(speed, select);
    row.append(main, actions);
    container.append(row);
  }
}

async function refreshEvents() {
  const result = await api("events");
  const container = $("events");
  container.replaceChildren();
  if (!result.events.length) container.append(element("p", "empty", "暂无记录"));
  for (const event of result.events.slice().reverse()) {
    const row = element("div", event.level === "error" ? "event error" : "event");
    const timestamp = element("time", "", new Date(event.time).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    timestamp.dateTime = event.time;
    row.append(timestamp, element("span", "", event.message));
    container.append(row);
  }
}

async function refreshStatus() {
  try {
    const status = await api("status");
    $("connection-dot").className = "status-dot connected";
    $("connection-text").textContent = "内核已连接";
    $("core-version").textContent = `Mihomo · ${status.version}`;
    $("core-state").textContent = status.configured ? "内核运行中" : "待配置代理源";
    $("core-state").className = "badge";
    $("refresh-subscription").disabled = !status.has_subscription;
    drawNodes(status);
  } catch (error) {
    $("connection-dot").className = "status-dot";
    $("connection-text").textContent = "内核连接中断";
    $("core-state").textContent = "连接中断";
    $("core-state").className = "badge error";
    $("current-node").textContent = "—";
    $("node-count").textContent = "—";
    $("refresh-subscription").disabled = true;
    $("nodes").replaceChildren(element("p", "empty", "无法读取节点。检查应用日志后刷新。"));
    throw error;
  }
  await refreshEvents();
}

async function loadSettings() {
  settings = await api("settings");
  $("subscription-url").value = settings.subscription_url || "";
  $("lan-host").value = settings.lan_host || "";
  $("lan-port").value = settings.lan_port || 7890;
  $("lan-type").value = settings.lan_type || "http";
  $("lan-user").value = settings.lan_username || "";
  $("lan-password").value = "";
  $("lan-password").placeholder = settings.has_lan_password ? "已保存；留空保留原密码" : "可选";
  $("password-note").textContent = settings.has_lan_password ? "留空保留密码；更换代理地址或用户名时，请重新填写密码。" : "不需要认证时，用户名和密码都留空。";
  $("clear-lan-auth").checked = false;
  for (const name of ["direct_domains", "proxy_domains", "direct_cidrs", "proxy_cidrs"]) {
    $(name.replaceAll("_", "-")).value = (settings[name] || []).join("\n");
  }
  $("default-action").value = settings.default_action || "proxy";
}

$("sources-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const button = event.submitter;
  action(button, "保存中", async () => {
    const source = {
      subscription_url: $("subscription-url").value.trim(), lan_host: $("lan-host").value.trim(),
      lan_port: Number($("lan-port").value), lan_type: $("lan-type").value,
      lan_username: $("lan-user").value,
    };
    if (!source.subscription_url && !source.lan_host) throw new Error("请添加订阅地址或共享代理地址。");
    if ($("clear-lan-auth").checked) {
      source.lan_username = "";
      source.lan_password = "";
    } else if ($("lan-password").value || !settings.has_lan_password) {
      source.lan_password = $("lan-password").value;
    }
    await api("settings", "PUT", source);
    await loadSettings();
    await refreshStatus();
    notice("代理源已保存并生效。通过节点列表选择需要的出口。");
  });
});

$("rules-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, "保存中", async () => {
    const rules = { default_action: $("default-action").value };
    for (const name of ["direct_domains", "proxy_domains", "direct_cidrs", "proxy_cidrs"]) {
      rules[name] = [...new Set($(name.replaceAll("_", "-")).value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean))];
    }
    await api("settings", "PUT", rules);
    await refreshStatus();
    notice("分流规则已保存并生效。规则只作用于经过代理出口的连接。");
  });
});

$("discover-form").addEventListener("submit", (event) => {
  event.preventDefault();
  action(event.submitter, "正在发现…", async () => {
    const container = $("discovery-results");
    container.replaceChildren(element("p", "empty", "正在验证代理协议，请稍候…"));
    try {
      const result = await api("discover", "POST", { cidr: $("cidr").value.trim() });
      container.replaceChildren();
      if (!result.proxies.length) container.append(element("p", "empty", "未发现代理。请确认设备已开启 LAN 共享，或手动填写地址和端口。"));
      for (const address of result.proxies) {
        const row = element("div", "discovery-row");
        const use = element("button", "secondary", "填入代理源");
        use.addEventListener("click", () => {
          const url = new URL(address);
          $("lan-host").value = url.hostname;
          $("lan-port").value = url.port;
          $("lan-type").value = url.protocol.replace(":", "");
          $("lan-user").value = "";
          $("lan-password").value = "";
          $("clear-lan-auth").checked = false;
          location.hash = "sources";
          notice("发现的代理已填入。需要认证时请填写凭据，然后保存代理源。");
        });
        row.append(element("span", "", address), use);
        container.append(row);
      }
      await refreshEvents();
    } catch (error) {
      container.replaceChildren(element("p", "empty", "扫描未完成。请检查网段后重新尝试。"));
      throw error;
    }
  });
});

$("refresh-status").addEventListener("click", (event) => action(event.currentTarget, "刷新中", refreshStatus));
$("refresh-subscription").addEventListener("click", (event) => action(event.currentTarget, "更新中", async () => {
  await api("refresh", "POST", {});
  await refreshStatus();
  notice("订阅已更新。");
}));
$("test-outlet").addEventListener("click", (event) => action(event.currentTarget, "测试中", async () => {
  $("test-result").textContent = "测试中…";
  try {
    const result = await api("test", "POST", {});
    $("test-result").textContent = `${result.latency_ms} ms`;
    notice(`代理出口收到 HTTP ${result.status} 响应。该测试验证出口，不验证 HA 集成的流量接入。`);
    await refreshEvents();
  } catch (error) {
    $("test-result").textContent = "未连通";
    throw error;
  }
}));

$("diagnose-outlet").addEventListener("click", (event) => action(event.currentTarget, "诊断中…", async () => {
  const container = $("diagnostic-results");
  container.replaceChildren(element("p", "empty", "正在检查认证与 HTTPS 连接，可能需要约 40 秒…"));
  try {
    const result = await api("diagnose", "POST", {});
    container.replaceChildren();
    for (const check of result.checks) {
      const stage = check.stage === "upstream_connect" ? "上游代理握手" : "HTTPS 出口";
      const detail = check.ok ? `成功 · HTTP ${check.http_status}${check.latency_ms === undefined ? "" : ` · ${check.latency_ms} ms`}` : (errors[check.code] || "连接失败");
      const row = element("p", check.ok ? "field-note" : "empty", `${stage} · ${check.target}：${detail}${!check.ok && check.http_status ? `（HTTP ${check.http_status}）` : ""}`);
      container.append(row);
    }
    await refreshEvents();
  } catch (error) {
    container.replaceChildren(element("p", "empty", "诊断未完成。"));
    throw error;
  }
}));

function activeNavigation() {
  const hash = location.hash || "#overview";
  for (const link of document.querySelectorAll("nav a")) link.classList.toggle("active", link.hash === hash);
}
window.addEventListener("hashchange", activeNavigation);
activeNavigation();

async function init() {
  const inputs = [...document.querySelectorAll("form button")];
  for (const button of inputs) button.disabled = true;
  try {
    await loadSettings();
    ready = true;
    await refreshStatus();
  } catch (error) {
    notice(error.message || "无法连接应用。", true);
  } finally {
    for (const button of inputs) button.disabled = !ready;
  }
}
init();
setInterval(() => {
  if (ready && !document.hidden) refreshStatus().catch(() => {});
}, 15000);
