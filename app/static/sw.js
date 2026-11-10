/**
 * Alliance Grid OS Service Worker (PWA Shell Cache & Notifications)
 */

const CACHE_NAME = 'alliance-shell-v8';
const SHELL_ASSETS = [
  '/',
  '/static/manifest.json',
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/js/board.js',
  '/static/js/i18n.js',
  '/static/js/radial_dock.js',
  '/static/locales/en.json',
  '/static/locales/ru.json',
  '/static/locales/rules_catalog.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/badge-72.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(SHELL_ASSETS);
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Never cache API, WebSocket, or dynamic endpoints
  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/ws')) {
    return;
  }

  // Network first with fallback to cache for shell assets
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response && response.status === 200 && response.type === 'basic') {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});

self.addEventListener('push', (event) => {
  let data = { title: 'ALLIANCE // GRID ALERT', body: 'New tactical telemetry available.' };
  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data.body = event.data.text();
    }
  }

  const options = {
    body: data.body,
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/badge-72.png',
    vibrate: [100, 50, 100],
    data: data.data || {},
    actions: [
      { action: 'open_matrix', title: 'Open Tactical Matrix' }
    ]
  };

  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (let i = 0; i < clientList.length; i++) {
        const client = clientList[i];
        if (client.url.includes('/') && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow('/');
      }
    })
  );
});
