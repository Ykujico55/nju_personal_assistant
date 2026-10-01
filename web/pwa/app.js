const api = "/api/v1";
const requestedTaskId = new URLSearchParams(window.location.search).get("task");
let currentApproval = null;
let selectedTaskId = null;
let nextBefore = null;
let listRequest = 0;
let detailRequest = 0;
let pendingCreate = null;
let renderedTaskDetail = null;
const messageEdits = new Map();
const formDraftEdits = new Map();
const formCreateAttempts = new Map();
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
    if (!selectedTaskId && requestedTaskId && /^[A-Za-z0-9_-]{1,128}$/.test(requestedTaskId)) {
      void loadTask(requestedTaskId);
    } else if (!selectedTaskId && page.items.length) {
      void loadTask(page.items[0].id);
    }
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
    void loadFormDraft(id);
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
    panel.replaceChildren(content, createMessageComposer(detail.task.id), createFormDraftPanel());
    panel.dataset.taskId = detail.task.id;
  } else {
    panel.querySelector(".task-detail-error")?.remove();
  }
  renderedTaskDetail = detail;
  messageEdit(detail.task.id).conflictUnresolved = false;
  updateMessageComposer(detail.task.id);
}

function createFormDraftPanel() {
  const panel = document.createElement("section");
  panel.id = "form-draft-panel";
  const title = document.createElement("h4");
  title.textContent = "Schema 表单草稿";
  const note = document.createElement("p");
  note.textContent = "此处只编辑草稿，不是可批准的权威预览，也不会提交外部表单。";
  const status = document.createElement("p");
  status.id = "form-draft-status";
  status.setAttribute("role", "status");
  status.textContent = "正在读取服务端草稿…";
  const body = document.createElement("div");
  body.id = "form-draft-body";
  panel.append(title, note, status, body);
  return panel;
}

const FORM_ROOT_KEYS = new Set(["type", "title", "description", "properties", "required", "additionalProperties"]);
const FORM_FIELD_KEYS = new Set(["type", "title", "description", "enum", "minLength", "maxLength", "minimum", "maximum"]);
const FORM_WIDGETS = new Set(["text", "textarea", "select", "number"]);
const FORM_SOURCES = { EVIDENCE: "已有证据", USER_INPUT: "用户输入", UNKNOWN: "未确定" };

function exactKeys(value, allowed) {
  return Object.keys(value).every((key) => allowed.has(key));
}

function validateDraftDescriptor(draft, taskId) {
  if (draft?.task_id !== taskId || typeof draft.extension_id !== "string" ||
      typeof draft.extension_version !== "string" || typeof draft.form_id !== "string" ||
      !Number.isSafeInteger(draft.version) || draft.version < 1 ||
      !draft.json_schema || typeof draft.json_schema !== "object" || Array.isArray(draft.json_schema) ||
      !draft.ui_schema || typeof draft.ui_schema !== "object" || Array.isArray(draft.ui_schema) ||
      !draft.values || typeof draft.values !== "object" || Array.isArray(draft.values) ||
      !draft.sources || typeof draft.sources !== "object" || Array.isArray(draft.sources)) {
    throw new Error("服务端草稿结构不完整");
  }
  const schema = draft.json_schema;
  const ui = draft.ui_schema;
  if (!exactKeys(schema, FORM_ROOT_KEYS) || schema.type !== "object" ||
      schema.additionalProperties !== false || !schema.properties ||
      typeof schema.properties !== "object" || Array.isArray(schema.properties)) {
    throw new Error("不支持此 JSON Schema 的根结构");
  }
  if ((schema.title !== undefined && typeof schema.title !== "string") ||
      (schema.description !== undefined && typeof schema.description !== "string")) {
    throw new Error("不支持此 JSON Schema 的标题或描述");
  }
  const names = Object.keys(schema.properties);
  if (!names.length || names.length > 100 ||
      names.some((name) => !name || name.length > 128 || ["__proto__", "constructor", "prototype"].includes(name))) {
    throw new Error("不支持此 JSON Schema 的字段名或数量");
  }
  const required = schema.required ?? [];
  if (!Array.isArray(required) || new Set(required).size !== required.length ||
      required.some((name) => !names.includes(name))) {
    throw new Error("不支持此 JSON Schema 的必填声明");
  }
  if (!exactKeys(ui, new Set([...names, "sensitivity", "ui:order"])) ||
      (ui.sensitivity !== undefined && !["PUBLIC", "PERSONAL", "SENSITIVE"].includes(ui.sensitivity)) ||
      (ui["ui:order"] !== undefined &&
       (!Array.isArray(ui["ui:order"]) || [...ui["ui:order"]].sort().join("\0") !== [...names].sort().join("\0")))) {
    throw new Error("不支持此 UI Schema");
  }
  for (const name of names) {
    const field = schema.properties[name];
    const fieldUi = ui[name] ?? {};
    const widget = fieldUi?.["ui:widget"];
    const compatibleWidgets = field?.enum !== undefined ? ["select"] :
      field?.type === "string" ? ["text", "textarea"] :
      field?.type === "boolean" ? ["select"] : ["number"];
    if (!field || typeof field !== "object" || Array.isArray(field) ||
        !exactKeys(field, FORM_FIELD_KEYS) ||
        !["string", "boolean", "integer", "number"].includes(field.type) ||
        !fieldUi || typeof fieldUi !== "object" || Array.isArray(fieldUi) ||
        !exactKeys(fieldUi, new Set(["ui:placeholder", "ui:widget"])) ||
        (fieldUi["ui:widget"] !== undefined && !FORM_WIDGETS.has(fieldUi["ui:widget"])) ||
        (widget !== undefined && !compatibleWidgets.includes(widget)) ||
        (fieldUi["ui:placeholder"] !== undefined && typeof fieldUi["ui:placeholder"] !== "string") ||
        (field.enum !== undefined && (!Array.isArray(field.enum) || !field.enum.length)) ||
        (field.enum !== undefined && field.enum.some((choice) =>
          field.type === "string" ? typeof choice !== "string" :
          field.type === "boolean" ? typeof choice !== "boolean" :
          field.type === "integer" ? !Number.isSafeInteger(choice) : !Number.isFinite(choice))) ||
        (field.minLength !== undefined && (field.type !== "string" || !Number.isSafeInteger(field.minLength) || field.minLength < 0)) ||
        (field.maxLength !== undefined && (field.type !== "string" || !Number.isSafeInteger(field.maxLength) || field.maxLength < 0)) ||
        (field.minimum !== undefined && (!["integer", "number"].includes(field.type) || !Number.isFinite(field.minimum))) ||
        (field.maximum !== undefined && (!["integer", "number"].includes(field.type) || !Number.isFinite(field.maximum))) ||
        (field.title !== undefined && typeof field.title !== "string") ||
        (field.description !== undefined && typeof field.description !== "string") ||
        !Object.hasOwn(FORM_SOURCES, draft.sources[name])) {
      throw new Error(`不支持字段 ${name} 的 Schema、UI Schema 或来源`);
    }
    if (Object.hasOwn(draft.values, name)) {
      const value = draft.values[name];
      if ((field.type === "string" && typeof value !== "string") ||
          (field.type === "boolean" && typeof value !== "boolean") ||
          (field.type === "integer" && !Number.isSafeInteger(value)) ||
          (field.type === "number" && !Number.isFinite(value))) {
        throw new Error(`字段 ${name} 的服务端值类型无效`);
      }
    }
  }
  if (Object.keys(draft.values).some((name) => !names.includes(name)) ||
      Object.keys(draft.sources).some((name) => !names.includes(name))) {
    throw new Error("服务端草稿含未声明字段");
  }
  return names;
}

function formEdit(taskId) {
  if (!formDraftEdits.has(taskId)) {
    formDraftEdits.set(taskId, { server: null, remote: null, values: {}, dirty: false,
      attempt: null, conflict: false, notice: "" });
  }
  return formDraftEdits.get(taskId);
}

function formDraftStatus(text) {
  document.querySelector("#form-draft-status").textContent = text;
}

function sameFormValues(left, right) {
  const keys = Object.keys(left).sort();
  return keys.length === Object.keys(right).length &&
    keys.every((key) => Object.hasOwn(right, key) && left[key] === right[key]);
}

function renderFormDraft(taskId) {
  if (selectedTaskId !== taskId) return;
  const edit = formEdit(taskId);
  const draft = edit.server;
  const names = validateDraftDescriptor(draft, taskId);
  const schema = draft.json_schema;
  const order = draft.ui_schema["ui:order"] ?? names;
  const body = document.querySelector("#form-draft-body");
  const form = document.createElement("form");
  form.id = "form-draft";
  const version = document.createElement("p");
  version.id = "form-draft-version";
  version.textContent = `草稿基准版本：${draft.version}`;
  form.append(version);
  for (const name of order) {
    const field = schema.properties[name];
    const ui = draft.ui_schema[name] ?? {};
    const row = document.createElement("div");
    row.className = "form-field";
    row.dataset.field = name;
    const label = document.createElement("label");
    const inputId = `form-field-${name}`;
    label.htmlFor = inputId;
    label.textContent = field.title || name;
    let input;
    if (field.enum || field.type === "boolean") {
      input = document.createElement("select");
      const blank = document.createElement("option");
      blank.value = "";
      blank.textContent = "未确定";
      input.append(blank);
      for (const choice of field.enum ?? [true, false]) {
        const option = document.createElement("option");
        option.value = JSON.stringify(choice);
        option.textContent = String(choice);
        input.append(option);
      }
      input.value = Object.hasOwn(edit.values, name) ? JSON.stringify(edit.values[name]) : "";
    } else if (field.type === "string" && ui["ui:widget"] === "textarea") {
      input = document.createElement("textarea");
      input.value = edit.values[name] ?? "";
    } else {
      input = document.createElement("input");
      input.type = ["integer", "number"].includes(field.type) ? "number" : "text";
      input.value = edit.values[name] ?? "";
    }
    input.id = inputId;
    if (input.type === "number") input.step = field.type === "integer" ? "1" : "any";
    if (field.maxLength !== undefined) input.maxLength = field.maxLength;
    if (field.minLength !== undefined) input.minLength = field.minLength;
    if (field.minimum !== undefined) input.min = field.minimum;
    if (field.maximum !== undefined) input.max = field.maximum;
    if (ui["ui:placeholder"] !== undefined)
      input.placeholder = ui["ui:placeholder"];
    input.readOnly = edit.attempt?.state === "unknown" || edit.attempt?.state === "sending";
    if (input.tagName === "SELECT")
      input.disabled = Boolean(edit.attempt);
    input.addEventListener("input", () => {
      if (input.tagName === "SELECT") {
        if (input.value === "") delete edit.values[name];
        else edit.values[name] = JSON.parse(input.value);
      } else if (input.value === "" && input.type === "number") delete edit.values[name];
      else edit.values[name] = input.type === "number" ? Number(input.value) : input.value;
      edit.dirty = true;
      edit.notice = "有未保存更改；来源：用户输入。";
      row.querySelector(".field-source").textContent = "来源：用户输入（未保存）";
      formDraftStatus(edit.notice);
    });
    const source = document.createElement("small");
    source.className = "field-source";
    source.textContent = `来源：${edit.dirty && edit.values[name] !== draft.values[name]
      ? "用户输入（未保存）" : FORM_SOURCES[draft.sources[name]]}`;
    row.append(label, input, source);
    form.append(row);
  }
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = edit.attempt?.state === "unknown" ? "重试同一保存请求" : "保存草稿";
  save.disabled = edit.attempt?.state === "sending";
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void saveFormDraft(taskId);
  });
  form.append(save);
  const conflict = document.createElement("div");
  conflict.id = "form-draft-conflict";
  conflict.hidden = !edit.conflict;
  if (edit.conflict) {
    const remote = document.createElement("pre");
    remote.id = "form-draft-server-version";
    remote.textContent = edit.remote
      ? `服务器版本：${edit.remote.version}\n服务端字段值：${JSON.stringify(edit.remote.values, null, 2)}`
      : "尚未读取到服务端新版本";
    conflict.append(remote);
    if (edit.remote) {
      const keep = document.createElement("button");
      keep.id = "form-draft-use-version";
      keep.type = "button";
      keep.textContent = "保留本机输入，以服务器新版保存";
      keep.addEventListener("click", () => {
        edit.server = edit.remote;
        edit.remote = null;
        edit.conflict = false;
        edit.notice = "已选择新版；本机输入尚未保存，请再次点保存。";
        renderFormDraft(taskId);
      });
      const discard = document.createElement("button");
      discard.type = "button";
      discard.textContent = "使用服务器草稿，放弃本机输入";
      discard.addEventListener("click", () => {
        edit.server = edit.remote;
        edit.values = structuredClone(edit.remote.values);
        edit.remote = null;
        edit.conflict = false;
        edit.dirty = false;
        edit.notice = "已显式载入服务器草稿。";
        renderFormDraft(taskId);
      });
      conflict.append(keep, discard);
    }
  }
  body.replaceChildren(form, conflict);
  formDraftStatus(edit.notice || `已读取服务端草稿版本 ${draft.version}。`);
}

async function loadFormDraft(taskId) {
  if (selectedTaskId !== taskId) return;
  try {
    const draft = await jsonRequest(`${api}/tasks/${encodeURIComponent(taskId)}/form-draft`);
    if (selectedTaskId !== taskId) return;
    validateDraftDescriptor(draft, taskId);
    const edit = formEdit(taskId);
    if (edit.dirty || edit.attempt || edit.conflict) {
      if (draft.version !== edit.server?.version) {
        edit.remote = draft;
        edit.conflict = true;
        edit.notice = `服务器草稿已更新到版本 ${draft.version}；本机输入未保存，请显式处理冲突。`;
      }
    } else {
      edit.server = draft;
      edit.values = structuredClone(draft.values);
    }
    renderFormDraft(taskId);
  } catch (failure) {
    if (selectedTaskId !== taskId) return;
    if (failure.status === 404) {
      void showFormCatalog(taskId);
    } else {
      document.querySelector("#form-draft-body").replaceChildren();
      formDraftStatus(`无法编辑草稿：${failure.message}。不支持或无法读取的 Schema 不会用于填表。`);
    }
  }
}

async function showFormCatalog(taskId) {
  try {
    const catalog = await jsonRequest(`${api}/forms`);
    if (selectedTaskId !== taskId) return;
    const body = document.querySelector("#form-draft-body");
    const selector = document.createElement("select");
    selector.id = "form-draft-selector";
    let unsupported = 0;
    for (const item of catalog.items) {
      const option = document.createElement("option");
      option.value = `${item.extension_id}\0${item.id}`;
      option.textContent = `${item.extension_id} · ${item.id}`;
      try {
        const fields = item.json_schema?.properties ?? {};
        validateDraftDescriptor({ ...item, task_id: taskId, form_id: item.id,
          version: 1, values: {}, sources: Object.fromEntries(
            Object.keys(fields).map((name) => [name, "UNKNOWN"]),
          ) }, taskId);
      } catch (_unsupported) {
        option.disabled = true;
        option.textContent += "（Schema 不支持）";
        unsupported += 1;
      }
      selector.append(option);
    }
    const firstSupported = [...selector.options].find((option) => !option.disabled);
    if (firstSupported) selector.value = firstSupported.value;
    const button = document.createElement("button");
    button.type = "button";
    const pending = formCreateAttempts.get(taskId);
    button.textContent = pending ? "重试同一创建请求" : "开始此 Schema 草稿";
    button.disabled = !firstSupported;
    selector.disabled = Boolean(pending);
    if (pending) selector.value = `${pending.extensionId}\0${pending.formId}`;
    button.addEventListener("click", async () => {
      const [extensionId, formId] = selector.value.split("\0");
      const selectedForm = catalog.items.find((item) =>
        item.extension_id === extensionId && item.id === formId);
      const attempt = formCreateAttempts.get(taskId) ??
        { extensionId, formId, extensionVersion: selectedForm.extension_version,
          key: crypto.randomUUID() };
      formCreateAttempts.set(taskId, attempt);
      formDraftStatus("正在创建服务端草稿…");
      try {
        const draft = await jsonRequest(`${api}/tasks/${encodeURIComponent(taskId)}/form-draft`, {
          method: "POST", headers: commandHeaders(attempt.key),
          body: JSON.stringify({ extension_id: attempt.extensionId, form_id: attempt.formId }),
        });
        validateDraftDescriptor(draft, taskId);
        if (draft.extension_id !== attempt.extensionId || draft.form_id !== attempt.formId ||
            draft.extension_version !== attempt.extensionVersion || draft.version !== 1 ||
            Object.keys(draft.values).length !== 0 ||
            Object.values(draft.sources).some((source) => source !== "UNKNOWN")) {
          throw new Error("服务端返回的草稿创建回执与所选表单或初始状态不符");
        }
        const edit = formEdit(taskId);
        edit.server = draft;
        edit.values = structuredClone(draft.values);
        edit.notice = "服务端草稿已创建。";
        renderFormDraft(taskId);
        formCreateAttempts.delete(taskId);
      } catch (failure) {
        if (failure.status >= 400 && failure.status < 500 && failure.status !== 408) {
          formCreateAttempts.delete(taskId);
          formDraftStatus(`草稿未创建：${failure.message}。`);
        } else {
          button.textContent = "重试同一创建请求";
          selector.disabled = true;
          if (!button.isConnected) body.replaceChildren(selector, button);
          formDraftStatus(`创建结果未确认：${failure.message}。请同键重试或刷新核对。`);
        }
      }
    });
    body.replaceChildren(selector, button);
    formDraftStatus(firstSupported
      ? `尚无草稿，请选择表单创建。${unsupported ? `${unsupported} 个 Schema 不支持，已禁用。` : ""}`
      : `尚无可用草稿。${unsupported ? `${unsupported} 个 Schema 不支持，已禁用。` : "没有可用表单。"}`);
  } catch (failure) {
    formDraftStatus(`无法读取表单目录：${failure.message}。`);
  }
}

async function saveFormDraft(taskId) {
  const edit = formEdit(taskId);
  if (selectedTaskId !== taskId || !edit.server || edit.attempt?.state === "sending") return;
  if (edit.conflict) {
    edit.notice = "版本冲突尚未处理；请先选择如何使用服务器新版本。";
    formDraftStatus(edit.notice);
    return;
  }
  if (!edit.dirty && !edit.attempt) {
    formDraftStatus("没有未保存更改。");
    return;
  }
  if (navigator.onLine === false) {
    formDraftStatus(edit.attempt ? "设备离线，上次保存结果未确认；恢复连接后重试同一请求。" :
      "设备离线，本机更改未保存到服务器。 ");
    return;
  }
  if (!edit.attempt) {
    edit.attempt = { version: edit.server.version, values: structuredClone(edit.values),
      key: crypto.randomUUID(), state: "sending" };
  } else edit.attempt.state = "sending";
  const attempt = edit.attempt;
  edit.notice = "正在保存…";
  renderFormDraft(taskId);
  try {
    const receipt = await jsonRequest(`${api}/tasks/${encodeURIComponent(taskId)}/form-draft`, {
      method: "PUT", headers: commandHeaders(attempt.key),
      body: JSON.stringify({ version: attempt.version, values: attempt.values }),
    });
    validateDraftDescriptor(receipt, taskId);
    if (receipt.form_id !== edit.server.form_id ||
        receipt.extension_id !== edit.server.extension_id ||
        receipt.extension_version !== edit.server.extension_version ||
        receipt.version !== attempt.version + 1 || !sameFormValues(receipt.values, attempt.values)) {
      throw new Error("服务端返回的草稿回执不完整");
    }
    edit.server = receipt;
    edit.values = structuredClone(receipt.values);
    edit.attempt = null;
    edit.dirty = false;
    edit.notice = `草稿已保存，服务器版本 ${receipt.version}。`;
    renderFormDraft(taskId);
  } catch (failure) {
    if (failure.status === 409 || failure.status === 412) {
      edit.attempt = null;
      edit.conflict = true;
      edit.notice = "版本冲突；本机输入未保存，正在读取服务器新版本。";
      renderFormDraft(taskId);
      try {
        const remote = await jsonRequest(`${api}/tasks/${encodeURIComponent(taskId)}/form-draft`);
        validateDraftDescriptor(remote, taskId);
        edit.remote = remote;
        edit.notice = `版本冲突；服务器版本 ${remote.version}，本机输入仍保留。`;
      } catch (_readFailure) {
        edit.notice = "版本冲突；尚未读取到服务器新版本，本机输入仍保留。";
      }
    } else if (failure.status >= 400 && failure.status < 500 && failure.status !== 408) {
      edit.attempt = null;
      edit.notice = `草稿未保存：${failure.message}。请修正本机输入后再保存。`;
    } else {
      attempt.state = "unknown";
      edit.notice = "保存结果未确认；本机输入与原版本、幂等键已保留，只能手动重试同一请求。";
    }
    renderFormDraft(taskId);
  }
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

function approvalShape(record, requestedId) {
  const action = record?.action;
  return record?.id === requestedId && Number.isSafeInteger(record.version)
    && typeof record.state === "string"
    && /^[0-9a-f]{64}$/.test(record.action_fingerprint || "")
    && Number.isFinite(Date.parse(record.created_at))
    && Number.isFinite(Date.parse(record.expires_at))
    && action && typeof action === "object" && !Array.isArray(action)
    && typeof action.tool_id === "string" && action.action_type === action.tool_id
    && typeof action.task_id === "string" && typeof action.extension_id === "string"
    && typeof action.extension_version === "string"
    && action.target && typeof action.target === "object" && !Array.isArray(action.target)
    && action.payload && typeof action.payload === "object" && !Array.isArray(action.payload)
    && Array.isArray(action.attachments)
    && record.review && typeof record.review.ready === "boolean"
    && typeof record.review.kind === "string";
}

function mailApprovalReady(record) {
  if (!approvalShape(record, record?.id)
    || record.state !== "WAITING_APPROVAL" || record.review.ready !== true
    || record.review.kind !== "MAIL" || typeof record.nonce !== "string"
    || record.nonce.length === 0 || Date.parse(record.expires_at) <= Date.now()) return false;
  const { action } = record;
  const payload = action.payload;
  const details = record.review.details;
  if (!details || typeof details !== "object" || Array.isArray(details)) return false;
  if (action.target.account_id !== payload.account_id
    || details.account_id !== payload.account_id
    || details.mime_sha256 !== payload.mime_sha256
    || details.from_address !== payload.from_address
    || details.subject !== payload.subject
    || typeof details.body_text !== "string"
    || !/^[0-9a-f]{64}$/.test(details.mime_sha256 || "")) return false;
  for (const field of ["to", "cc", "bcc"]) {
    if (!Array.isArray(payload[field]) || !Array.isArray(details[field])
      || JSON.stringify(payload[field]) !== JSON.stringify(details[field])) return false;
  }
  return Array.isArray(payload.attachment_hashes)
    && Array.isArray(details.attachments)
    && details.attachments.every((item) => typeof item?.name === "string"
      && /^[0-9a-f]{64}$/.test(item.sha256 || "")
      && Number.isSafeInteger(item.size_bytes))
    && JSON.stringify(payload.attachment_hashes.slice().sort())
      === JSON.stringify(details.attachments.map((item) => item.sha256).sort());
}

function renderApproval(record) {
  const ready = mailApprovalReady(record);
  const details = record.review.details;
  document.querySelector("#approval-preview").hidden = false;
  document.querySelector("#approval-action").textContent = JSON.stringify({
    approval_id: record.id,
    state: record.state,
    action_fingerprint: record.action_fingerprint,
    created_at: record.created_at,
    expires_at: record.expires_at,
    nonce: record.nonce,
    action: record.action,
  }, null, 2);
  document.querySelector("#approval-material").textContent = ready ? [
    `账户：${details.account_id}`,
    `发件人：${details.from_address}`,
    `收件人：${details.to.join(", ")}`,
    `抄送：${details.cc.join(", ") || "无"}`,
    `密送：${details.bcc.join(", ") || "无"}`,
    `主题：${details.subject}`,
    `正文：\n${details.body_text}`,
    `附件：${details.attachments.length ? details.attachments.map(
      (item) => `${item.name} · ${item.size_bytes} bytes · SHA-256 ${item.sha256}`,
    ).join("\n") : "无"}`,
    `MIME SHA-256：${details.mime_sha256}`,
  ].join("\n") : "完整内容未获助手核验，不可批准。";
  document.querySelector("#approval-status").textContent = ready
    ? "助手已核对邮件正文和附件。请逐项确认。"
    : (record.state === "WAITING_APPROVAL"
      ? record.review.reason || "预览不完整或已过期，不可批准。"
      : `状态：${record.state}`);
  document.querySelector("#approve-button").disabled = !ready;
  document.querySelector("#reject-button").disabled = !["WAITING_APPROVAL", "APPROVED"].includes(record.state);
}

function uncertainApprovalResult() {
  currentApproval = null;
  document.querySelector("#approval-status").textContent = "结果未确认。请重新载入服务端状态；不要重复点击。";
  document.querySelector("#approve-button").disabled = true;
  document.querySelector("#reject-button").disabled = true;
}

document.querySelector("#approval-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = document.querySelector("#approval-id").value.trim();
  currentApproval = null;
  document.querySelector("#approve-button").disabled = true;
  document.querySelector("#reject-button").disabled = true;
  document.querySelector("#approval-preview").hidden = false;
  document.querySelector("#approval-status").textContent = "正在从助手读取审批记录…";
  document.querySelector("#approval-action").textContent = "";
  document.querySelector("#approval-material").textContent = "";
  try {
    const record = await jsonRequest(`${api}/approvals/${encodeURIComponent(id)}`);
    if (!approvalShape(record, id)) throw new Error("审批回执不完整或不属于当前记录");
    currentApproval = record;
    renderApproval(record);
  } catch (error) {
    document.querySelector("#approval-status").textContent = `无法确认审批状态：${error.message}`;
  }
});

document.querySelector("#approve-button").addEventListener("click", async () => {
  const requested = currentApproval;
  if (!mailApprovalReady(requested) || navigator.onLine === false) return;
  if (!window.confirm("确认这份由助手核验的邮件内容？")) return;
  document.querySelector("#approve-button").disabled = true;
  try {
    const receipt = await jsonRequest(`${api}/approvals/${encodeURIComponent(requested.id)}/approve`, {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({ nonce: requested.nonce }),
    });
    if (!approvalShape(receipt, requested.id) || receipt.state !== "APPROVED"
      || receipt.action_fingerprint !== requested.action_fingerprint
      || receipt.version <= requested.version || receipt.nonce !== null) {
      throw new Error("批准回执不完整");
    }
    currentApproval = receipt;
    renderApproval(receipt);
  } catch (_error) {
    uncertainApprovalResult();
  }
});

document.querySelector("#reject-button").addEventListener("click", async () => {
  const requested = currentApproval;
  if (!requested || navigator.onLine === false) return;
  document.querySelector("#reject-button").disabled = true;
  try {
    const receipt = await jsonRequest(`${api}/approvals/${encodeURIComponent(requested.id)}/reject`, {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({ reason: "Rejected from PWA" }),
    });
    if (!approvalShape(receipt, requested.id) || receipt.state !== "CANCELLED"
      || receipt.action_fingerprint !== requested.action_fingerprint
      || receipt.version <= requested.version) throw new Error("拒绝回执不完整");
    currentApproval = receipt;
    renderApproval(receipt);
  } catch (_error) {
    uncertainApprovalResult();
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

let pushPublicKey = null;
let pushSubscription = null;
let pushUnconfirmed = null;

function pushStatus(message, { canEnable = false, canDisable = false } = {}) {
  document.querySelector("#push-status").textContent = message;
  document.querySelector("#push-enable").disabled = !canEnable;
  document.querySelector("#push-disable").disabled = !canDisable;
}

function pushKeyBytes(encoded) {
  const normalized = encoded.replace(/-/g, "+").replace(/_/g, "/");
  const bytes = atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "="));
  return Uint8Array.from(bytes, (character) => character.charCodeAt(0));
}

async function pushEndpointId(endpoint) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function pushHealth(id) {
  const health = await jsonRequest(`${api}/push/subscriptions/${id}`);
  if (health.id !== id || typeof health.active !== "boolean" ||
      typeof health.reconfigure_required !== "boolean" ||
      (health.active && health.reconfigure_required)) {
    throw new Error("订阅状态回执不完整");
  }
  return health;
}

async function readPushStatus() {
  try {
    const config = await jsonRequest(`${api}/push/config`);
    pushPublicKey = config.enabled === true && typeof config.public_key === "string"
      ? config.public_key : null;
    if (!("serviceWorker" in navigator) || !("Notification" in window)) {
      pushStatus("此浏览器不支持通知或 Service Worker。");
      return;
    }
    const registration = await navigator.serviceWorker.ready;
    if (!registration.pushManager) {
      pushStatus("此浏览器不支持 PushManager。");
      return;
    }
    pushSubscription = await registration.pushManager.getSubscription();
    if (!pushSubscription) {
      if (pushUnconfirmed) {
        pushStatus("原订阅结果未确认；浏览器订阅已变化，请关闭旧记录后重新开启。", {
          canDisable: true,
        });
        return;
      }
      pushStatus(pushPublicKey ? "未订阅通知。" : "助手未配置 Web Push；无法订阅。", {
        canEnable: pushPublicKey !== null,
      });
      return;
    }
    const id = await pushEndpointId(pushSubscription.endpoint);
    const receipt = await pushHealth(id);
    if (receipt.reconfigure_required || pushPublicKey === null) {
      pushStatus("订阅不可投递，需重新配置 Push 服务或关闭旧订阅后重新开启。", {
        canDisable: true,
      });
    } else if (receipt.active) {
      pushUnconfirmed = null;
      pushStatus("已订阅通知。", { canDisable: true });
    } else {
      pushStatus("未订阅通知。", { canEnable: true });
    }
  } catch (_error) {
    pushStatus("无法确认订阅状态；请恢复连接后重读。", {
      canEnable: pushPublicKey !== null && pushUnconfirmed !== null,
    });
  }
}

document.querySelector("#push-enable").addEventListener("click", async () => {
  if (!pushPublicKey) return;
  if (navigator.onLine === false) {
    pushStatus("设备离线：通知尚未保存。", { canEnable: true });
    return;
  }
  pushStatus("正在订阅…");
  try {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      pushStatus("通知权限未授予。", { canEnable: true });
      return;
    }
    const registration = await navigator.serviceWorker.ready;
    let subscription = await registration.pushManager.getSubscription();
    let expiredEndpoint = null;
    if (pushUnconfirmed && (!subscription ||
        subscription.endpoint !== pushUnconfirmed.endpoint)) {
      pushStatus("原订阅结果未确认；浏览器订阅已变化，请关闭旧记录后重新开启。", {
        canDisable: true,
      });
      return;
    }
    if (subscription && !pushUnconfirmed) {
      const oldId = await pushEndpointId(subscription.endpoint);
      const oldHealth = await pushHealth(oldId);
      if (oldHealth.reconfigure_required) {
        pushSubscription = subscription;
        pushStatus("订阅不可投递，需重新配置 Push 服务或关闭旧订阅后重新开启。", {
          canDisable: true,
        });
        return;
      }
      if (oldHealth.active) {
        pushSubscription = subscription;
        pushStatus("已订阅通知。", { canDisable: true });
        return;
      }
      expiredEndpoint = subscription.endpoint;
      let removed = false;
      try {
        removed = await subscription.unsubscribe();
      } catch (_error) {
        // A failed browser cleanup must never recreate the expired server record.
      }
      if (!removed || await registration.pushManager.getSubscription()) {
        pushSubscription = subscription;
        pushStatus("旧浏览器订阅无法清理；未重新开启通知。", {
          canEnable: true, canDisable: true,
        });
        return;
      }
      pushSubscription = null;
      subscription = null;
    }
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true, applicationServerKey: pushKeyBytes(pushPublicKey),
      });
      if (expiredEndpoint && subscription.endpoint === expiredEndpoint) {
        pushSubscription = subscription;
        pushStatus("Push 服务仍返回已失效端点；未保存，请重试。", { canEnable: true });
        return;
      }
    }
    pushSubscription = subscription;
    const material = subscription.toJSON();
    if (!material || material.endpoint !== subscription.endpoint ||
        typeof material.keys?.p256dh !== "string" ||
        typeof material.keys?.auth !== "string") {
      throw new Error("浏览器订阅材料不完整");
    }
    const id = await pushEndpointId(material.endpoint);
    const key = pushUnconfirmed?.endpoint === material.endpoint
      ? pushUnconfirmed.key : crypto.randomUUID();
    pushUnconfirmed = { endpoint: material.endpoint, key };
    const receipt = await jsonRequest(`${api}/push/subscriptions`, {
      method: "POST", headers: commandHeaders(key),
      body: JSON.stringify({ endpoint: material.endpoint, keys: material.keys }),
    });
    if (receipt.id !== id || !Number.isFinite(Date.parse(receipt.created_at))) {
      throw new Error("订阅回执不完整");
    }
    const health = await pushHealth(id);
    pushUnconfirmed = null;
    if (health.reconfigure_required || pushPublicKey === null) {
      pushStatus("订阅不可投递，需重新配置 Push 服务或关闭旧订阅后重新开启。", {
        canDisable: true,
      });
    } else if (health.active) {
      pushStatus("已订阅通知。", { canDisable: true });
    } else {
      pushStatus("订阅未生效；可重新开启。", { canEnable: true });
    }
  } catch (_error) {
    pushStatus("订阅结果未确认；可手动重试同一浏览器订阅。", { canEnable: true });
  }
});

document.querySelector("#push-disable").addEventListener("click", async () => {
  if (!pushSubscription && !pushUnconfirmed) return;
  if (navigator.onLine === false) {
    pushStatus("设备离线：通知尚未关闭。", { canDisable: true });
    return;
  }
  pushStatus("正在关闭通知…");
  try {
    const endpoint = pushSubscription?.endpoint || pushUnconfirmed?.endpoint;
    const id = await pushEndpointId(endpoint);
    const response = await fetch(`${api}/push/subscriptions/${id}`, {
      method: "DELETE", cache: "no-store", headers: commandHeaders(),
    });
    if (response.status !== 204) throw new Error("关闭回执不完整");
    const browserRemoved = pushSubscription ? await pushSubscription.unsubscribe() : true;
    pushSubscription = null;
    pushUnconfirmed = null;
    pushStatus(browserRemoved ? "未订阅通知。" : "助手端已关闭；浏览器订阅清理未确认。", {
      canEnable: pushPublicKey !== null,
    });
  } catch (_error) {
    pushStatus("关闭结果未确认；请重读订阅状态。", { canDisable: true });
  }
});
document.querySelector("#push-refresh").addEventListener("click", () => {
  void readPushStatus();
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("./service-worker.js");
}

void readPushStatus();
loadExtensions();
void loadTasks();
connectEvents();
