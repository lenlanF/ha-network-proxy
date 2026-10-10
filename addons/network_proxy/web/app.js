"use strict";
const base = document.querySelector('meta[name="proxy-base"]').content;
const $ = (id) => document.getElementById(id);
let settings = {}, targets = {}, ready = false;
const scopes = {explicit: "��ʽ����", haos: "��̨ HAOS", selected: "����Ӧ�� / ����"};
const errors = {
  invalid_settings: "������Ч�������ַ���˿ڡ���֤��Ŀ�ꡣ�������ε�ַ���û���ʱ��������д���롣",
  upstream_loop: "���ܰѱ�Ӧ�õĳ������ó��Լ������Ρ�",
  invalid_yaml: "YAML ��ʽ��Ч�������ظ��������ݹ��ڸ��ӡ�",
  subscription_invalid: "���ı�������Ч�� Clash / Mihomo ���á�",
  subscription_empty: "����û�нڵ������ṩ�ߡ�",
  subscription_fetch_failed: "������ȡʧ�ܣ��������ѱ������������Ӻ����硣",
  subscription_not_ready: "������δ��ȡ�������±��涩�ĵ�ַ��",
  config_too_large: "�������ó��� 2 MiB ���ơ�",
  provider_unsupported: "ֻ֧�� HTTP �� inline �ṩ�ߣ���֧�ֶ�ȡ���Ȿ���ļ���",
  managed_setting: "��չ�����޸�Ӧ�ù����Ķ˿ڡ���֤��TUN ����� API ���á�",
  reserved_group: "����ʹ����Ӧ�ñ����Ĳ��������� NETWORK_PROXY_OUTLET��",
  core_validation_failed: "Mihomo У��ʧ�ܣ�������������á��ڵ��������չ���򡣾������ѱ�����",
  core_validation_timeout: "Mihomo У�鳬ʱ���������ѱ�����",
  core_validation_geodata: "Mihomo �����������ݼ���ʧ�ܣ����� GeoIP / GeoSite ���ݼ��������硣",
  core_validation_permission: "Mihomo �޷���д���û����ݣ�����Ӧ������Ŀ¼Ȩ�ޡ�",
  core_validation_proxy_group: "Mihomo ������У��ʧ�ܣ����� use / proxies�������ú� YAML ��չ��",
  core_validation_provider: "Mihomo �ṩ��У��ʧ�ܣ��������������ṩ�ߵ����͡�������·����",
  core_validation_rule: "Mihomo ����У��ʧ�ܣ���������﷨�������õĲ����顣",
  core_validation_dns: "Mihomo DNS ����У��ʧ�ܣ����� nameserver �ͽ������ԡ�",
  core_validation_proxy: "Mihomo �ڵ����У��ʧ�ܣ�����ڵ����ͼ�Э�������",
  core_validation_yaml: "Mihomo �����ֶλ�������Ч������ YAML ��չ��",
  startup_recovery: "��������δ���أ���������ҳ����������������á�",
  tun_unavailable: "͸�����������ã�����Ӧ������Ȩ�޼� /dev/net/tun��",
  tun_not_started: "TUN δ�ɹ��������ѳ��Իָ�ԭ���á�",
  traffic_apply_failed: "ϵͳ·������ʧ�ܣ��ѳ��Իָ�ԭ���á�",
  app_not_isolatable: "��ѡӦ���޷��������룬����ж�ء������Ŀ��ѡ��",
  supervisor_unavailable: "�޷���ȡ Supervisor Ӧ����Ϣ�����Ժ����ԡ�",
  integration_unavailable: "���׼���δ���ӣ�����ѡ�����ѽ��á��������Ӹ������ɡ�",
  source_required: "��������һ�����ó��ڣ�������͸��������",
  proxy_auth_required: "������֤���ܾ���HTTP 407������˶Թ��������˻���",
  http_error: "������Ŀ�귵�� HTTP ����������������ϡ�",
  tls_certificate_error: "HTTPS ֤��У��ʧ�ܡ�",
  tls_error: "TLS ����ʧ�ܡ�", network_timeout: "���ӳ�ʱ��", dns_error: "DNS ����ʧ�ܡ�",
  connection_refused: "���ӱ��ܾ�����������˿ڡ�", connection_closed: "������ǰ�رա�",
  network_error: "��������ʧ�ܣ���������ϡ�", core_unavailable: "�ں���ʱ�����ã���鿴Ӧ����־��",
  core_incompatible: "�ں���Ӧ�����ݡ�", ingress_only: "��� Home Assistant Ӧ��ҳ��򿪡�",
};
function notice(message, error = false) { $("notice").textContent = message; $("notice").className = error ? "notice error" : "notice"; $("notice").hidden = false; }
function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
async function api(path, method = "GET", data) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 120000);
  try {
    const response = await fetch(`${base}/api/${path}`, {method, signal: controller.signal, credentials: "same-origin", headers: {"Content-Type": "application/json", "X-Proxy-Request": "1"}, body: data === undefined ? undefined : JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(errors[result.error] || "����δ��ɣ�����Ӧ��״̬��");
    return result;
  } catch (error) { if (error.name === "AbortError") throw new Error("����ʱ����ˢ��ȷ���Ƿ񱣴�ɹ���"); throw error; }
  finally { clearTimeout(timer); }
}
async function action(button, label, operation) {
  if (button.disabled) return;
  $("notice").hidden = true; $("notice").textContent = "";
  const previous = button.textContent; button.disabled = true; button.textContent = label;
  try { await operation(); } catch (error) { notice(error.message || "�޷�����Ӧ�á�", true); }
  finally { button.disabled = false; button.textContent = previous; }
}
function drawOutlets(status) {
  $("current-node").textContent = status.configured ? status.current_label : "δ����";
  $("nodes").replaceChildren();
  for (const outlet of status.outlets) {
    const selected = status.configured && status.current === outlet.name;
    const row = element("div", selected ? "node selected" : "node"), main = element("div", "node-main");
    main.append(element("span", "node-name", outlet.label), element("span", "node-meta", selected ? "ʹ����" : outlet.ready ? "������" : "��δ����"));
    const use = element("button", "secondary", selected ? "ʹ����" : "ʹ��");
    use.setAttribute("aria-label", `ʹ�� ${outlet.label}`); use.disabled = selected || !outlet.ready;
    use.addEventListener("click", () => action(use, "�л���", async () => { await api("select", "POST", {name: outlet.name}); await loadSettings(); await refreshStatus(); notice(`���л���${outlet.label}��`); }));
    row.append(main, use); $("nodes").append(row);
  }
  const sub = status.subscription;
  $("subscription-summary").textContent = status.has_subscription ? `�ϴ���ȡ��${sub.fetched_at ? new Date(sub.fetched_at * 1000).toLocaleString("zh-CN") : "��δ���"}${status.recovery_error ? " �� ������δ����" : status.current === "subscription" ? ` �� ${sub.groups} �������� �� ${sub.rules} �����򣨺�����ֱ����` : ""}` : "���Ļ����״α���ʱ������ȡ��";
}
async function refreshEvents() {
  const result = await api("events"); $("events").replaceChildren();
  if (!result.events.length) $("events").append(element("p", "empty", "���޼�¼"));
  for (const item of result.events.slice().reverse()) { const row = element("div", item.level === "error" ? "event error" : "event"); row.append(element("time", "", new Date(item.time).toLocaleTimeString("zh-CN")), element("span", "", item.message)); $("events").append(row); }
}
async function refreshStatus() {
  try {
    const status = await api("status"); $("connection-dot").className = "status-dot connected"; $("connection-text").textContent = "�ں�������"; $("core-version").textContent = `Mihomo �� ${status.version}`;
    $("core-state").textContent = status.recovery_error ? "���ûָ���" : status.configured ? "�ں�������" : "�����ô���Դ";
    $("recovery-note").hidden = !status.recovery_error;
    $("recovery-note").textContent = status.recovery_error ? `�ѽ������ûָ�ģʽ��${errors[status.recovery_error] || "ԭ����У��ʧ�ܡ�"} ԭ�����ѱ��������������ݲ����ã�δ����͸������������������������󱣴棬�ɹ��󱣳���ʽģʽ����Ҫʱ����������������͹�����` : "";
    $("refresh-subscription").disabled = !status.has_subscription;
    $("traffic-state").textContent = scopes[status.traffic_scope];
    $("traffic-detail").textContent = status.traffic_scope === "haos" ? (status.traffic.active ? "TUN ���� TCP / UDP" : "͸��������δ����") : status.traffic_scope === "selected" ? `${(status.traffic.ips || []).length} �������е�Ӧ�ý���` : "�豸��ͻ����������ô���";
    $("scope-note").textContent = status.traffic_scope === "selected" ? "�������������׼��ɽ��롣��ѡ���ɲ�����ʾ����������ͻ��˾��Ѹ��ǡ�" : status.traffic_scope === "haos" ? "��̨ HAOS �ĳ�վ���ӽ�����������������˽�е�ֱַ����" : "��ǰ���ṩ�������ڣ�δ͸������ HAOS ��վ������";
    if (status.recovery_error) $("scope-note").textContent = "��ǰ�������ûָ�ģʽ���������ھܾ�����δ����͸������������������";
    $("integration-state").textContent = status.integration_connected ? "���׼��������ӣ�ÿ 10 ��ͬ����ҳ���á�" : "���׼�����δ���ӡ����Ӻ���ڱ���ҳѡ�񼯳�ʵ����";
    drawOutlets(status);
  } catch (error) { $("connection-dot").className = "status-dot"; $("connection-text").textContent = "�����ж�"; $("core-state").textContent = "�����ж�"; throw error; }
  await refreshEvents();
}
function modeNote() {
  const mode = $("traffic-mode").value; $("target-panel").hidden = mode !== "selected";
  $("mode-note").textContent = mode === "haos" ? "���� HA Core ��Ӧ�ó�վ��������Ҫ NET_ADMIN �� TUN�������ù������������ֱ����˽�е�ַ�ƹ�������" : mode === "selected" ? "ѡ��Ҫ�����Ӧ�û򼯳ɣ����ߵĿɸ��Ƿ�Χ��ͬ������󼯳��������Լ 10 ����Ч��" : "�ṩ�����������ڣ��ͻ���������ָ��������";
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
    const reason = item.missing ? "���Ƴ�����ȡ��ѡ��" : kind === "app" && !item.supported ? item.reason === "host_network" ? "�����������磬�޷���������" : "���޶��������ַ" : kind === "integration" && item.disabled ? "�ѽ���" : kind === "integration" && !targets.integration_connected ? "��������δ����" : kind === "app" ? item.state === "started" ? "������" : "��δ����" : item.domain;
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
  notice(copied ? "�ڲ���ַ�Ѹ��ơ�" : "�޷��Զ����ƣ���ѡ�е�ַ�ֶ����ơ�", !copied);
}
async function loadTargets() {
  targets = await api("targets");
  drawTargets("app", targets.apps, settings.selected_apps || [], "�޷���ȡӦ���б�����û���Ѱ�װӦ�á�");
  drawTargets("integration", targets.integrations, settings.selected_integrations || [], "������������ HA ���ɡ�");
  $("connection-addresses").replaceChildren();
  const labels = {controller_url: "���� API ��ַ", manager_url: "Ӧ�ù�����ַ", proxy_url: "HTTP ��������"};
  for (const [key, value] of Object.entries(targets.addresses)) { const row = element("div", "copy-row"), label = element("label", "", labels[key]), input = element("input"), button = element("button", "secondary", "����"); input.id = `copy-${key}`; input.value = value; input.readOnly = true; label.htmlFor = input.id; button.type = "button"; button.setAttribute("aria-label", `����${labels[key]}`); button.addEventListener("click", () => copyText(value, button)); row.append(label, input, button); $("connection-addresses").append(row); }
  $("share-addresses").textContent = targets.lan_addresses.length ? `������ַ��${targets.lan_addresses.map((ip) => `${ip}:7890`).join("��")}` : "������ַ��HA �ľ����� IP���˿� 7890��";
}
async function loadSettings() {
  settings = await api("settings");
  for (const [id, key, fallback] of [["subscription-url", "subscription_url", ""], ["extension-yaml", "extension_yaml", ""], ["outlet-source", "outlet_source", "subscription"], ["lan-host", "lan_host", ""], ["lan-port", "lan_port", 7890], ["lan-type", "lan_type", "http"], ["lan-user", "lan_username", ""], ["proxy-user", "proxy_username", ""], ["traffic-mode", "traffic_mode", "explicit"], ["core-mode", "core_mode", "rule"]]) $(id).value = settings[key] ?? fallback;
  for (const kind of ["lan", "proxy"]) { $(`${kind}-password`).value = ""; $(`${kind}-password`).placeholder = settings[`has_${kind}_password`] ? "�ѱ��棬���ձ���" : "��ѡ"; $(`clear-${kind}-auth`).checked = false; }
  $("password-note").textContent = settings.has_lan_password ? "�ѱ������룬������ַ���û���ʱ��������д��" : "����Ҫ��֤ʱ���û�������������ա�";
  $("subscription-interval").value = (settings.subscription_interval ?? 3600) / 60;
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
function form(id, build, message) { $(id).addEventListener("submit", (event) => { event.preventDefault(); action(event.submitter, "У�鲢�����С�", async () => { await api("settings", "PUT", build()); await loadSettings(); await refreshStatus(); notice(message); }); }); }
form("sources-form", () => { const data = {subscription_url: $("subscription-url").value.trim(), subscription_interval: Number($("subscription-interval").value) * 60, extension_yaml: $("extension-yaml").value, outlet_source: $("outlet-source").value, lan_host: $("lan-host").value.trim(), lan_port: Number($("lan-port").value), lan_type: $("lan-type").value}; if (!data.lan_host && data.subscription_url) data.outlet_source = "subscription"; credentials(data, "lan"); return data; }, "������������ȡ��У�鲢���档");
form("traffic-form", () => ({traffic_mode: $("traffic-mode").value, core_mode: $("core-mode").value, selected_apps: [...document.querySelectorAll('input[data-kind="app"]:checked')].map((input) => input.value), selected_integrations: [...document.querySelectorAll('input[data-kind="integration"]:checked')].map((input) => input.value)}), "����ģʽ�ѱ��棻���׼������Լ 10 ��ͬ����");
form("sharing-form", () => { const data = {share_enabled: $("share-enabled").checked}; credentials(data, "proxy"); return data; }, "���������ѱ��档");
form("rules-form", () => { const data = {default_action: $("default-action").value}; for (const key of ["direct_domains", "proxy_domains", "direct_cidrs", "proxy_cidrs"]) data[key] = [...new Set($(key.replaceAll("_", "-")).value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean))]; return data; }, "���׹����ѱ��档");
$("traffic-mode").addEventListener("change", modeNote);
$("discover-form").addEventListener("submit", (event) => { event.preventDefault(); action(event.submitter, "���ڷ��֡�", async () => { $("discovery-results").replaceChildren(element("p", "empty", "������֤����Э�顭")); const result = await api("discover", "POST", {cidr: $("cidr").value.trim()}); $("discovery-results").replaceChildren(); if (!result.proxies.length) $("discovery-results").append(element("p", "empty", "δ���ִ��������ֶ���д��ַ��")); for (const address of result.proxies) { const row = element("div", "discovery-row"), use = element("button", "secondary", "�����������"); use.addEventListener("click", () => { const url = new URL(address); $("lan-host").value = url.hostname; $("lan-port").value = url.port; $("lan-type").value = url.protocol.replace(":", ""); $("lan-user").value = ""; $("lan-password").value = ""; $("outlet-source").value = "lan"; location.hash = "sources"; notice("�����뷢�ֵĴ�������д��֤�󱣴档 "); }); row.append(element("span", "", address), use); $("discovery-results").append(row); } await refreshEvents(); }); });
$("refresh-status").addEventListener("click", (event) => action(event.currentTarget, "ˢ����", async () => { await loadSettings(); await refreshStatus(); }));
$("refresh-subscription").addEventListener("click", (event) => action(event.currentTarget, "������", async () => { await api("refresh", "POST", {}); await refreshStatus(); notice("������������ȡ�����ء�"); }));
$("test-outlet").addEventListener("click", (event) => action(event.currentTarget, "������", async () => { $("test-result").textContent = "�����С�"; try { const result = await api("test", "POST", {}); $("test-result").textContent = `${result.latency_ms} ms`; notice(`�����յ� HTTP ${result.status} ��Ӧ���˲�����֤�������ڣ��������������к��顣`); await refreshEvents(); } catch (error) { $("test-result").textContent = "δ��ͨ"; throw error; } }));
$("diagnose-outlet").addEventListener("click", (event) => action(event.currentTarget, "�����", async () => { $("diagnostic-results").replaceChildren(element("p", "empty", "���ڼ����֤�� HTTPS��")); const result = await api("diagnose", "POST", {}); $("diagnostic-results").replaceChildren(); for (const check of result.checks) { const stage = check.stage === "upstream_connect" ? "���δ�������" : "HTTPS ����"; $("diagnostic-results").append(element("p", "field-note", `${stage} �� ${check.target}��${check.ok ? `�ɹ� �� HTTP ${check.http_status}` : errors[check.code] || "ʧ��"}`)); } await refreshEvents(); }));
function navigation() { for (const link of document.querySelectorAll("nav a")) link.classList.toggle("active", link.hash === (location.hash || "#overview")); }
window.addEventListener("hashchange", navigation); navigation();
async function init() { const buttons = [...document.querySelectorAll("form button")]; buttons.forEach((b) => b.disabled = true); try { await loadSettings(); ready = true; await refreshStatus(); } catch (error) { notice(error.message, true); } finally { buttons.forEach((b) => b.disabled = !ready); } }
init(); setInterval(() => { if (ready && !document.hidden) refreshStatus().catch(() => {}); }, 15000);
