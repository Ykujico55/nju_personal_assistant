const base = "/admin/v1";
const notice = (text) => { document.querySelector("#status").textContent = text; };
async function read(path, options = {}) {
  const response = await fetch(base + path, { cache: "no-store", ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message ?? `HTTP ${response.status}`);
  return data;
}
function headers(key) { return { "Content-Type": "application/json", "Idempotency-Key": key, "X-Requested-With": "personal-assistant-local-admin" }; }
function commandButton(button, path, body, receipt) {
  let attempt = null;
  button.addEventListener("click", async () => {
    attempt ??= { key: crypto.randomUUID(), body };
    button.disabled = true;
    try {
      const value = await read(path, { method: "POST", headers: headers(attempt.key), body: JSON.stringify(attempt.body) });
      if (!receipt(value)) throw new Error("回执结构不完整");
      notice("请求回执已确认；操作是否完成以服务端状态为准。");
      await refresh();
    } catch (failure) { button.disabled = false; button.textContent = "结果未确认，重试同一请求"; notice(failure.message); }
  });
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
}
for (const name of ["freeze", "resume"]) commandButton(document.querySelector("#" + name), "/host/" + name, {}, (value) => value.frozen === (name === "freeze"));
document.querySelector("#refresh").addEventListener("click", refresh);
let plan = null, installAttempt = null;
document.querySelector("#inspect").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (installAttempt) { notice("请先确认原安装请求的服务端状态。"); return; }
  try {
    plan = await read("/extensions/inspect", { method: "POST", headers: headers(crypto.randomUUID()), body: JSON.stringify({ source: document.querySelector("#source").value }) });
    if (!plan.plan_id || !plan.confirmation_nonce || !plan.preview_hash || !plan.artifact_hash) throw new Error("材料预览不完整");
    document.querySelector("#preview").textContent = JSON.stringify(plan, null, 2);
    document.querySelector("#install").disabled = !document.querySelector("#trust").checked;
  } catch (failure) { notice(failure.message); }
});
document.querySelector("#trust").addEventListener("change", (event) => { document.querySelector("#install").disabled = !plan || !event.target.checked; });
document.querySelector("#install").addEventListener("click", async (event) => {
  if (!plan || !document.querySelector("#trust").checked) return;
  installAttempt ??= { key: crypto.randomUUID(), body: { plan_id: plan.plan_id, confirmation_nonce: plan.confirmation_nonce, preview_hash: plan.preview_hash, accepted_warning: true } };
  event.target.disabled = true;
  try {
    const result = await read("/extensions/install", { method: "POST", headers: headers(installAttempt.key), body: JSON.stringify(installAttempt.body) });
    if (typeof result.id !== "string" || result.extension_id !== plan.extension_id || result.operation !== "install") throw new Error("安装回执不完整");
    notice(`安装请求已确认，操作 ${result.id} 的状态为 ${result.status}；尚未启用扩展。`);
    installAttempt = null; plan = null; document.querySelector("#trust").checked = false;
    await refresh();
  } catch (_) { event.target.disabled = false; event.target.textContent = "安装结果未确认，重试同一请求"; }
});
document.querySelector("#config-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await read(`/extensions/${encodeURIComponent(document.querySelector("#extension-id").value)}/config`);
    document.querySelector("#config-schema").textContent = JSON.stringify(data.config_schema, null, 2);
    document.querySelector("#config-json").value = JSON.stringify(data.config, null, 2);
  } catch (failure) { notice(failure.message); }
});
document.querySelector("#save-config").addEventListener("click", async () => {
  try {
    const config = JSON.parse(document.querySelector("#config-json").value);
    const value = await read(`/extensions/${encodeURIComponent(document.querySelector("#extension-id").value)}/config`, { method: "PUT", headers: headers(crypto.randomUUID()), body: JSON.stringify({ config }) });
    if (!value.extension_id || JSON.stringify(value.config) !== JSON.stringify(config)) throw new Error("保存回执未确认");
    notice("配置已保存，需要停用后重新启用该扩展。");
  } catch (failure) { notice(`配置保存未确认，本机输入保留：${failure.message}`); }
});
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
void refresh();
