// Service worker: shows push notifications when the app is closed, and opens the task when tapped.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => { });   // required by some browsers for "install app"

self.addEventListener("push", e => {
  let d = {};
  try { const j = e.data ? e.data.json() : {}; d = j.data || j.notification || j; } catch (x) { }
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    if (wins.some(w => w.visibilityState === "visible")) return;   // app is open: it already shows its own alert
    await self.registration.showNotification(d.title || "Task Manager", {
      body: d.body || "", tag: d.tag || d.taskId || "task", renotify: true,
      icon: "/icon-192.png", badge: "/icon-192.png", vibrate: [200, 100, 200], data: { taskId: d.taskId || "" }
    });
  })());
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const id = (e.notification.data && e.notification.data.taskId) || "";
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) { if ("focus" in w) { await w.focus(); w.postMessage({ taskId: id }); return; } }
    await self.clients.openWindow("/?task=" + encodeURIComponent(id));
  })());
});
