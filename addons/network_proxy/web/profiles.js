"use strict";
let catalog = {profiles: []}, editingProfile = "", editorProfile = "";
Object.assign(errors, {
  proxy_disabled: "代理已关闭，管理服务继续运行。",
  invalid_profiles: "订阅名称、地址、间隔或数量无效（最多 32 份）。",
  profile_not_found: "订阅已移除，请刷新列表。",
  profile_not_ready: "订阅尚未就绪，请先拉取。",
  group_not_selectable: "此策略组不支持手动选择。",
  invalid_group_member: "节点已移除，请刷新策略组。",
  tls_connection_closed: "TLS 连接被提前关闭，请检查当前节点和规则。",
});
function profileForm(profile = {}) {
  editingProfile = profile.id || "";
  $("profile-name").value = profile.name || "";
  $("subscription-url").value = profile.url || "";
  $("subscription-interval").value = (profile.interval ?? 3600) / 60;
  $("extension-yaml").value = profile.extension_yaml || "";
  $("profile-form-title").textContent = profile.id ? `修改订阅 · ${profile.name}` : "新增订阅";
}
async function loadProfiles() {
  catalog = await api("subscriptions");
  $("profile-list").replaceChildren();
  for (const profile of catalog.profiles) {
    const row = element("article", "profile-row"), main = element("div", "node-main");
    main.append(element("strong", "", profile.name), element("small", "", `${profile.active ? "当前订阅 · " : ""}${profile.config_mode === "edited" ? "本地编辑副本" : "原生配置"} · ${profile.fetched_at ? new Date(profile.fetched_at * 1000).toLocaleString("zh-CN") : "尚未拉取"}`));
    const buttons = element("div", "profile-actions");
    for (const [label, operation] of [["使用", "activate"], ["拉取", "refresh"], ["修改", "form"], ["查看 / 编辑", "editor"], ["移除", "delete"]]) {
      const button = element("button", "secondary", label); button.type = "button";
      button.setAttribute("aria-label", `${label} ${profile.name}`);
      button.addEventListener("click", () => {
        if (operation === "form") { profileForm(profile); $("profile-name").focus(); return; }
        if (operation === "editor") { action(button, "读取中", () => openEditor(profile.id)); return; }
        if (operation === "delete" && !confirm(`移除订阅“${profile.name}”及本地编辑副本？可用原订阅地址重新添加。`)) return;
        action(button, "处理中", async () => { await api(`subscription/${operation}`, "POST", {id: profile.id}); await loadProfiles(); await loadSettings(); await refreshStatus(); await loadPolicies(); notice("订阅设置已保存。"); });
      }); buttons.append(button);
    }
    row.append(main, buttons); $("profile-list").append(row);
  }
  if (!catalog.profiles.length) $("profile-list").append(element("p", "empty", "添加订阅后立即拉取并校验；默认保留原生策略组、规则和模式。"));
}
async function openEditor(identity) {
  editorProfile = identity;
  const profile = catalog.profiles.find(item => item.id === identity);
  $("editor-title").textContent = `订阅配置 · ${profile.name}`;
  $("editor-view").value = "editable";
  $("config-editor").hidden = false;
  await loadEditor(); location.hash = "config-editor";
}
async function loadEditor() {
  const view = $("editor-view").value;
  const result = await api(`subscription/config?id=${encodeURIComponent(editorProfile)}&view=${view}`);
  $("config-yaml").value = result.yaml;
  $("config-yaml").readOnly = view !== "editable";
  $("save-config").disabled = view !== "editable";
  $("editor-mode").textContent = result.config_mode === "edited" ? "正在使用本地编辑副本；拉取订阅不会覆盖它。" : "正在使用订阅原生配置。保存编辑后切换为本地副本。";
}
async function loadPolicies() {
  const data = await api("policies"); $("policy-groups").replaceChildren();
  for (const group of data.groups) {
    const row = element("div", "policy-row"), label = element("label", "", group.name), select = element("select");
    select.setAttribute("aria-label", `策略组 ${group.name}`);
    for (const member of group.members) { const option = element("option", "", member); option.value = member; option.selected = group.current === member; select.append(option); }
    select.disabled = !group.selectable;
    const meta = element("small", "", `${group.type} · 当前：${group.current || "自动"}`);
    const apply = element("button", "secondary", "选择"); apply.type = "button"; apply.disabled = !group.selectable;
    apply.addEventListener("click", () => action(apply, "切换中", async () => { await api("policy/select", "POST", {group: group.name, name: select.value}); await loadPolicies(); notice("策略组选择已保存。"); }));
    row.append(label, select, apply, meta);
    if (group.automatic && group.selectable) { const auto = element("button", "text-button", "恢复自动"); auto.type = "button"; auto.addEventListener("click", () => action(auto, "处理中", async () => { await api("policy/select", "POST", {group: group.name, automatic: true}); await loadPolicies(); notice("策略组已恢复自动选择。"); })); row.append(auto); }
    $("policy-groups").append(row);
  }
  $("node-list").replaceChildren(); $("node-count").textContent = `${data.nodes.length} 个节点`;
  for (const node of data.nodes) { const row = element("div", "node-row"); row.append(element("span", "", node.name), element("small", "", `${node.type}${node.delay ? ` · ${node.delay} ms` : ""}${node.alive === false ? " · 暂未连通" : ""}`)); $("node-list").append(row); }
  if (!data.groups.length) $("policy-groups").append(element("p", "empty", "当前没有策略组。选择订阅来源后显示其原生策略组。"));
}
$("new-profile").addEventListener("click", () => profileForm());
$("subscription-form").addEventListener("submit", event => { event.preventDefault(); action(event.submitter, "拉取并校验中", async () => { const result = await api("subscription/save", "POST", {...(editingProfile ? {id: editingProfile} : {}), name: $("profile-name").value.trim(), url: $("subscription-url").value.trim(), interval: Number($("subscription-interval").value) * 60, extension_yaml: $("extension-yaml").value}); await loadProfiles(); profileForm(catalog.profiles.find(item => item.id === result.id)); await loadSettings(); await refreshStatus(); await loadPolicies(); notice("订阅已拉取、校验并保存。"); }); });
$("editor-view").addEventListener("change", () => loadEditor().catch(error => notice(error.message, true)));
$("config-form").addEventListener("submit", event => { event.preventDefault(); action(event.submitter, "校验中", async () => { await api("subscription/edit", "POST", {id: editorProfile, yaml: $("config-yaml").value}); await loadProfiles(); await loadEditor(); await refreshStatus(); await loadPolicies(); notice("本地编辑副本已校验并保存。"); }); });
$("restore-native").addEventListener("click", event => action(event.currentTarget, "校验中", async () => { await api("subscription/edit", "POST", {id: editorProfile, native: true}); await loadProfiles(); await loadEditor(); await refreshStatus(); await loadPolicies(); notice("已恢复使用原生配置，编辑副本仍保留。"); }));
$("refresh-policies").addEventListener("click", event => action(event.currentTarget, "刷新中", loadPolicies));
$("proxy-toggle").addEventListener("click", event => action(event.currentTarget, "应用中", async () => { const enabled = settings.proxy_enabled === false; await api("settings", "PUT", {proxy_enabled: enabled}); await loadSettings(); await refreshStatus(); notice(enabled ? "代理已启用。" : "代理已关闭；出口监听和透明接入已停止，管理服务继续运行。"); }));
