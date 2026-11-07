const base = "/admin/v1";
const notice = (text) => { document.querySelector("#status").textContent = text; };
async function read(path, options = {}) {
  const response = await fetch(base + path, { cache: "no-store", ...options });
  const data = await response.json();
  if (!response.ok) {
    const failure = new Error(data.error?.message ?? `HTTP ${response.status}`);
    failure.status = response.status; failure.code = data.error?.code;
    throw failure;
  }
  return data;
}
function headers(key) { return { "Content-Type": "application/json", "Idempotency-Key": key, "X-Requested-With": "personal-assistant-local-admin" }; }
// Keep command identity outside replaceable DOM nodes so read-only refresh cannot lose it.
const commands = new Map();
function renderCommands() {
  const pending = document.querySelector("#pending-commands");
  pending.replaceChildren();
  for (const entry of commands.values()) {
    entry.buttons = entry.buttons.filter((button) => button.isConnected);
    for (const button of entry.buttons) {
      button.disabled = entry.busy;
      button.textContent = entry.attempt ? "结果未确认，重试同一请求" : entry.label;
    }
    if (entry.attempt) {
      const button = document.createElement("button"); button.type = "button";
      button.textContent = `${entry.path} · 重试同一请求`;
      button.disabled = entry.busy;
      button.addEventListener("click", () => runCommand(entry));
      pending.append(button);
    }
  }
  pending.hidden = pending.children.length === 0;
}
async function runCommand(entry) {
  if (entry.busy) return;
  entry.attempt ??= { key: crypto.randomUUID(), body: entry.body, receipt: entry.receipt };
  const attempt = entry.attempt;
  entry.busy = true; renderCommands();
  try {
    const value = await read(entry.path, { method: "POST", headers: headers(attempt.key), body: JSON.stringify(attempt.body) });
    if (!attempt.receipt(value)) throw new Error("回执结构不完整");
    entry.attempt = null;
    notice("请求回执已确认；操作是否完成以服务端状态为准。");
    await refresh();
  } catch (failure) { notice(`结果未确认：${failure.message}`); }
  finally { entry.busy = false; renderCommands(); }
}
function commandButton(button, path, body, receipt) {
  let entry = commands.get(path);
  if (!entry) {
    entry = { path, body, receipt, label: button.textContent, buttons: [], attempt: null, busy: false };
    commands.set(path, entry);
  }
  entry.body = body; entry.receipt = receipt; entry.label = button.textContent;
  entry.buttons.push(button);
  button.disabled = entry.busy;
  if (entry.attempt) button.textContent = "结果未确认，重试同一请求";
  button.addEventListener("click", () => runCommand(entry));
}
async function refresh() {
  try {
    document.querySelector("#host-status").textContent = JSON.stringify(await read("/host"), null, 2);
    const response = await read("/extensions");
    const list = document.querySelector("#extensions"); list.replaceChildren();
    for (const item of response.items) {
      const section = document.createElement("section");
      const title = document.createElement("h3"); title.textContent = `${item.id} · ${item.version} · ${item.state}`;
      section.append(title);
      for (const operation of item.state === "ENABLED" ? ["disable"] : item.state === "DISABLED" ? ["enable", "rollback"] : []) {
        const button = document.createElement("button"); button.type = "button"; button.className = "secondary"; button.textContent = operation;
        commandButton(button, `/extensions/${encodeURIComponent(item.id)}/${operation}`, {}, (value) => typeof value.id === "string" && value.extension_id === item.id && value.operation === operation);
        section.append(button);
      }
      list.append(section);
    }
    const schedules = await read("/schedules");
    const scheduled = document.querySelector("#schedules"); scheduled.replaceChildren();
    for (const item of schedules.items) {
      const description = document.createElement("pre"); description.textContent = JSON.stringify(item, null, 2);
      const button = document.createElement("button"); button.type = "button"; button.textContent = item.value.enabled ? "停用此调度" : "启用此调度";
      commandButton(button, `/schedules/${encodeURIComponent(item.id)}`, { version: item.version, enabled: !item.value.enabled }, (value) => value.id === item.id && value.version === item.version + 1);
      scheduled.append(description, button);
    }
  } catch (failure) { notice(`读取失败：${failure.message}`); }
  finally { renderCommands(); }
}
for (const name of ["freeze", "resume"]) commandButton(document.querySelector("#" + name), "/host/" + name, {}, (value) => value.frozen === (name === "freeze"));
document.querySelector("#refresh").addEventListener("click", refresh);
let plan = null, installAttempt = null, inspectBusy = false, installBusy = false;
// These codes are checked before begin_install/begin_upgrade starts work, after durable replay.
const installPreflightRejections = new Set(["CONFIRMATION_REQUIRED", "PLAN_NOT_FOUND", "PLAN_MODE_MISMATCH"]);
function renderInstall() {
  const button = document.querySelector("#install");
  button.disabled = inspectBusy || installBusy || (!installAttempt && (!plan || !document.querySelector("#trust").checked));
  button.textContent = installAttempt ? "结果未确认，重试同一安装／升级请求" : plan?.mode === "upgrade" ? "确认这份精确材料并升级" : "确认这份精确材料并安装";
  document.querySelector("#trust").disabled = inspectBusy || Boolean(installAttempt);
  document.querySelector("#source").disabled = inspectBusy || Boolean(installAttempt);
  document.querySelector("#inspect button").disabled = inspectBusy || Boolean(installAttempt);
}
function clearPlan() {
  plan = null; document.querySelector("#trust").checked = false;
  document.querySelector("#preview").textContent = "";
  renderInstall();
}
document.querySelector("#source").addEventListener("input", () => { if (!installAttempt) clearPlan(); });
document.querySelector("#inspect").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (inspectBusy || installBusy || installAttempt) { notice("请先确认原请求的服务端状态。"); return; }
  clearPlan(); inspectBusy = true; renderInstall();
  const source = document.querySelector("#source").value;
  try {
    const candidate = await read("/extensions/inspect", { method: "POST", headers: headers(crypto.randomUUID()), body: JSON.stringify({ source }) });
    if (!candidate.plan_id || !candidate.confirmation_nonce || !candidate.preview_hash || !candidate.artifact_hash || !candidate.extension_id || !["install", "upgrade"].includes(candidate.mode)) throw new Error("材料预览不完整");
    plan = candidate;
    document.querySelector("#preview").textContent = JSON.stringify(plan, null, 2);
  } catch (failure) { notice(failure.message); }
  finally { inspectBusy = false; renderInstall(); }
});
document.querySelector("#trust").addEventListener("change", renderInstall);
document.querySelector("#install").addEventListener("click", async () => {
  if (inspectBusy || installBusy || (!installAttempt && (!plan || !document.querySelector("#trust").checked))) return;
  installAttempt ??= {
    key: crypto.randomUUID(), extensionId: plan.extension_id, mode: plan.mode,
    path: plan.mode === "upgrade" ? `/extensions/${encodeURIComponent(plan.extension_id)}/upgrade` : "/extensions/install",
    body: { plan_id: plan.plan_id, confirmation_nonce: plan.confirmation_nonce, preview_hash: plan.preview_hash, accepted_warning: true },
  };
  const attempt = installAttempt;
  installBusy = true; renderInstall();
  try {
    const result = await read(attempt.path, { method: "POST", headers: headers(attempt.key), body: JSON.stringify(attempt.body) });
    if (typeof result.id !== "string" || result.extension_id !== attempt.extensionId || result.operation !== attempt.mode) throw new Error("安装／升级回执不完整");
    notice(`安装／升级请求已确认，操作 ${result.id} 的状态为 ${result.status}；完成状态请查看扩展列表。`);
    installAttempt = null; clearPlan();
    await refresh();
  } catch (failure) {
    if (failure.status >= 400 && failure.status < 500 && installPreflightRejections.has(failure.code)) {
      installAttempt = null; clearPlan();
      notice(`本次被拒绝（${failure.code}），先核对扩展状态再重新核对材料；不会自动重发。`);
    } else { notice(`安装／升级结果未确认：${failure.message}`); }
  } finally { installBusy = false; renderInstall(); }
});

// Config PUT has no server-side CAS or command receipt: reconcile a lost response by GET only.
let loadedConfigId = null, configAttempt = null, configBusy = false;
const configNotice = (text) => { document.querySelector("#config-status").textContent = text; };
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  return value;
}
const sameConfig = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
function renderConfig() {
  document.querySelector("#save-config").disabled = configBusy || Boolean(configAttempt) || !loadedConfigId || loadedConfigId !== document.querySelector("#extension-id").value.trim();
  document.querySelector("#config-form button").disabled = configBusy;
  document.querySelector("#reconcile-config").hidden = !configAttempt;
  document.querySelector("#reconcile-config").disabled = configBusy;
  const different = configAttempt?.state === "different";
  document.querySelector("#config-conflict").hidden = !different;
  document.querySelector("#server-config").textContent = different ? JSON.stringify({ extension_id: configAttempt.extensionId, config: configAttempt.current.config }, null, 2) : "";
  for (const id of ["use-server-config", "keep-local-config"]) document.querySelector("#" + id).disabled = configBusy;
}
function validateConfig(data, extensionId) {
  if (data.extension_id !== extensionId || !data.config || typeof data.config !== "object" || Array.isArray(data.config)) throw new Error("配置回执与原扩展不匹配");
}
async function reconcileConfig() {
  if (!configAttempt || configBusy) return;
  const attempt = configAttempt; configBusy = true; renderConfig();
  try {
    const data = await read(`/extensions/${encodeURIComponent(attempt.extensionId)}/config`);
    validateConfig(data, attempt.extensionId);
    if (sameConfig(data.config, attempt.config)) {
      configAttempt = null;
      loadedConfigId = document.querySelector("#extension-id").value.trim() === attempt.extensionId ? attempt.extensionId : null;
      configNotice("当前配置与原请求相符；这不能证明原请求执行结果。本机后改输入保留，尚未保存。再次保存前请核对。");
    } else {
      attempt.state = "different"; attempt.current = data;
      configNotice("当前服务端配置与原请求不同，无法据此判断原请求是否执行。请选择处理方式；不会自动保存。");
    }
  } catch (failure) { configNotice(`原配置结果仍未确认，请继续只读核对：${failure.message}`); }
  finally { configBusy = false; renderConfig(); }
}
document.querySelector("#extension-id").addEventListener("input", () => {
  if (loadedConfigId !== document.querySelector("#extension-id").value.trim()) loadedConfigId = null;
  renderConfig();
});
document.querySelector("#config-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (configBusy) return;
  if (configAttempt) { await reconcileConfig(); return; }
  const extensionId = document.querySelector("#extension-id").value.trim();
  if (!extensionId) return;
  const text = document.querySelector("#config-json").value;
  loadedConfigId = null; configBusy = true; renderConfig();
  try {
    const data = await read(`/extensions/${encodeURIComponent(extensionId)}/config`);
    validateConfig(data, extensionId);
    if (document.querySelector("#extension-id").value.trim() !== extensionId) { configNotice("扩展选择已改变，请重新读取当前扩展。"); return; }
    loadedConfigId = extensionId;
    document.querySelector("#config-schema").textContent = JSON.stringify(data.config_schema, null, 2);
    if (document.querySelector("#config-json").value === text) document.querySelector("#config-json").value = JSON.stringify(data.config, null, 2);
    configNotice("已读取当前配置；保存会覆盖当前值，本接口没有并发版本保护。读取期间新增的本机编辑已保留。");
  } catch (failure) { configNotice(`读取失败：${failure.message}`); }
  finally { configBusy = false; renderConfig(); }
});
document.querySelector("#save-config").addEventListener("click", async () => {
  if (configBusy || configAttempt) { configNotice("原配置结果未确认，请先只读核对原扩展。"); return; }
  const extensionId = document.querySelector("#extension-id").value.trim();
  if (!loadedConfigId || loadedConfigId !== extensionId) { configNotice("请先读取此扩展的当前配置。"); return; }
  let config;
  try { config = JSON.parse(document.querySelector("#config-json").value); }
  catch (failure) { configNotice(`配置 JSON 无效，尚未发送：${failure.message}`); return; }
  configAttempt = { extensionId, config, key: crypto.randomUUID(), state: "pending" };
  const attempt = configAttempt; configBusy = true; renderConfig();
  try {
    const value = await read(`/extensions/${encodeURIComponent(attempt.extensionId)}/config`, { method: "PUT", headers: headers(attempt.key), body: JSON.stringify({ config: attempt.config }) });
    validateConfig(value, attempt.extensionId);
    if (!sameConfig(value.config, attempt.config)) throw new Error("保存回执未确认");
    configAttempt = null;
    configNotice("原配置请求已确认、待生效；请在本机 TUI 扩展页选择“应用配置”。本机后改输入保留，需另行保存。");
  } catch (failure) {
    attempt.state = "unknown";
    configNotice(`配置保存未确认，本机输入保留；不要重复保存，请只读核对原扩展：${failure.message}`);
  } finally { configBusy = false; renderConfig(); }
});
document.querySelector("#reconcile-config").addEventListener("click", reconcileConfig);
function resolveConfig(useServer) {
  if (configBusy || configAttempt?.state !== "different") return;
  const attempt = configAttempt;
  if (useServer) {
    document.querySelector("#extension-id").value = attempt.extensionId;
    document.querySelector("#config-json").value = JSON.stringify(attempt.current.config, null, 2);
    document.querySelector("#config-schema").textContent = JSON.stringify(attempt.current.config_schema, null, 2);
  }
  loadedConfigId = document.querySelector("#extension-id").value.trim() === attempt.extensionId ? attempt.extensionId : null;
  configAttempt = null; renderConfig();
  configNotice(useServer ? "已采用刚读取的服务端配置；没有发送保存请求。" : "本机输入已保留；尚未保存。请按服务端当前状态重新核对，再明确保存；本接口没有并发版本保护。");
}
document.querySelector("#use-server-config").addEventListener("click", () => resolveConfig(true));
document.querySelector("#keep-local-config").addEventListener("click", () => resolveConfig(false));
renderConfig();
document.querySelector("#declarations").addEventListener("click", async () => {
  try { document.querySelector("#declared").textContent = JSON.stringify(await read("/declarations"), null, 2); }
  catch (failure) { notice(failure.message); }
});
let scheduleAttempt = null;
document.querySelector("#schedule-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button"); button.disabled = true;
  scheduleAttempt ??= { key: crypto.randomUUID(), body: Object.fromEntries(new FormData(event.currentTarget)) };
  try {
    const result = await read("/schedules", { method: "POST", headers: headers(scheduleAttempt.key), body: JSON.stringify(scheduleAttempt.body) });
    if (result.id !== scheduleAttempt.body.schedule_id || result.value?.definition?.source_id !== scheduleAttempt.body.source_id) throw new Error("绑定回执不完整");
    scheduleAttempt = null; await refresh(); notice("只读调度已绑定。");
  } catch (failure) { notice(failure.message); button.textContent = "结果未确认，重试同一绑定请求"; }
  finally { button.disabled = false; }
});
window.addEventListener("beforeunload", (event) => {
  if (installAttempt || configAttempt || scheduleAttempt || [...commands.values()].some((entry) => entry.attempt)) {
    event.preventDefault(); event.returnValue = "";
  }
});
void refresh();
