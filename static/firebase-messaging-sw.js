// Firebase Messaging Service Worker
// This file MUST be served from the root path /firebase-messaging-sw.js

importScripts('https://www.gstatic.com/firebasejs/10.8.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.8.0/firebase-messaging-compat.js');

// Initialize Firebase in the service worker
// These values must match your Firebase project config
firebase.initializeApp({
    apiKey: "AIzaSyD2xT-2-KVgu1-Iyv1Wn6mXXfP8aXM3w8g",
    authDomain: "sarna-broker.firebaseapp.com",
    projectId: "sarna-broker",
    storageBucket: "sarna-broker.firebasestorage.app",
    messagingSenderId: "165530468734",
    appId: "1:165530468734:web:d7ce59ece97662fbc8332f"
});

const messaging = firebase.messaging();

// Handle background messages
messaging.onBackgroundMessage(function (payload) {
    console.log('[firebase-messaging-sw.js] Background message received:', payload);

    const notificationTitle = payload.notification?.title || 'Saarna Canvessars';
    const notificationOptions = {
        body: payload.notification?.body || 'You have a new notification',
        icon: '/static/image/Sarna broker.png',
        badge: '/static/image/Sarna broker.png',
        tag: 'sarna-notification',
        data: payload.data || {}
    };

    self.registration.showNotification(notificationTitle, notificationOptions);
});

// Handle notification click
self.addEventListener('notificationclick', function (event) {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (clientList) {
            // If a window is already open, focus it
            for (let client of clientList) {
                if (client.url.includes('/miller') && 'focus' in client) {
                    return client.focus();
                }
            }
            // Otherwise open a new window
            if (clients.openWindow) {
                return clients.openWindow('/miller');
            }
        })
    );
});
