// Browser-side fast path for the live dashboard bundle.
//
// App.jsx keeps /vwap_signal/bundle as the source of truth. During live
// trading, however, SSE used to trigger that full request again for every
// VWAP/SR/MACD/OBV event. Cache the initial bundle in the browser and apply SSE
// deltas to it. When App asks for the bundle after an event, it receives an
// in-memory Response instead of another network/DB round trip.

const NativeFetch = typeof window !== "undefined" ? window.fetch.bind(window) : null;
const NativeEventSource = typeof window !== "undefined" ? window.EventSource : null;
const todayBundles = new Map();
const metricRefreshes = new Map();

function urlOf(input) {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input?.url || "";
}

function parsedUrl(input) {
  try {
    return new URL(urlOf(input), window.location.href);
  } catch {
    return null;
  }
}

function isGet(init, input) {
  const method = String(init?.method || input?.method || "GET").toUpperCase();
  return method === "GET";
}

function isTodayBundleUrl(url) {
  return url?.pathname === "/vwap_signal/bundle" && !url.searchParams.has("date");
}

function bundleKey(url) {
  return `${url.pathname}?${url.searchParams.toString()}`;
}

function cachedResponse(payload) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "X-VWAP-Cache": "sse-memory",
    },
  });
}

function eventKey(row) {
  return [row?.stock_id, row?.time || "", row?.direction || "", row?.sr_kind || "", row?.vwap_dir || ""].join("|");
}

function mergeVwapRows(existing, fresh, repeat) {
  const incoming = [...(fresh || [])].reverse();
  if (repeat) {
    const seen = new Set();
    return [...incoming, ...(existing || [])].filter((row) => {
      const key = eventKey(row);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  const latest = new Map();
  for (const row of [...incoming, ...(existing || [])]) {
    const sid = String(row?.stock_id || "");
    if (sid && !latest.has(sid)) latest.set(sid, row);
  }
  return [...latest.values()];
}

function mergeSrRows(existing, fresh) {
  const seen = new Set();
  return [[...(fresh || [])].reverse(), existing || []]
    .flat()
    .filter((row) => {
      const key = eventKey(row);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function updateCachedBundles(mutator) {
  for (const entry of todayBundles.values()) mutator(entry.data, entry.url);
}

async function refreshMetric(metric) {
  if (!NativeFetch || !todayBundles.size) return;
  const universes = new Set();
  for (const entry of todayBundles.values()) universes.add(entry.url.searchParams.get("universe") || "daytrade");

  await Promise.all(
    [...universes].map(async (universe) => {
      const refreshKey = `${metric}:${universe}`;
      if (metricRefreshes.has(refreshKey)) return metricRefreshes.get(refreshKey);
      const task = (async () => {
        try {
          const path = metric === "macd" ? "/vwap_macd_div" : "/vwap_obv_div";
          const response = await NativeFetch(`${path}?universe=${encodeURIComponent(universe)}`, { cache: "no-store" });
          if (!response.ok) return;
          const payload = await response.json();
          for (const entry of todayBundles.values()) {
            if ((entry.url.searchParams.get("universe") || "daytrade") === universe) {
              entry.data[metric] = payload?.stocks || {};
            }
          }
        } catch {
          // The regular App refresh remains a safe fallback on the next load.
        }
      })().finally(() => metricRefreshes.delete(refreshKey));
      metricRefreshes.set(refreshKey, task);
      return task;
    }),
  );
}

async function applySseMessage(message) {
  if (!message || !todayBundles.size) return;
  if (message.type === "vwap_breakout") {
    updateCachedBundles((data, url) => {
      data.vwap = mergeVwapRows(data.vwap, message.data, url.searchParams.get("repeat") === "1");
    });
    return;
  }
  if (message.type === "sr_vwap_cross") {
    updateCachedBundles((data) => {
      data.sr = mergeSrRows(data.sr, message.data);
    });
    return;
  }
  if (message.type === "vwap_chg") {
    updateCachedBundles((data) => {
      data.chg = { ...(data.chg || {}), ...(message.stocks || {}) };
    });
    return;
  }
  if (message.type === "vwap_macd_div") await refreshMetric("macd");
  if (message.type === "vwap_obv_div") await refreshMetric("obv");
}

function invalidateTodayBundles() {
  todayBundles.clear();
}

if (NativeFetch) {
  window.fetch = async function vwapCachedFetch(input, init) {
    const url = parsedUrl(input);
    if (!url || !isGet(init, input)) return NativeFetch(input, init);

    // Manual catch-up changes several live maps at once. Force the next bundle
    // request to establish a fresh baseline after it completes.
    if (url.pathname === "/vwap_sr_catchup") {
      invalidateTodayBundles();
      return NativeFetch(input, init);
    }

    if (!isTodayBundleUrl(url)) return NativeFetch(input, init);
    const key = bundleKey(url);
    const cached = todayBundles.get(key);
    if (cached) return cachedResponse(cached.data);

    const response = await NativeFetch(input, init);
    if (response.ok) {
      try {
        const data = await response.clone().json();
        todayBundles.set(key, { url, data });
      } catch {
        // Keep the original network response untouched if parsing fails.
      }
    }
    return response;
  };
}

if (NativeEventSource) {
  class VwapEventSource {
    constructor(url, options) {
      this._inner = new NativeEventSource(url, options);
      this._onopen = null;
      this._onerror = null;
      this._onmessage = null;
      this._messageListeners = new Set();

      this._inner.onopen = (event) => this._onopen?.call(this, event);
      this._inner.onerror = (event) => this._onerror?.call(this, event);
      this._inner.onmessage = async (event) => {
        try {
          await applySseMessage(JSON.parse(event.data));
        } catch {
          // Forward malformed/unknown messages unchanged.
        }
        this._onmessage?.call(this, event);
        for (const listener of this._messageListeners) listener.call(this, event);
      };
    }

    get url() { return this._inner.url; }
    get readyState() { return this._inner.readyState; }
    get withCredentials() { return this._inner.withCredentials; }
    get onopen() { return this._onopen; }
    set onopen(handler) { this._onopen = handler; }
    get onerror() { return this._onerror; }
    set onerror(handler) { this._onerror = handler; }
    get onmessage() { return this._onmessage; }
    set onmessage(handler) { this._onmessage = handler; }

    close() { this._inner.close(); }

    addEventListener(type, listener, options) {
      if (type === "message") {
        this._messageListeners.add(listener);
        return;
      }
      this._inner.addEventListener(type, listener, options);
    }

    removeEventListener(type, listener, options) {
      if (type === "message") {
        this._messageListeners.delete(listener);
        return;
      }
      this._inner.removeEventListener(type, listener, options);
    }

    dispatchEvent(event) {
      return this._inner.dispatchEvent(event);
    }
  }

  VwapEventSource.CONNECTING = NativeEventSource.CONNECTING;
  VwapEventSource.OPEN = NativeEventSource.OPEN;
  VwapEventSource.CLOSED = NativeEventSource.CLOSED;
  window.EventSource = VwapEventSource;
}
