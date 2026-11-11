self.addEventListener('push', event => {
  let payload = {};
  try { payload = event.data?.json() || {}; } catch {}
  event.waitUntil(self.registration.showNotification(payload.title || 'Mill City Workspace', {
    body: payload.body || 'A website inquiry needs an owner. Open your shared lead pool.',
    icon: '/icon-512.png', badge: '/apple-touch-icon.png',
    tag: payload.tag === 'mill-city-test' ? 'mill-city-test' : 'mill-city-website',
    data: { url: '/#/sales?scope=shared' }
  }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const url = new URL('/#/sales?scope=shared', self.location.origin).href;
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of windows) {
      if (new URL(client.url).origin === self.location.origin) {
        await client.navigate(url); return client.focus();
      }
    }
    return self.clients.openWindow(url);
  })());
});
