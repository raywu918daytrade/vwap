export const TIMEFRAME_LABEL = {
  "1m": "M1K",
  "3m": "M3K",
  "5m": "M5K",
  day: "日K",
};

export function twDayFromEpoch(epoch) {
  return new Date(Number(epoch) * 1000).toLocaleDateString("en-CA", { timeZone: "Asia/Taipei" });
}

export function twHmFromEpoch(epoch) {
  return new Date(Number(epoch) * 1000).toLocaleTimeString("en-GB", {
    timeZone: "Asia/Taipei",
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function twWallEpoch(day, hm) {
  return Math.floor(Date.parse(`${day}T${hm}:00+08:00`) / 1000);
}

export function sessionSlotTimes(day, timeframe) {
  const stepMin = timeframe === "5m" ? 5 : timeframe === "3m" ? 3 : 1;
  const start = twWallEpoch(day, "09:00");
  const end = twWallEpoch(day, "13:30");
  const out = [];
  for (let t = start; t < end; t += stepMin * 60) out.push(t);
  return out;
}

export function plotFullDay(candles, timeframe) {
  if (!candles?.length || !["1m", "3m", "5m"].includes(timeframe)) return candles || [];
  const day = twDayFromEpoch(candles[0].time);
  const byTime = new Map();
  const byHm = new Map();
  for (const candle of candles) {
    byTime.set(Number(candle.time), candle);
    byHm.set(twHmFromEpoch(candle.time), candle);
  }
  const used = new Set();
  const out = [];
  for (const t of sessionSlotTimes(day, timeframe)) {
    const hit = byTime.get(t) || byHm.get(twHmFromEpoch(t));
    if (hit) {
      out.push(Number(hit.time) === t ? hit : { ...hit, time: t });
      used.add(hit);
    } else {
      out.push({ time: t });
    }
  }
  const end = twWallEpoch(day, "13:30");
  for (const candle of candles) {
    if (!used.has(candle) && Number(candle.time) >= end) out.push(candle);
  }
  return out;
}

export function realCandles(candles) {
  return (candles || []).filter((c) => c.open != null && c.close != null);
}

export function volumePoints(candles) {
  return realCandles(candles).map((c) => ({
    time: c.time,
    value: Number(c.volume) || 0,
    color: Number(c.close) >= Number(c.open) ? "rgba(248,81,73,.5)" : "rgba(63,185,80,.5)",
  }));
}

export function priceSummary(candles) {
  const real = realCandles(candles);
  if (!real.length) return null;
  const first = real[0];
  const last = real[real.length - 1];
  const open = Number(first.open);
  const close = Number(last.close);
  const chgPct = open ? ((close - open) / open) * 100 : null;
  return { close, chgPct };
}
