const API_BASE = (import.meta.env.VITE_BACKEND_URL || "").replace(/\/$/, "");

const responseCache = new Map();
const inflight = new Map();
const HISTORICAL_TTL = Number.POSITIVE_INFINITY;
const LIVE_TTL = 3000;
const PREFETCH_STOCKS = 10;

// The restored UI still contains one legacy 350ms debounce before loading a
// selected VWAP/watch-list chart. There is no expensive client-side compute left
// to debounce, so collapse that legacy delay to the next task without touching
// the UI component tree. Other timer durations remain unchanged.
if (typeof window !== "undefined" && !window.__vwapChartDelayRemoved) {
  const originalSetTimeout = window.setTimeout.bind(window);
  window.setTimeout = (handler, timeout, ...args) =>
    originalSetTimeout(handler, Number(timeout) === 350 ? 0 : timeout, ...args);
  window.__vwapChartDelayRemoved = true;
}

export function apiUrl(path) {
  const clean = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE}${clean}`;
}

function cacheableRequest(path, options) {
  const method = String(options?.method || "GET").toUpperCase();
  if (method !== "GET" || options?.body) return false;
  return path.startsWith("/api/pattern/") || path.startsWith("/vwap_signal/bundle");
}

function requestTtl(path) {
  try {
    const url = new URL(path, "http://local");
    const date = url.searchParams.get("date");
    if (date) {
      const today = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Taipei",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).format(new Date());
      return date === today ? LIVE_TTL : HISTORICAL_TTL;
    }
  } catch {
    // Fall through to short cache for live/default requests.
  }
  return LIVE_TTL;
}

function cachedValue(path) {
  const entry = responseCache.get(path);
  if (!entry) return undefined;
  if (entry.expiresAt !== Infinity && entry.expiresAt <= Date.now()) {
    responseCache.delete(path);
    return undefined;
  }
  return entry.value;
}

function remember(path, value) {
  const ttl = requestTtl(path);
  responseCache.set(path, {
    value,
    expiresAt: ttl === Infinity ? Infinity : Date.now() + ttl,
  });
}

function detailSiblingPaths(path) {
  try {
    const url = new URL(path, "http://local");
    if (!/^\/api\/pattern\/[^/]+\/detail$/.test(url.pathname)) return [];
    const stockId = decodeURIComponent(url.pathname.split("/")[3] || "");
    const timeframe = url.searchParams.get("timeframe");
    if (!stockId || timeframe !== "day") return [];
    const date = url.searchParams.get("date") || "";
    const siblings = [
      patternDetailPath(stockId, { timeframe: "1m", date, limit: 120, fullDay: true }),
    ];
    if (stockId !== "0050") {
      siblings.push(patternDetailPath("0050", { timeframe: "1m", date, limit: 120, fullDay: true }));
    }
    return siblings;
  } catch {
    return [];
  }
}

function prefetchPaths(paths) {
  for (const path of paths) {
    if (!path || cachedValue(path) !== undefined || inflight.has(path)) continue;
    void fetchJson(path).catch(() => {});
  }
}

function prefetchFromBundle(path, payload) {
  try {
    const url = new URL(path, "http://local");
    const date = url.searchParams.get("date") || "";
    const rows = [...(payload?.vwap || []), ...(payload?.sr || [])];
    const seen = new Set();
    const paths = [];
    for (const row of rows) {
      const sid = String(row?.stock_id || "");
      if (!sid || seen.has(sid)) continue;
      seen.add(sid);
      // Warm both chart panes. If the user clicks one of these rows, the current
      // sequential App caller will consume the same in-flight/cache entries.
      paths.push(patternDetailPath(sid, { timeframe: "day", date, limit: 120 }));
      paths.push(patternDetailPath(sid, { timeframe: "1m", date, limit: 120, fullDay: true }));
      if (seen.size >= PREFETCH_STOCKS) break;
    }
    paths.push(patternDetailPath("0050", { timeframe: "1m", date, limit: 120, fullDay: true }));
    prefetchPaths(paths);
  } catch {
    // Prefetch is best-effort only.
  }
}

function prefetchFromPatternScan(path, payload) {
  if (!path.startsWith("/api/pattern/scan")) return;
  try {
    const url = new URL(path, "http://local");
    const date = url.searchParams.get("date") || "";
    const seen = new Set();
    const paths = [];
    for (const row of payload?.results || []) {
      const sid = String(row?.stock_id || "");
      const patternType = String(row?.pattern_type || "none");
      if (!sid || seen.has(sid)) continue;
      seen.add(sid);
      paths.push(patternDetailPath(sid, { patternType, timeframe: "day", date, limit: 120 }));
      if (seen.size >= PREFETCH_STOCKS) break;
    }
    prefetchPaths(paths);
  } catch {
    // Prefetch is best-effort only.
  }
}

export async function fetchJson(path, options) {
  const canCache = cacheableRequest(path, options);
  if (canCache) {
    const cached = cachedValue(path);
    if (cached !== undefined) return cached;
    const pending = inflight.get(path);
    if (pending) return pending;
  }

  // A day-chart request immediately starts its 1m chart and 0050 siblings.
  // Thus even the legacy sequential caller performs the network/I/O concurrently.
  prefetchPaths(detailSiblingPaths(path));

  const request = (async () => {
    const response = await fetch(apiUrl(path), options);
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        detail = payload.detail || payload.error || detail;
      } catch {
        try {
          detail = await response.text();
        } catch {
          // keep HTTP status text
        }
      }
      throw new Error(detail);
    }
    const payload = await response.json();
    if (canCache) remember(path, payload);
    if (path.startsWith("/vwap_signal/bundle")) prefetchFromBundle(path, payload);
    prefetchFromPatternScan(path, payload);
    return payload;
  })();

  if (canCache) inflight.set(path, request);
  try {
    return await request;
  } finally {
    if (canCache && inflight.get(path) === request) inflight.delete(path);
  }
}

export function patternDetailPath(stockId, { patternType = "none", timeframe, date, limit = 120, fullDay = false, forceLive = false }) {
  const params = new URLSearchParams({
    pattern_type: patternType || "none",
    timeframe,
    limit: String(limit),
  });
  if (date) params.set("date", date);
  if (fullDay) params.set("full_day", "true");
  // Normal chart loads use backend cache invalidation rather than force_live.
  void forceLive;
  return `/api/pattern/${encodeURIComponent(stockId)}/detail?${params.toString()}`;
}
