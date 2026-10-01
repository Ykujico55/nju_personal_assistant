const CACHE = "assistant-static-v11";
const STATIC = ["/ui/", "/ui/styles.css", "/ui/app.js", "/ui/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(STATIC)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || !STATIC.includes(url.pathname)) return;
  if (event.request.method !== "GET") return;
  event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
});

function validTaskId(value) {
  return typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);
}

self.addEventListener("push", (event) => {
  let hint;
  try {
    hint = event.data?.json();
  } catch (_error) {
    return;
  }
  if (!hint || hint.kind !== "pending" ||
      !["R0", "R1", "R2", "R3"].includes(hint.risk) ||
      !validTaskId(hint.task_id)) return;
  event.waitUntil(self.registration.showNotification("助手有待处理事项", {
    body: `风险等级：${hint.risk}。打开助手查看任务。`,
    tag: `pending:${hint.task_id}`,
    data: { task_id: hint.task_id },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const taskId = event.notification.data?.task_id;
  const path = validTaskId(taskId) ? `/ui/?task=${encodeURIComponent(taskId)}` : "/ui/";
  event.waitUntil(self.clients.openWindow(path));
});
