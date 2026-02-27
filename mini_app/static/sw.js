const CACHE_NAME = 'quiz-pwa-v2';
const OFFLINE_URLS = ['/', '/static/index.html', '/static/app.js', '/static/manifest.json'];
const SYNC_TAG = 'quiz-sync';
const DB_NAME = 'quiz_offline';
const STORE_NAME = 'pending_reviews';

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME)
      .then(c => c.addAll(OFFLINE_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k=>k!==CACHE_NAME).map(k=>caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api/review')) {
    e.respondWith(
      fetch(e.request.clone()).catch(async () => {
        const body = await e.request.clone().json().catch(()=>null);
        if (body) await saveToQueue(body);
        return new Response(JSON.stringify({success:true,offline:true}),
          {headers:{'Content-Type':'application/json'}});
      })
    );
    return;
  }
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request).then(res => {
      const clone = res.clone();
      caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
      return res;
    }))
  );
});

self.addEventListener('sync', e => {
  if (e.tag === SYNC_TAG) e.waitUntil(syncReviews());
});

function openDB() {
  return new Promise((res,rej) => {
    const req = indexedDB.open(DB_NAME,1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE_NAME,{autoIncrement:true});
    req.onsuccess = () => res(req.result);
    req.onerror   = () => rej(req.error);
  });
}

async function saveToQueue(data) {
  const db = await openDB();
  const tx = db.transaction(STORE_NAME,'readwrite');
  tx.objectStore(STORE_NAME).add({...data, timestamp: new Date().toISOString()});
}

async function syncReviews() {
  const db    = await openDB();
  const items = await new Promise(res => {
    const req = db.transaction(STORE_NAME,'readonly').objectStore(STORE_NAME).getAll();
    req.onsuccess = () => res(req.result);
  });
  if (!items.length) return;
  try {
    const r = await fetch('/api/sync',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({items})
    });
    if (r.ok) {
      db.transaction(STORE_NAME,'readwrite').objectStore(STORE_NAME).clear();
      console.log('Synced', items.length, 'reviews');
    }
  } catch(err) { console.error('Sync failed:',err); }
}
