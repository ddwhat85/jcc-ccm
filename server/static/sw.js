/* JCC GUARD — 홈 화면 앱용 서비스 워커.
 * 고객 화면의 껍데기(화면·글꼴·아이콘)만 보관해, 전파가 약한 현장에서도 앱이 열리고
 * '연결을 다시 잇는 중'을 보여 줄 수 있게 한다. 데이터(/api)·운영자 화면(/ops)은 절대 저장하지 않는다
 * — 경보·수치는 언제나 서버에서 새로 받는다. 화면 껍데기도 네트워크가 먼저, 안 될 때만 보관본. */
const SHELL = "jcc-guard-shell-v1";
const FILES = ["/", "/manifest.webmanifest", "/img/gd-icon-192.png", "/img/gd-apple-180.png",
  "/fonts/GothicA1-Light.woff2", "/fonts/GothicA1-Regular.woff2", "/fonts/GothicA1-Medium.woff2",
  "/fonts/GothicA1-SemiBold.woff2", "/fonts/GothicA1-Bold.woff2"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => Promise.all(FILES.map(f => c.add(f).catch(() => null)))).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith("jcc-guard-shell-") && k !== SHELL).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== self.location.origin) return;
  if (u.pathname.startsWith("/api/") || u.pathname.startsWith("/ops") || u.pathname === "/index.html") return;   // 데이터·운영자 화면은 손대지 않음
  if (r.mode === "navigate" && (u.pathname === "/" || u.pathname === "/guard")) {
    // 고객 화면: 네트워크가 먼저(늘 새 화면), 끊겼을 때만 보관본
    e.respondWith(fetch(r).then(res => { const cp = res.clone(); caches.open(SHELL).then(c => c.put("/", cp)); return res; })
      .catch(() => caches.match("/")));
    return;
  }
  if (u.pathname.startsWith("/fonts/") || u.pathname.startsWith("/img/gd-")) {
    e.respondWith(caches.match(r).then(hit => hit || fetch(r)));   // 글꼴·아이콘은 바뀌지 않는다
  }
});
