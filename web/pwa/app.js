const api = "/api/v1";
let currentApproval = null;

function commandHeaders() {
  return {
    "Content-Type": "application/json",
    "Idempotency-Key": crypto.randomUUID(),
    "X-Requested-With": "personal-assistant-pwa",
  };
}

async function jsonRequest(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error?.message || `HTTP ${response.status}`);
  }
  return payload;
}

document.querySelector("#task-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const output = document.querySelector("#task-result");
  output.textContent = "正在创建…";
  try {
    const task = await jsonRequest(`${api}/tasks`, {
      method: "POST",
      headers: commandHeaders(),
      body: JSON.stringify({ objective: document.querySelector("#objective").value }),
    });
    output.textContent = JSON.stringify(task, null, 2);
  } catch (error) {
    output.textContent = error.message;
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

const eventList = document.querySelector("#events");
const stream = new EventSource(`${api}/events`);
stream.onmessage = (event) => {
  const item = document.createElement("li");
  item.textContent = event.data;
  eventList.prepend(item);
};
stream.addEventListener("task.queued", (event) => {
  const item = document.createElement("li");
  item.textContent = event.data;
  eventList.prepend(item);
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("./service-worker.js");
}

loadExtensions();
