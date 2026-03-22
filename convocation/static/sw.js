// ConvocAItion Service Worker — Push Notifications

self.addEventListener('push', (event) => {
    let data = { title: 'ConvocAItion', body: 'New update', url: '/' };
    try {
        data = event.data.json();
    } catch {}

    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            icon: '/static/icon-192.png',
            badge: '/static/icon-192.png',
            data: { url: data.url || '/' },
        })
    );
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    event.waitUntil(
        clients.openWindow(event.notification.data.url || '/')
    );
});
