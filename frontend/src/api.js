const API_BASE = (import.meta.env.VITE_BACKEND_URL || "").replace(/\/$/, "");

const responseCache = new Map();
const inflight = new Map();
const HISTORICAL_TTL = Number.POSITIVE_INFINITY;
const LIVE_TTL = 3000;

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

export async function fetchJson(path, options) {
  const canCache = cacheableRequest(path, options);
  if (canCache) {
    const cached = cachedValue(path);
    if (cached !== undefined) return cached;
    const pending = inflight.get(path);
    if (pending) return pending;
  }

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
