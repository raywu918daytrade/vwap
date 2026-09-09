const LIVE_DATE_DEFAULT_VERSION = "2";
const LIVE_DATE_DEFAULT_VERSION_KEY = "vwapLiveDateDefaultVersion";

if (typeof window !== "undefined") {
  const storage = window.localStorage;
  if (storage.getItem(LIVE_DATE_DEFAULT_VERSION_KEY) !== LIVE_DATE_DEFAULT_VERSION) {
    storage.removeItem("vwapDate");
    storage.removeItem("chartDate");
    storage.setItem(LIVE_DATE_DEFAULT_VERSION_KEY, LIVE_DATE_DEFAULT_VERSION);
  }
}

export function taipeiTodayIso() {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Taipei" });
}

// 盤勢雷達預設應該進「即時/今日」模式；空字串就是前端既有的 live sentinel。
// 保留舊函式名稱，避免為了這個修正去改動 App.jsx 的其他日期/圖表邏輯。
export function previousTaipeiWeekdayIso() {
  return "";
}

export function formatTaipeiClock() {
  return new Date().toLocaleTimeString("zh-TW", {
    timeZone: "Asia/Taipei",
    hour12: false,
  });
}

export function isTaipeiMarketHours(now = new Date()) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: "Asia/Taipei",
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(now)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  if (["Sat", "Sun"].includes(parts.weekday)) return false;
  const minutes = Number(parts.hour) * 60 + Number(parts.minute);
  return minutes >= 8 * 60 && minutes < 13 * 60 + 30;
}
