export function taipeiTodayIso() {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Taipei" });
}

export function previousTaipeiWeekdayIso() {
  const today = taipeiTodayIso();
  const date = new Date(`${today}T12:00:00+08:00`);
  const day = date.getUTCDay();
  const back = day === 6 ? 1 : day === 0 ? 2 : 0;
  date.setUTCDate(date.getUTCDate() - back);
  return date.toLocaleDateString("en-CA", { timeZone: "Asia/Taipei" });
}

export function formatTaipeiClock() {
  return new Date().toLocaleTimeString("zh-TW", {
    timeZone: "Asia/Taipei",
    hour12: false,
  });
}
