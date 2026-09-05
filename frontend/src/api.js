const API_BASE = (import.meta.env.VITE_BACKEND_URL || "").replace(/\/$/, "");

export function apiUrl(path) {
  const clean = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE}${clean}`;
}

export async function fetchJson(path, options) {
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
  return response.json();
}

export function patternDetailPath(stockId, { patternType = "none", timeframe, date, limit = 120, fullDay = false, forceLive = false }) {
  const params = new URLSearchParams({
    pattern_type: patternType || "none",
    timeframe,
    limit: String(limit),
  });
  if (date) params.set("date", date);
  if (fullDay) params.set("full_day", "true");
  if (forceLive) params.set("force_live", "true");
  return `/api/pattern/${encodeURIComponent(stockId)}/detail?${params.toString()}`;
}
