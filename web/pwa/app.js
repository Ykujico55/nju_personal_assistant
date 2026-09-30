const api = "/api/v1";
let currentApproval = null;
let selectedTaskId = null;
let nextBefore = null;
let listRequest = 0;
let detailRequest = 0;
let pendingCreate = null;
let renderedTaskDetail = null;
const messageEdits = new Map();
// Match TaskService's Python str.strip() before binding an idempotent request.
const TASK_MESSAGE_EDGE_SPACE =
  /^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu;

function canonicalTaskMessageContent(content) {
  return content.replace(TASK_MESSAGE_EDGE_SPACE, "");
}

function commandHeaders(idempotencyKey = crypto.randomUUID()) {
  return {
    "Content-Type": "application/json",
    "Idempotency-Key": idempotencyKey,
    "X-Requested-With": "personal-assistant-pwa",
  };
}

async function jsonRequest(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const payload = await response.json();
  if (!response.ok) {
    const failure = new Error(payload.error?.message || `HTTP ${response.status}`);
    failure.status = response.status;
    failure.code = payload.error?.code;
    throw failure;
  }
  return payload;
}

function setConnectionState(message, state) {
  const indicator = document.querySelector("#connection-state");
  indicator.textContent = message;
  indicator.className = `connection-state ${state}`;
}

function markSelectedTask() {
  for (const button of document.querySelectorAll("#task-list button[data-task-id]")) {
    button.setAttribute("aria-current", String(button.dataset.taskId === selectedTaskId));
  }
}

function taskButton(task) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.taskId = task.id;
  const objective = document.createElement("span");
  objective.textContent = task.objective.length > 140
    ? `${task.objective.slice(0, 140)}…` : task.objective;
  const meta = document.createElement("small");
  meta.textContent = `${task.state} · ${new Date(task.created_at).toLocaleString()}`;
  button.append(objective, meta);
  button.addEventListener("click", () => { void loadTask(task.id); });
  item.append(button);
  return item;
}

async function loadTasks({ append = false } = {}) {
  if (append && !nextBefore) return;
  const requestId = ++listRequest;
  const cursor = append ? nextBefore : null;
  const url = new URL(`${api}/tasks`, window.location.origin);
  url.searchParams.set("limit", "20");
  if (cursor) url.searchParams.set("before", cursor);
  const list = document.querySelector("#task-list");
  const error = document.querySelector("#task-list-error");
  try {
    const page = await jsonRequest(url.toString());
    if (requestId !== listRequest) return;
    if (!append) list.replaceChildren();
    for (const task of page.items) list.append(taskButton(task));
    if (!list.children.length) {
      const empty = document.createElement("li");
      empty.textContent = "暂无任务。";
      list.append(empty);
    }
    nextBefore = page.next_before;
    document.querySelector("#more-tasks").hidden = !nextBefore;
    error.hidden = true;
    error.textContent = "";
    markSelectedTask();
    if (!selectedTaskId && page.items.length) void loadTask(page.items[0].id);
  } catch (failure) {
    if (requestId !== listRequest) return;
    error.textContent = `无法读取最新任务：${failure.message}。已有列表可能已过期。`;
    error.hidden = false;
  }
}

async function loadTask(id) {
  const requestId = ++detailRequest;
  selectedTaskId = id;
  markSelectedTask();
  const panel = document.querySelector("#task-detail");
  if (panel.dataset.taskId !== id) {
    panel.textContent = "正在读取任务详情…";
    delete panel.dataset.taskId;
    renderedTaskDetail = null;
  }
  try {
    const detail = await jsonRequest(`${api}/tasks/${encodeURIComponent(id)}`);
    if (requestId !== detailRequest) return null;
    renderTaskDetail(detail);
    return detail;
  } catch (failure) {
    if (requestId !== detailRequest) return null;
    const error = document.createElement("p");
    error.className = "read-error task-detail-error";
    error.textContent = `无法读取任务详情：${failure.message}。请恢复连接后刷新。`;
    if (panel.dataset.taskId === id) {
      panel.querySelector(".task-detail-error")?.remove();
      panel.prepend(error);
    } else {
      panel.replaceChildren(error);
    }
    return null;
  }
}

function messageEdit(taskId) {
  if (!messageEdits.has(taskId)) {
    messageEdits.set(taskId, { content: "", attempt: null, notice: "", conflictUnresolved: false });
  }
  return messageEdits.get(taskId);
}

function updateMessageComposer(taskId) {
  const panel = document.querySelector("#task-detail");
  if (panel.dataset.taskId !== taskId || renderedTaskDetail?.task.id !== taskId) return;
  const edit = messageEdit(taskId);
  const form = panel.querySelector("#task-message-form");
  const input = form.querySelector("#task-message-input");
  const button = form.querySelector('button[type="submit"]');
  form.querySelector("#task-message-version").textContent =
    `服务器版本：${renderedTaskDetail.task.version}`;
  if (input.value !== edit.content) input.value = edit.content;
  input.disabled = edit.attempt?.state === "sending";
  input.readOnly = edit.attempt?.state === "unknown";
  button.disabled = edit.attempt?.state === "sending";
  button.textContent = edit.attempt?.state === "unknown"
    ? "重试同一请求" : "发送补充内容";
  form.querySelector("#task-message-status").textContent = edit.notice;
}

function createMessageComposer(taskId) {
  const form = document.createElement("form");
  form.id = "task-message-form";
  const label = document.createElement("label");
  label.htmlFor = "task-message-input";
  label.textContent = "补充说明或回答材料缺口";
  const input = document.createElement("textarea");
  input.id = "task-message-input";
  input.required = true;
  input.maxLength = 100000;
  input.rows = 4;
  input.autocomplete = "off";
  input.addEventListener("input", () => {
    const edit = messageEdit(taskId);
    edit.content = input.value;
    edit.notice = "";
    updateMessageComposer(taskId);
  });
  const version = document.createElement("p");
  version.id = "task-message-version";
  const button = document.createElement("button");
  button.type = "submit";
  const status = document.createElement("p");
  status.id = "task-message-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitTaskMessage(taskId);
  });
  form.append(label, input, version, button, status);
  return form;
}

function renderTaskDetail(detail) {
  if (selectedTaskId !== detail.task.id) return;
  const panel = document.querySelector("#task-detail");
  const sameTask = panel.dataset.taskId === detail.task.id;
  let content = sameTask ? panel.querySelector(".task-detail-content") : null;
  if (!content) content = document.createElement("div");
  content.className = "task-detail-content";
  const title = document.createElement("h3");
  title.textContent = detail.task.objective;
  const state = document.createElement("p");
  state.textContent = `状态：${detail.task.state} · 版本：${detail.task.version}`;
  const created = document.createElement("p");
  created.textContent = `创建：${new Date(detail.task.created_at).toLocaleString()}`;
  const messagesTitle = document.createElement("h4");
  messagesTitle.textContent = "消息";
  const messages = document.createElement("ol");
  for (const message of detail.messages) {
    const item = document.createElement("li");
    const source = document.createElement("strong");
    source.textContent = `${message.actor} · ${new Date(message.created_at).toLocaleString()}`;
    const body = document.createElement("p");
    body.textContent = message.content;
    item.append(source, body);
    messages.append(item);
  }
  if (!detail.messages.length) {
    const empty = document.createElement("li");
    empty.textContent = "暂无消息。";
    messages.append(empty);
  }
  content.replaceChildren(title, state, created, messagesTitle, messages);
  if (!sameTask) {
    panel.replaceChildren(content, createMessageComposer(detail.task.id));
    panel.dataset.taskId = detail.task.id;
  } else {
    panel.querySelector(".task-detail-error")?.remove();
  }
  renderedTaskDetail = detail;
  messageEdit(detail.task.id).conflictUnresolved = false;
  updateMessageComposer(detail.task.id);
}

function isMessageTaskDetail(detail, taskId, attempt) {
  const task = detail?.task;
  if (task?.id !== taskId || typeof task.objective !== "string" ||
      typeof task.state !== "string" || typeof task.created_at !== "string" ||
      !Number.isSafeInteger(task.version) || task.version <= attempt.version ||
      !Array.isArray(detail.messages)) return false;
  return detail.messages.every((message) =>
    message?.task_id === taskId && typeof message.id === "string" &&
    typeof message.actor === "string" && typeof message.content === "string" &&
    typeof message.created_at === "string",
  ) && detail.messages.some((message) => message.content === attempt.content);
}

async function submitTaskMessage(taskId) {
  if (selectedTaskId !== taskId || renderedTaskDetail?.task.id !== taskId) return;
  const edit = messageEdit(taskId);
  if (edit.attempt?.state === "sending") return;
  const content = canonicalTaskMessageContent(edit.content);
  if (!content) {
    edit.notice = "请输入补充内容，消息未发送。";
    updateMessageComposer(taskId);
    return;
  }
  if (edit.conflictUnresolved) {
    edit.notice = "尚未读取到服务器最新版本，请刷新任务详情后再发送。";
    updateMessageComposer(taskId);
    return;
  }
  if (navigator.onLine === false) {
    edit.notice = edit.attempt?.state === "unknown"
      ? "设备离线，上次发送结果未确认；恢复连接后可重试同一请求。"
      : "设备离线，消息未发送；草稿仅保留在当前页面。";
    updateMessageComposer(taskId);
    return;
  }
  if (!edit.attempt) {
    edit.attempt = {
      version: renderedTaskDetail.task.version,
      content,
      draft: edit.content,
      key: crypto.randomUUID(),
      state: "sending",
    };
  } else {
    edit.attempt.state = "sending";
  }
  const attempt = edit.attempt;
  edit.notice = "正在发送…";
  updateMessageComposer(taskId);
  try {
    const detail = await jsonRequest(`${api}/tasks/${encodeURIComponent(taskId)}/messages`, {
      method: "POST",
      headers: commandHeaders(attempt.key),
      body: JSON.stringify({ version: attempt.version, content: attempt.content }),
    });
    if (!isMessageTaskDetail(detail, taskId, attempt)) {
      throw new Error("服务端返回的任务详情不完整");
    }
    if (selectedTaskId === taskId) {
      ++detailRequest;
      renderTaskDetail(detail);
    }
    edit.content = "";
    edit.attempt = null;
    edit.notice = "消息已发送，服务端已确认。";
    updateMessageComposer(taskId);
    void loadTasks();
  } catch (failure) {
    if (failure.status === 409 && failure.code === "CONCURRENT_MODIFICATION") {
      edit.attempt = null;
      edit.conflictUnresolved = true;
      edit.notice = "版本冲突，消息未发送；正在读取服务器最新版本。";
      updateMessageComposer(taskId);
      const latest = selectedTaskId === taskId ? await loadTask(taskId) : null;
      edit.notice = latest
        ? `版本冲突，消息未发送。服务器版本已更新为 ${latest.task.version}；请检查消息后再次发送。`
        : "版本冲突，消息未发送。尚未读到最新版本，请刷新后再发送。";
      if (!latest) edit.conflictUnresolved = true;
      updateMessageComposer(taskId);
    } else if (failure.status >= 400 && failure.status < 500 && failure.status !== 408) {
      edit.attempt = null;
      edit.notice = `消息未发送：${failure.message}。`;
      updateMessageComposer(taskId);
    } else {
      edit.content = attempt.draft;
      edit.attempt = attempt;
      attempt.state = "unknown";
      edit.notice = "发送结果未确认；请恢复连接后重试同一请求，或刷新核对。";
      updateMessageComposer(taskId);
    }
  }
}

async function refreshTaskViews() {
  await Promise.all([
    loadTasks(),
    selectedTaskId ? loadTask(selectedTaskId) : Promise.resolve(),
  ]);
}

document.querySelector("#refresh-tasks").addEventListener("click", () => {
  void refreshTaskViews();
});
document.querySelector("#more-tasks").addEventListener("click", () => {
  void loadTasks({ append: true });
});

document.querySelector("#task-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const output = document.querySelector("#task-result");
  const objective = document.querySelector("#objective").value;
  if (!pendingCreate || pendingCreate.objective !== objective) {
    pendingCreate = { objective, key: crypto.randomUUID() };
  }
  output.textContent = "正在创建…";
  try {
    const task = await jsonRequest(`${api}/tasks`, {
      method: "POST",
      headers: commandHeaders(pendingCreate.key),
      body: JSON.stringify({ objective }),
    });
    pendingCreate = null;
    output.textContent = `任务已创建，服务端当前状态：${task.state}`;
    await loadTasks();
    await loadTask(task.id);
  } catch (error) {
    output.textContent = `创建结果未确认：${error.message}。请刷新任务列表核对。`;
  }
});

async function loadExtensions() {
  const list = document.querySelector("#extensions");
  list.replaceChildren();
  try {
    const payload = await jsonRequest(`${api}/extensions`);
    for (const extension of payload.items) {
      const item = document.createElement("li");
      item.textContent = `${extension.name} · ${extension.version} · ${extension.state}`;
      list.append(item);
    }
  } catch (error) {
    const item = document.createElement("li");
    item.textContent = error.message;
    list.append(item);
  }
}

document.querySelector("#refresh-extensions").addEventListener("click", loadExtensions);

document.querySelector("#approval-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = document.querySelector("#approval-id").value.trim();
  try {
    currentApproval = await jsonRequest(`${api}/approvals/${encodeURIComponent(id)}`);
    document.querySelector("#approval-action").textContent = JSON.stringify(
      currentApproval.action,
      null,
      2,
    );
    document.querySelector("#approval-preview").hidden = false;
  } catch (error) {
    currentApproval = null;
    document.querySelector("#approval-action").textContent = error.message;
    document.querySelector("#approval-preview").hidden = false;
  }
});

document.querySelector("#approve-button").addEventListener("click", async () => {
  if (!currentApproval?.nonce) return;
  if (!window.confirm("确认执行预览中的精确动作？")) return;
  try {
    currentApproval = await jsonRequest(`${api}/approvals/${currentApproval.id}/approve`, {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({ nonce: currentApproval.nonce }),
    });
    document.querySelector("#approval-action").textContent = `状态：${currentApproval.state}`;
  } catch (error) {
    document.querySelector("#approval-action").textContent = error.message;
  }
});

document.querySelector("#reject-button").addEventListener("click", async () => {
  if (!currentApproval) return;
  try {
    currentApproval = await jsonRequest(`${api}/approvals/${currentApproval.id}/reject`, {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({ reason: "Rejected from PWA" }),
    });
    document.querySelector("#approval-action").textContent = `状态：${currentApproval.state}`;
  } catch (error) {
    document.querySelector("#approval-action").textContent = error.message;
  }
});

let stream = null;
let lastEventId = 0;
let refreshTimer = null;
function scheduleTaskRefresh() {
  if (refreshTimer !== null) return;
  refreshTimer = window.setTimeout(() => {
    refreshTimer = null;
    void refreshTaskViews();
  }, 150);
}
function handleTaskEvent(event) {
  const sequence = Number(event.lastEventId);
  if (Number.isSafeInteger(sequence) && sequence > lastEventId) lastEventId = sequence;
  scheduleTaskRefresh();
}
function connectEvents() {
  if (stream) stream.close();
  const current = new EventSource(`${api}/events?after=${lastEventId}`);
  stream = current;
  current.onopen = () => {
    if (stream !== current) return;
    setConnectionState("事件连接已建立，正在同步服务端状态。", "online");
    void refreshTaskViews();
  };
  current.onerror = () => {
    if (stream !== current) return;
    setConnectionState(
      navigator.onLine === false
        ? "设备离线：无法确认最新任务状态。"
        : "事件连接中断：正在重连，任务状态可能已过期。",
      "offline",
    );
  };
  current.onmessage = handleTaskEvent;
  for (const type of ["task.queued", "task.message_added", "task.cancelled"]) {
    current.addEventListener(type, handleTaskEvent);
  }
}
window.addEventListener("offline", () => {
  setConnectionState("设备离线：无法确认最新任务状态。", "offline");
});
window.addEventListener("online", () => {
  setConnectionState("网络已恢复，正在连接服务…", "pending");
  connectEvents();
  void refreshTaskViews();
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") void refreshTaskViews();
});
window.setInterval(() => {
  if (document.visibilityState === "visible" && navigator.onLine !== false) {
    void refreshTaskViews();
  }
}, 30000);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("./service-worker.js");
}

loadExtensions();
void loadTasks();
connectEvents();
