/*
 * Service Worker。CDX API は CORS ヘッダを返さないので、content script からは照会できない。
 * ここで host_permissions を使って照会し、結果を content script へ返す。
 */
importScripts("lib.js");

const INTERVAL_MS = 1000; // Wayback のレート制限は未公表。App 1 と同じく 1 秒間隔にする
const TIMEOUT_MS = 180000; // CDX は 1 回 20〜60 秒かかる
const RETRIES = 3;
const CACHE_TTL_MS = 6 * 60 * 60 * 1000;
// v2: files の結果に possiblyTruncated と alt を加えた（Issue #7）
const CACHE_VERSION = "v2";
const RETRY_STATUS = new Set([429, 502, 503, 504]);

let nextSlot = 0;
const inflight = new Map();
let pending = 0;
let keepAlive = null;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 照会の開始間隔を INTERVAL_MS 以上あける（タブをまたいで共通）。 */
async function throttle() {
  const now = Date.now();
  const wait = Math.max(0, nextSlot - now);
  nextSlot = Math.max(now, nextSlot) + INTERVAL_MS;
  if (wait) await sleep(wait);
}

/** 照会中に Service Worker が停止しないよう、拡張機能 API を定期的に呼ぶ。 */
function hold() {
  pending += 1;
  if (!keepAlive) keepAlive = setInterval(() => chrome.runtime.getPlatformInfo(() => {}), 20000);
}

function release() {
  pending -= 1;
  if (pending <= 0 && keepAlive) {
    clearInterval(keepAlive);
    keepAlive = null;
  }
}

async function fetchText(url) {
  let lastError = null;
  for (let attempt = 0; attempt <= RETRIES; attempt++) {
    await throttle();
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
    try {
      const res = await fetch(url, { signal: ctl.signal, credentials: "omit" });
      if (res.ok) return await res.text();
      lastError = new Error("HTTP " + res.status);
      if (!RETRY_STATUS.has(res.status)) break;
      const after = Number(res.headers.get("Retry-After"));
      await sleep(after > 0 ? Math.min(after, 60) * 1000 : 5000 * 2 ** attempt);
    } catch (e) {
      lastError = e.name === "AbortError" ? new Error("timeout") : e;
      await sleep(5000 * 2 ** attempt);
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError;
}

/** kind（files / landing）ごとに CDX を照会して要約を返す。成功した結果だけキャッシュする。 */
async function lookup(kind, host, id) {
  // 結果の形を変えたら CACHE_VERSION を上げる（古い形の結果を使わない。古いキーは TTL 後に掃除される）
  const key = CACHE_VERSION + ":" + kind + ":" + host + "/" + id;
  const stored = await chrome.storage.local.get(key);
  const hit = stored[key];
  if (hit && Date.now() - hit.savedAt < CACHE_TTL_MS) return hit.result;
  if (inflight.has(key)) return inflight.get(key);

  const job = (async () => {
    hold();
    try {
      const rows = WBF.parseCdxJson(await fetchText(WBF.cdxQueryUrl(kind, host, id)));
      const result = kind === "files"
        ? WBF.latestFileCaptures(rows, host, id)
        : WBF.latestLandingCapture(rows, host, id);
      await chrome.storage.local.set({ [key]: { savedAt: Date.now(), result } });
      return result;
    } finally {
      release();
      inflight.delete(key);
    }
  })();
  inflight.set(key, job);
  return job;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.type !== "cdx" || (msg.kind !== "files" && msg.kind !== "landing")) return false;
  if (!WBF.isJairoHost(msg.host) || !/^\d+$/.test(msg.id)) return false;
  lookup(msg.kind, msg.host, msg.id)
    .then((result) => sendResponse({ ok: true, result }))
    .catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
  return true; // 非同期で応答する
});

// 古いキャッシュを起動時に掃除する。
chrome.runtime.onStartup.addListener(async () => {
  const all = await chrome.storage.local.get(null);
  const old = Object.keys(all).filter((k) => !all[k] || Date.now() - all[k].savedAt >= CACHE_TTL_MS);
  if (old.length) await chrome.storage.local.remove(old);
});
