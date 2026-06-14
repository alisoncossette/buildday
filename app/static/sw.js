// Stead service worker — installable PWA shell.
// IMPORTANT: network-first for the page itself so UI updates show immediately
// (cache-first on the document is what made old builds "stick" during the redesign).
const CACHE = "stead-v3";
const SHELL = ["/manifest.json", "/icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api")) return; // live consent state, never cached

  // Network-first for navigations / the HTML document — always show the latest UI.
  if (e.request.mode === "navigate" || url.pathname === "/" || url.pathname.endsWith(".html")) {
    e.respondWith(fetch(e.request).catch(() => caches.match("/manifest.json")));
    return;
  }
  // Cache-first for static assets (icons, manifest, fonts).
  e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
});
