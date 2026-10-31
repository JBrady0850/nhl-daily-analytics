/* Offline support for the app shell. Files come from the network first, so a
   connected device always gets the newest version and the newest data; the cached
   copy is used only when offline. Bump VERSION when the file list changes. */
const VERSION = "nda-v1.14.1";
const FILES = ["./", "index.html", "css/style.css", "js/glossary.js", "js/app.js", "data/latest.js",
  "manifest.webmanifest", "icon.svg", "icon-180.png", "icon-192.png", "icon-512.png", "icon-maskable-512.png", "qr-app.png"];
self.addEventListener("install", e => e.waitUntil(caches.open(VERSION).then(c => c.addAll(FILES)).then(() => self.skipWaiting())));
self.addEventListener("activate", e => e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== VERSION).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  e.respondWith(fetch(new Request(e.request.url, { cache: "no-cache", credentials: "same-origin" })).then(res => {
    // one cached copy per file: the app adds ?ts= to skip stale copies, which
    // would otherwise leave a new cache entry behind on every check
    if (res.ok) { const copy = res.clone(); caches.open(VERSION).then(c => c.put(url.search ? url.origin + url.pathname : e.request, copy)); }
    return res;
  }).catch(() => caches.match(e.request, { ignoreSearch: true }).then(hit => hit || caches.match("index.html"))));
});
