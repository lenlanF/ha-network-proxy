"use strict";
const base = document.querySelector('meta[name="proxy-base"]').content;
const $ = (id) => document.getElementById(id);
let settings = {}, targets = {}, ready = false;
const scopes = {explicit: "显式出口", haos: "整台 HAOS", selected: "部分应用 / 集成"};
const errors = {
  invalid_settings: "设置无效，请检查地址、端口、认证和目标。更换上游地址或用户名时需重新填写密码。",
  invalid_yaml: "YAML 格式无效、存在重复键或内容过于复杂。",
  subscription_invalid: "订阅必须是有效的 Clash / Mihomo 配置。",
  subscription_empty: "订阅没有节点或代理提供者。",
  subscription_fetch_failed: "订阅拉取失败，旧配置已保留。请检查链接和网络。",
  subscription_not_ready: "订阅尚未拉取，请重新保存订阅地址。",
  config_too_large: "订阅配置超过 2 MiB 限制。",
  provider_unsupported: "只支持 HTTP 或 inline 提供者，不支持读取任意本地文件。",
  managed_setting: "扩展不能修改应用管理的端口、认证、TUN 或控制 API 设置。",
  reserved_group: "订阅使用了应用保留的策略组名称 NETWORK_PROXY_OUTLET。",
  core_validation_failed: "Mihomo 校验失败，请检查策略组引用、节点参数和扩展规则。旧配置已保留。",
  core_validation_timeout: "Mihomo 校验超时，旧配置已保留。",
  tun_unavailable: "透明代理不可用，请检查应用网络权限及 /dev/net/tun。",
  tun_not_started: "TUN 未成功启动，已尝试恢复原配置。",
  traffic_apply_failed: "系统路由设置失败，已尝试恢复原配置。",
  app_not_isolatable: "所选应用无法单独隔离，或已卸载。请更新目标选择。",
  supervisor_unavailable: "无法读取 Supervisor 应用信息，请稍后重试。",
  integration_unavailable: "配套集成未连接，或所选集成已禁用。请先连接辅助集成。",
  source_required: "请先配置一个可用出口，再启用透明代理。",
  proxy_auth_required: "上游认证被拒绝（HTTP 407），请核对共享代理账户。",
  http_error: "代理或目标返回 HTTP 错误，请运行连接诊断。",
  tls_certificate_error: "HTTPS 证书校验失败。",
  tls_error: "TLS 握手失败。", network_timeout: "连接超时。", dns_error: "DNS 解析失败。",
  connection_refused: "连接被拒绝，请检查监听端口。", connection_closed: "连接提前关闭。",
  network_error: "网络请求失败，请运行诊断。", core_unavailable: "内核暂时不可用，请查看应用日志。",
  core_incompatible: "内核响应不兼容。", ingress_only: "请从 Home Assistant 应用页面打开。",
};
function notice(message, error = false) { $("notice").textContent = message; $("notice").className = error ? "notice error" : "notice"; $("notice").hidden = false; }
function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
async function api(path, method = "GET", data) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 120000);
  try {
    const response = await fetch(`${base}/api/${path}`, {method, signal: controller.signal, credentials: "same-origin", headers: {"Content-Type": "application/json", "X-Proxy-Request": "1"}, body: data === undefined ? undefined : JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(errors[result.error] || "操作未完成，请检查应用状态。");
    return result;
  } catch (error) { if (error.name === "AbortError") throw new Error("请求超时，请刷新确认是否保存成功。"); throw error; }
  finally { clearTimeout(timer); }
}
async function action(button, label, operation) {
  if (button.disabled) return;
  const previous = button.textContent; button.disabled = true; button.textContent = label;
  try { await operation(); } catch (error) { notice(error.message || "无法连接应用。", true); }
  finally { button.disabled = false; button.textContent = previous; }
}
function drawOutlets(status) {
  $("current-node").textContent = status.configured ? status.current_label : "未配置";
  $("nodes").replaceChildren();
  for (const outlet of status.outlets) {
    const selected = status.configured && status.current === outlet.name;
    const row = element("div", selected ? "node selected" : "node"), main = element("div", "node-main");
    main.append(element("span", "node-name", outlet.label), element("span", "node-meta", selected ? "使用中" : outlet.ready ? "已配置" : "尚未配置"));
    const use = element("button", "secondary", selected ? "使用中" : "使用");
    use.setAttribute("aria-label", `使用 ${outlet.label}`); use.disabled = selected || !outlet.ready;
    use.addEventListener("click", () => action(use, "切换中", async () => { await api("select", "POST", {name: outlet.name}); await loadSettings(); await refreshStatus(); notice(`已切换到${outlet.label}。`); }));
    row.append(main, use); $("nodes").append(row);
  }
  const sub = status.subscription;
  $("subscription-summary").textContent = status.has_subscription ? `上次拉取：${sub.fetched_at ? new Date(sub.fetched_at * 1000).toLocaleString("zh-CN") : "尚未完成"}${status.current === "subscription" ? ` · ${sub.groups} 个策略组 · ${sub.rules} 条规则（含本地直连）` : ""}` : "订阅会在首次保存时立即拉取。";
}
async function refreshEvents() {
  const result = await api("events"); $("events").replaceChildren();
  if (!result.events.length) $("events").append(element("p", "empty", "暂无记录"));
  for (const item of result.events.slice().reverse()) { const row = element("div", item.level === "error" ? "event error" : "event"); row.append(element("time", "", new Date(item.time).toLocaleTimeString("zh-CN")), element("span", "", item.message)); $("events").append(row); }
}
async function refreshStatus() {
  try {
    const status = await api("status"); $("connection-dot").className = "status-dot connected"; $("connection-text").textContent = "内核已连接"; $("core-version").textContent = `Mihomo · ${status.version}`;
    $("core-state").textContent = status.configured ? "内核运行中" : "待配置代理源";
    $("refresh-subscription").disabled = !status.has_subscription;
    $("traffic-state").textContent = scopes[status.traffic_scope];
    $("traffic-detail").textContent = status.traffic_scope === "haos" ? "TUN 接入 TCP / UDP" : status.traffic_scope === "selected" ? `${(status.traffic.ips || []).length} 个运行中的应用接入` : "设备或客户端主动设置代理";
    $("scope-note").textContent = status.traffic_scope === "selected" ? "集成请求由配套集成接入。勾选集成并不表示其所有网络客户端均已覆盖。" : status.traffic_scope === "haos" ? "整台 HAOS 的出站连接接入代理；管理入口与私有地址直连。" : "当前仅提供代理出口，未透明接入 HAOS 出站流量。";
    $("integration-state").textContent = status.integration_connected ? "配套集成已连接，每 10 秒同步网页设置。" : "配套集成尚未连接。添加后可在本网页选择集成实例。";
    drawOutlets(status);
  } catch (error) { $("connection-dot").className = "status-dot"; $("connection-text").textContent = "连接中断"; $("core-state").textContent = "连接中断"; throw error; }
  await refreshEvents();
}
function modeNote() {
  const mode = $("traffic-mode").value; $("target-panel").hidden = mode !== "selected";
  $("mode-note").textContent = mode === "haos" ? "接入 HA Core 与应用出站流量，需要 NET_ADMIN 和 TUN。按配置规则决定代理或直连；私有地址绕过代理。" : mode === "selected" ? "选择要接入的应用或集成；两者的可覆盖范围不同。保存后集成设置最多约 10 秒生效。" : "提供本机代理出口，客户端需主动指定代理。";
}
function drawTargets(kind, items, selected, unavailable) {
  const parent = $(kind === "app" ? "app-targets" : "integration-targets"); parent.replaceChildren();
  if (!items.length) parent.append(element("p", "empty", unavailable));
  const known = new Set(items.map((item) => item.id));
  const all = [...items, ...selected.filter((id) => !known.has(id)).map((id) => ({id, name: id, missing: true}))];
  for (const item of all) {
    const label = element("label", "check target"), input = element("input"); input.type = "checkbox"; input.value = item.id; input.dataset.kind = kind; input.checked = selected.includes(item.id);
    const unsupported = kind === "app" ? !item.supported : item.disabled || !targets.integration_connected;
    // Existing stale selections remain removable rather than silently disappearing.
    input.disabled = !!unsupported && !input.checked;
    const reason = item.missing ? "已移除，请取消选择" : kind === "app" && !item.supported ? item.reason === "host_network" ? "共享主机网络，无法单独隔离" : "暂无独立网络地址" : kind === "integration" && item.disabled ? "已禁用" : kind === "integration" && !targets.integration_connected ? "辅助集成未连接" : kind === "app" ? item.state === "started" ? "运行中" : "尚未启动" : item.domain;
    const text = element("span"); text.append(element("span", "target-name", item.name), element("small", "", reason)); label.append(input, text); parent.append(label);
  }
}
async function copyText(text, button) {
  let copied = false;
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(text); copied = true; } catch { /* Ingress may restrict clipboard permissions. */ }
  }
  if (!copied) {
    const input = button.previousElementSibling; input.focus(); input.select();
    copied = document.execCommand("copy");
  }
  notice(copied ? "内部地址已复制。" : "无法自动复制，请选中地址手动复制。", !copied);
}
async function loadTargets() {
  targets = await api("targets");
  drawTargets("app", targets.apps, settings.selected_apps || [], "无法读取应用列表，或没有已安装应用。");
  drawTargets("integration", targets.integrations, settings.selected_integrations || [], "请先连接配套 HA 集成。");
  $("connection-addresses").replaceChildren();
  const labels = {controller_url: "控制 API 地址", manager_url: "应用管理地址", proxy_url: "HTTP 代理出口"};
  for (const [key, value] of Object.entries(targets.addresses)) { const row = element("div", "copy-row"), label = element("label", "", labels[key]), input = element("input"), button = element("button", "secondary", "复制"); input.id = `copy-${key}`; input.value = value; input.readOnly = true; label.htmlFor = input.id; button.type = "button"; button.setAttribute("aria-label", `复制${labels[key]}`); button.addEventListener("click", () => copyText(value, button)); row.append(label, input, button); $("connection-addresses").append(row); }
  $("share-addresses").textContent = targets.lan_addresses.length ? `共享地址：${targets.lan_addresses.map((ip) => `${ip}:7890`).join("、")}` : "共享地址：HA 的局域网 IP，端口 7890。";
}
async function loadSettings() {
  settings = await api("settings");
  for (const [id, key, fallback] of [["subscription-url", "subscription_url", ""], ["extension-yaml", "extension_yaml", ""], ["outlet-source", "outlet_source", "subscription"], ["lan-host", "lan_host", ""], ["lan-port", "lan_port", 7890], ["lan-type", "lan_type", "http"], ["lan-user", "lan_username", ""], ["proxy-user", "proxy_username", ""], ["traffic-mode", "traffic_mode", "explicit"], ["core-mode", "core_mode", "rule"]]) $(id).value = settings[key] ?? fallback;
  for (const kind of ["lan", "proxy"]) { $(`${kind}-password`).value = ""; $(`${kind}-password`).placeholder = settings[`has_${kind}_password`] ? "已保存，留空保留" : "可选"; $(`clear-${kind}-auth`).checked = false; }
  $("password-note").textContent = settings.has_lan_password ? "已保存密码，更换地址或用户名时需重新填写。" : "不需要认证时，用户名和密码均留空。";
  $("share-enabled").checked = !!settings.share_enabled;
  for (const key of ["direct_domains", "proxy_domains", "direct_cidrs", "proxy_cidrs"]) $(key.replaceAll("_", "-")).value = (settings[key] || []).join("\n");
  $("default-action").value = settings.default_action || "proxy";
  await loadTargets(); modeNote();
}
function credentials(data, kind) {
  data[`${kind}_username`] = $(kind === "lan" ? "lan-user" : "proxy-user").value;
  if ($(`clear-${kind}-auth`).checked) { data[`${kind}_username`] = ""; data[`${kind}_password`] = ""; }
  else if ($(`${kind}-password`).value || !settings[`has_${kind}_password`]) data[`${kind}_password`] = $(`${kind}-password`).value;
}
function form(id, build, message) { $(id).addEventListener("submit", (event) => { event.preventDefault(); action(event.submitter, "校验并保存中…", async () => { await api("settings", "PUT", build()); await loadSettings(); await refreshStatus(); notice(message); }); }); }
form("sources-form", () => { const data = {subscription_url: $("subscription-url").value.trim(), extension_yaml: $("extension-yaml").value, outlet_source: $("outlet-source").value, lan_host: $("lan-host").value.trim(), lan_port: Number($("lan-port").value), lan_type: $("lan-type").value}; if (!data.lan_host && data.subscription_url) data.outlet_source = "subscription"; credentials(data, "lan"); return data; }, "代理配置已拉取、校验并保存。");
form("traffic-form", () => ({traffic_mode: $("traffic-mode").value, core_mode: $("core-mode").value, selected_apps: [...document.querySelectorAll('input[data-kind="app"]:checked')].map((input) => input.value), selected_integrations: [...document.querySelectorAll('input[data-kind="integration"]:checked')].map((input) => input.value)}), "流量模式已保存；配套集成最多约 10 秒同步。");
form("sharing-form", () => { const data = {share_enabled: $("share-enabled").checked}; credentials(data, "proxy"); return data; }, "共享设置已保存。");
form("rules-form", () => { const data = {default_action: $("default-action").value}; for (const key of ["direct_domains", "proxy_domains", "direct_cidrs", "proxy_cidrs"]) data[key] = [...new Set($(key.replaceAll("_", "-")).value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean))]; return data; }, "简易规则已保存。");
$("traffic-mode").addEventListener("change", modeNote);
$("discover-form").addEventListener("submit", (event) => { event.preventDefault(); action(event.submitter, "正在发现…", async () => { $("discovery-results").replaceChildren(element("p", "empty", "正在验证代理协议…")); const result = await api("discover", "POST", {cidr: $("cidr").value.trim()}); $("discovery-results").replaceChildren(); if (!result.proxies.length) $("discovery-results").append(element("p", "empty", "未发现代理，可手动填写地址。")); for (const address of result.proxies) { const row = element("div", "discovery-row"), use = element("button", "secondary", "填入代理配置"); use.addEventListener("click", () => { const url = new URL(address); $("lan-host").value = url.hostname; $("lan-port").value = url.port; $("lan-type").value = url.protocol.replace(":", ""); $("lan-user").value = ""; $("lan-password").value = ""; $("outlet-source").value = "lan"; location.hash = "sources"; notice("已填入发现的代理，填写认证后保存。 "); }); row.append(element("span", "", address), use); $("discovery-results").append(row); } await refreshEvents(); }); });
$("refresh-status").addEventListener("click", (event) => action(event.currentTarget, "刷新中", async () => { await loadSettings(); await refreshStatus(); }));
$("refresh-subscription").addEventListener("click", (event) => action(event.currentTarget, "更新中", async () => { await api("refresh", "POST", {}); await refreshStatus(); notice("订阅已重新拉取并加载。"); }));
$("test-outlet").addEventListener("click", (event) => action(event.currentTarget, "测试中", async () => { $("test-result").textContent = "测试中…"; try { const result = await api("test", "POST", {}); $("test-result").textContent = `${result.latency_ms} ms`; notice(`出口收到 HTTP ${result.status} 响应。此测试验证代理出口，流量接入需另行核验。`); await refreshEvents(); } catch (error) { $("test-result").textContent = "未连通"; throw error; } }));
$("diagnose-outlet").addEventListener("click", (event) => action(event.currentTarget, "诊断中", async () => { $("diagnostic-results").replaceChildren(element("p", "empty", "正在检查认证与 HTTPS…")); const result = await api("diagnose", "POST", {}); $("diagnostic-results").replaceChildren(); for (const check of result.checks) { const stage = check.stage === "upstream_connect" ? "上游代理握手" : "HTTPS 出口"; $("diagnostic-results").append(element("p", "field-note", `${stage} · ${check.target}：${check.ok ? `成功 · HTTP ${check.http_status}` : errors[check.code] || "失败"}`)); } await refreshEvents(); }));
function navigation() { for (const link of document.querySelectorAll("nav a")) link.classList.toggle("active", link.hash === (location.hash || "#overview")); }
window.addEventListener("hashchange", navigation); navigation();
async function init() { const buttons = [...document.querySelectorAll("form button")]; buttons.forEach((b) => b.disabled = true); try { await loadSettings(); ready = true; await refreshStatus(); } catch (error) { notice(error.message, true); } finally { buttons.forEach((b) => b.disabled = !ready); } }
init(); setInterval(() => { if (ready && !document.hidden) refreshStatus().catch(() => {}); }, 15000);
