// Sentinel-MCP Service Worker — v0.2 minimal cache shell
// 策略：
//   - 静态壳（HTML/manifest/icon/CDN）：cache-first，离线可用
//   - /api/* 实时数据：network-first，离线时给一个最小占位响应
//   - 升级 SW 时立刻接管，避免老页面残留

const CACHE_NAME = 'sentinel-mcp-v1';
const SHELL = [
  '/',
  '/manifest.webmanifest',
  '/icon-192.png',
  '/icon-512.png',
  '/icon.svg',
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL).catch(() => null))
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // /api/* —— network-first
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(req).catch(() => new Response(
        JSON.stringify({ events: [], count: 0, _offline: true }),
        { headers: { 'content-type': 'application/json' } }
      ))
    );
    return;
  }

  // shell —— cache-first
  event.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((resp) => {
      // 回写到 cache（仅同源 GET 200）
      if (resp.ok && url.origin === self.location.origin) {
        const copy = resp.clone();
        caches.open(CACHE_NAME).then((c) => c.put(req, copy));
      }
      return resp;
    }).catch(() => caches.match('/')))
  );
});

// Web Push: 服务器（dashboard）通过 VAPID + push service 推过来的事件
self.addEventListener('push', (event) => {
  let payload = { title: 'Sentinel-MCP', body: '高风险事件' };
  try { if (event.data) payload = event.data.json(); } catch (_) {}
  event.waitUntil(
    self.registration.showNotification(payload.title || 'Sentinel-MCP', {
      body: payload.body || '',
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      tag: payload.tag || 'sentinel-event',
      requireInteraction: !!payload.pending_id,  // 审批通知不自动消失
      data: { url: payload.url || '/', pending_id: payload.pending_id || null },
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ('focus' in client) {
          client.focus();
          return;
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
    })
  );
});
