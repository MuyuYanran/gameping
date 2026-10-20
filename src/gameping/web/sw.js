/* GamePing service worker: cache the shell, never cache API responses. */
const VERSION = 'gameping-v3';
const SHELL = [
  '/',
  '/static/style.css',
  '/static/app.js',
  '/static/favicon.svg',
  '/static/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  // API 与报告永远走网络，避免看到过期数据
  if (event.request.method !== 'GET' || url.pathname.startsWith('/api/')) return;

  event.respondWith(
    fetch(event.request)
      .then((resp) => {
        // 只缓存同源的静态资源
        if (resp.ok && url.origin === self.location.origin) {
          const copy = resp.clone();
          caches.open(VERSION).then((c) => c.put(event.request, copy));
        }
        return resp;
      })
      .catch(() => caches.match(event.request).then((hit) => hit || caches.match('/')))
  );
});
