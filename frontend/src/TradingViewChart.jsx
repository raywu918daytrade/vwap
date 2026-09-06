import { useEffect, useRef } from "react";
import { createChart, CrosshairMode, LineStyle } from "lightweight-charts";
import { plotFullDay, realCandles, sessionSlotTimes, volumePoints, twDayFromEpoch } from "./chartData.js";

const COLORS = {
  bg: "#0d1117",
  grid: "#1e242c",
  border: "#30363d",
  text: "#8b949e",
  up: "#f85149",
  down: "#3fb950",
  vwap: "#d2a8ff",
};

const MACD_KIND_TITLE = { bull: "柱體底背離", bear: "柱體頂背離", both: "底+頂背離" };
const OBV_KIND_TITLE = { bull: "OBV底背離", bear: "OBV頂背離", both: "OBV底背離+頂背離" };

function baseOptions(container, variant) {
  const isMobile = typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches;
  return {
    width: Math.max(container.clientWidth, 320),
    height: Math.max(container.clientHeight, 280),
    layout: { background: { color: COLORS.bg }, textColor: COLORS.text },
    grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
    crosshair: { mode: CrosshairMode.Normal },
    rightPriceScale: { borderColor: COLORS.border },
    handleScroll: isMobile
      ? { mouseWheel: false, pressedMouseMove: false, horzTouchDrag: false, vertTouchDrag: false }
      : true,
    handleScale: isMobile
      ? { axisPressedMouseMove: false, mouseWheel: false, pinch: false, axisDoubleClickReset: false }
      : true,
    localization: {
      timeFormatter: (t) => {
        const date = new Date(Number(t) * 1000);
        if (variant === "day") {
          return date.toLocaleDateString("zh-TW", {
            timeZone: "Asia/Taipei",
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
          });
        }
        return date.toLocaleString("zh-TW", {
          timeZone: "Asia/Taipei",
          hour12: false,
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        });
      },
    },
    timeScale: {
      borderColor: COLORS.border,
      timeVisible: variant !== "day",
      secondsVisible: false,
      rightOffset: variant === "day" ? 20 : 12,
      tickMarkFormatter: (t) => {
        const date = new Date(Number(t) * 1000);
        if (variant === "day") {
          return date.toLocaleDateString("zh-TW", {
            timeZone: "Asia/Taipei",
            month: "2-digit",
            day: "2-digit",
          });
        }
        return date.toLocaleTimeString("zh-TW", {
          timeZone: "Asia/Taipei",
          hour12: false,
          hour: "2-digit",
          minute: "2-digit",
        });
      },
    },
  };
}

function addCandles(chart, candles) {
  const series = chart.addCandlestickSeries({
    upColor: COLORS.up,
    downColor: COLORS.down,
    borderUpColor: COLORS.up,
    borderDownColor: COLORS.down,
    wickUpColor: COLORS.up,
    wickDownColor: COLORS.down,
    priceLineVisible: false,
  });
  series.setData(candles);
  return series;
}

function addVolume(chart, candles, withIndicatorPane = false) {
  if (!realCandles(candles).some((c) => c.volume != null)) return;
  chart.priceScale("right").applyOptions({
    scaleMargins: withIndicatorPane ? { top: 0.05, bottom: 0.45 } : { top: 0.08, bottom: 0.28 },
  });
  const volume = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "volume",
    priceLineVisible: false,
    lastValueVisible: false,
  });
  chart.priceScale("volume").applyOptions(
    withIndicatorPane ? { scaleMargins: { top: 0.83, bottom: 0 } } : { scaleMargins: { top: 0.8, bottom: 0 } },
  );
  volume.setData(volumePoints(candles));
}

function addVwap(chart, vwap) {
  if (!vwap?.length) return;
  const series = chart.addLineSeries({
    color: COLORS.vwap,
    lineWidth: 1,
    lineStyle: LineStyle.Dashed,
    title: "基準線",
    priceLineVisible: false,
  });
  series.setData(vwap);
}

function addPriceLine(series, price, title, color, width = 1, style = LineStyle.Solid) {
  const value = Number(price);
  if (!Number.isFinite(value)) return;
  series.createPriceLine({
    price: value,
    color,
    lineWidth: width,
    lineStyle: style,
    axisLabelVisible: true,
    title,
  });
}

function addLatestHorizontalSrLines(series, lines, prefix = "") {
  let support = null;
  let resistance = null;
  for (const line of lines || []) {
    const price = line.start_price ?? line.end_price;
    if (line.line_type === "support") support = price;
    else resistance = price;
  }
  addPriceLine(series, resistance, `${prefix}壓力`, COLORS.up, 2);
  addPriceLine(series, support, `${prefix}支撐`, COLORS.down, 2);
}

function intradayPriceBounds(candles) {
  let low = Infinity;
  let high = -Infinity;
  for (const candle of realCandles(candles)) {
    low = Math.min(low, Number(candle.low));
    high = Math.max(high, Number(candle.high));
  }
  if (!Number.isFinite(low) || !Number.isFinite(high)) return null;
  const mid = (low + high) / 2;
  const pad = Math.max(high - low, mid * 0.05);
  return { low: low - pad, high: high + pad };
}

function priceOnTrendLine(line, time) {
  const t1 = Number(line.start_time);
  const t2 = Number(line.end_time);
  const p1 = Number(line.start_price);
  const p2 = Number(line.end_price);
  if (!Number.isFinite(t1) || !Number.isFinite(t2) || !Number.isFinite(p1) || !Number.isFinite(p2)) return null;
  if (t2 === t1) return p1;
  return p1 + ((p2 - p1) * (Number(time) - t1)) / (t2 - t1);
}

function addProjectedSrLines(chart, lines, startTime, endTime, bounds, prefix = "日") {
  if (!chart || startTime == null || endTime == null) return;
  for (const line of lines || []) {
    if (line.start_time == null || line.end_time == null) continue;
    const startPrice = priceOnTrendLine(line, startTime);
    const endPrice = priceOnTrendLine(line, endTime);
    if (startPrice == null || endPrice == null) continue;
    if (bounds && (Math.max(startPrice, endPrice) < bounds.low || Math.min(startPrice, endPrice) > bounds.high)) continue;
    const isSupport = line.line_type === "support";
    const series = chart.addLineSeries({
      color: isSupport ? COLORS.down : COLORS.up,
      lineWidth: 2,
      title: isSupport ? `${prefix}支撐` : `${prefix}壓力`,
      priceLineVisible: false,
      lastValueVisible: false,
      autoscaleInfoProvider: () => ({ priceRange: null }),
    });
    series.setData([
      { time: Number(startTime), value: startPrice },
      { time: Number(endTime), value: endPrice },
    ]);
  }
}

function addHorizontalSrLines(series, extraSr) {
  if (!extraSr) return;
  addPriceLine(series, extraSr.resistance, "日壓力", COLORS.up, 2);
  addPriceLine(series, extraSr.support, "日支撐", COLORS.down, 2);
}

function addPatternLines(chart, lines) {
  for (const line of lines || []) {
    if (line.start_time == null || line.end_time == null) continue;
    const isSupport = line.line_type === "support";
    const series = chart.addLineSeries({
      color: isSupport ? COLORS.down : COLORS.up,
      lineWidth: 2,
      title: isSupport ? "支撐線" : "壓力線",
      priceLineVisible: false,
      lastValueVisible: false,
    });
    series.setData([
      { time: Number(line.start_time), value: Number(line.start_price) },
      { time: Number(line.end_time), value: Number(line.end_price) },
    ]);
  }
}

function padFullDay(chart, candles, timeframe) {
  const real = realCandles(candles);
  if (!real.length || !["1m", "3m", "5m"].includes(timeframe)) return 0;
  const day = twDayFromEpoch(real[0].time);
  const slots = sessionSlotTimes(day, timeframe);
  const lastClose = Number(real[real.length - 1].close);
  if (!Number.isFinite(lastClose)) return 0;
  const pad = chart.addLineSeries({
    color: "rgba(0,0,0,0)",
    lineWidth: 0,
    lastValueVisible: false,
    priceLineVisible: false,
    crosshairMarkerVisible: false,
    autoscaleInfoProvider: () => ({ priceRange: null }),
  });
  pad.setData(slots.map((time) => ({ time, value: lastClose })));
  chart.timeScale().applyOptions({ rightOffset: 20, lockVisibleTimeRangeOnResize: true });
  chart.timeScale().setVisibleLogicalRange({ from: 0, to: slots.length - 1 + 20 });
  return slots.length;
}

function keepFullDayRange(chart, slotCount) {
  if (!slotCount) return;
  chart.timeScale().applyOptions({ rightOffset: 20, lockVisibleTimeRangeOnResize: true });
  chart.timeScale().setVisibleLogicalRange({ from: 0, to: slotCount - 1 + 20 });
}

function emaSeries(values, span) {
  if (!values.length) return [];
  const alpha = 2 / (span + 1);
  const out = new Array(values.length);
  out[0] = values[0];
  for (let i = 1; i < values.length; i += 1) out[i] = alpha * values[i] + (1 - alpha) * out[i - 1];
  return out;
}

function macdHistFromCloses(closes) {
  if (!closes.length) return [];
  const fast = emaSeries(closes, 12);
  const slow = emaSeries(closes, 26);
  const dif = fast.map((value, index) => value - slow[index]);
  const dea = emaSeries(dif, 9);
  return dif.map((value, index) => value - dea[index]);
}

function addMacdPane(chart, candles) {
  const real = realCandles(candles);
  if (!real.length) return;
  const hist = macdHistFromCloses(real.map((candle) => Number(candle.close)));
  const series = chart.addHistogramSeries({
    priceScaleId: "macd",
    priceLineVisible: false,
    lastValueVisible: false,
    title: "MACD柱",
  });
  chart.priceScale("macd").applyOptions({ scaleMargins: { top: 0.58, bottom: 0.2 } });
  series.setData(
    real.map((candle, index) => ({
      time: candle.time,
      value: hist[index] || 0,
      color: (hist[index] || 0) >= 0 ? "rgba(248,81,73,.7)" : "rgba(63,185,80,.7)",
    })),
  );
  series.createPriceLine({
    price: 0,
    color: "rgba(255,255,255,.28)",
    lineWidth: 1,
    lineStyle: LineStyle.Dotted,
    axisLabelVisible: false,
    title: "",
  });
}

function obvFromCandles(candles) {
  const real = realCandles(candles);
  if (!real.length) return [];
  const out = new Array(real.length);
  out[0] = 0;
  for (let i = 1; i < real.length; i += 1) {
    const current = real[i];
    const previous = real[i - 1];
    const dir = Number(current.close) > Number(previous.close) ? 1 : Number(current.close) < Number(previous.close) ? -1 : 0;
    out[i] = out[i - 1] + dir * (Number(current.volume) || 0);
  }
  return out;
}

function addObvPane(chart, candles) {
  const real = realCandles(candles);
  if (!real.length || typeof chart.addBaselineSeries !== "function") return;
  const obv = obvFromCandles(real);
  const series = chart.addBaselineSeries({
    priceScaleId: "obv",
    baseValue: { type: "price", price: 0 },
    topLineColor: COLORS.up,
    topFillColor1: "rgba(248,81,73,.28)",
    topFillColor2: "rgba(248,81,73,.05)",
    bottomLineColor: COLORS.down,
    bottomFillColor1: "rgba(63,185,80,.05)",
    bottomFillColor2: "rgba(63,185,80,.28)",
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: false,
    title: "OBV",
  });
  chart.priceScale("obv").applyOptions({ scaleMargins: { top: 0.58, bottom: 0.2 } });
  series.setData(real.map((candle, index) => ({ time: candle.time, value: obv[index] || 0 })));
  series.createPriceLine({
    price: 0,
    color: "rgba(255,255,255,.28)",
    lineWidth: 1,
    lineStyle: LineStyle.Dotted,
    axisLabelVisible: false,
    title: "",
  });
}

function addIndexPane(chart, idxData, timeframe) {
  const idxCandles = idxData?.candles || [];
  if (!idxCandles.length) return;
  const plotted = ["1m", "3m", "5m"].includes(timeframe) ? plotFullDay(idxCandles, timeframe) : idxCandles;
  const series = chart.addCandlestickSeries({
    priceScaleId: "idxspread",
    upColor: COLORS.up,
    downColor: COLORS.down,
    borderUpColor: COLORS.up,
    borderDownColor: COLORS.down,
    wickUpColor: COLORS.up,
    wickDownColor: COLORS.down,
    priceLineVisible: false,
    lastValueVisible: false,
    title: "0050",
  });
  chart.priceScale("idxspread").applyOptions({ scaleMargins: { top: 0.58, bottom: 0.2 } });
  series.setData(plotted);
}

function eventsFromMap(map, stockId) {
  const rec = map?.[String(stockId)];
  if (!rec) return [];
  if (Array.isArray(rec.events)) return rec.events;
  if (rec.time) return [rec];
  return [];
}

function hitAtOrBefore(map, stockId, time) {
  const cutoff = time || "";
  const hits = eventsFromMap(map, stockId).filter((event) => (event.time || "") <= cutoff);
  if (!hits.length) return null;
  const kinds = new Set(hits.map((event) => event.kind).filter(Boolean));
  const latest = hits.reduce((a, b) => ((a.time || "") >= (b.time || "") ? a : b));
  return {
    ...latest,
    kind: kinds.size > 1 ? "both" : latest.kind || "",
    legs: hits.flatMap((event) => event.legs || [event]),
  };
}

function divergenceLegs(hit, valueKeys) {
  if (!hit) return [];
  let legs = (hit.legs || []).filter((leg) => !leg.time || leg.time <= (hit.time || ""));
  if (!legs.length && hit.t1_ts != null) {
    legs = [
      {
        kind: hit.kind,
        t1_ts: hit.t1_ts,
        t2_ts: hit.t2_ts,
        p1_ts: hit.p1_ts,
        p2_ts: hit.p2_ts,
        price1: hit.price1,
        price2: hit.price2,
        [valueKeys[0]]: hit[valueKeys[0]],
        [valueKeys[1]]: hit[valueKeys[1]],
      },
    ];
  }
  return legs.filter((leg) => leg.t1_ts != null && leg.t2_ts != null && leg.price1 != null && leg.price2 != null);
}

function overlayDivergence(chart, hit, paneScaleId, paneValueKeys) {
  const legs = divergenceLegs(hit, paneValueKeys);
  if (!legs.length) return [];
  const markers = [];
  for (const leg of legs) {
    const isBull = leg.kind === "bull";
    const color = isBull ? COLORS.down : COLORS.up;
    const priceLine = chart.addLineSeries({
      color,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    priceLine.setData([
      { time: Number(leg.p1_ts ?? leg.t1_ts), value: Number(leg.price1) },
      { time: Number(leg.p2_ts ?? leg.t2_ts), value: Number(leg.price2) },
    ]);
    const [v1, v2] = paneValueKeys;
    if (leg[v1] != null && leg[v2] != null) {
      const paneLine = chart.addLineSeries({
        color,
        lineWidth: 2,
        priceScaleId: paneScaleId,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      paneLine.setData([
        { time: Number(leg.t1_ts), value: Number(leg[v1]) },
        { time: Number(leg.t2_ts), value: Number(leg[v2]) },
      ]);
    }
    const marker1 = Number(leg.p1_ts ?? leg.t1_ts);
    const marker2 = Number(leg.p2_ts ?? leg.t2_ts);
    markers.push({
      time: marker1,
      position: isBull ? "belowBar" : "aboveBar",
      color,
      shape: isBull ? "arrowUp" : "arrowDown",
      text: isBull ? "底1" : "頂1",
    });
    markers.push({
      time: marker2,
      position: isBull ? "belowBar" : "aboveBar",
      color,
      shape: isBull ? "arrowUp" : "arrowDown",
      text: isBull ? "底2" : "頂2",
    });
  }
  return markers.sort((a, b) => Number(a.time) - Number(b.time));
}

function patternMarkers(pattern) {
  return (pattern?.pivots || [])
    .filter((pivot) => pivot.time != null)
    .sort((a, b) => Number(a.time) - Number(b.time))
    .map((pivot) => {
      const isPeak = pivot.type === "peak";
      return {
        time: Number(pivot.time),
        position: isPeak ? "aboveBar" : "belowBar",
        color: isPeak ? COLORS.up : COLORS.down,
        shape: isPeak ? "arrowDown" : "arrowUp",
        text: String(pivot.price),
      };
    });
}

function addIndicatorPane({ chart, candles, indicatorMode, indicatorStockId, indicatorEventTime, macdMap, obvMap, idxData, timeframe }) {
  if (indicatorMode === "idx") {
    addIndexPane(chart, idxData, timeframe);
    return [];
  }
  if (indicatorMode === "obv") {
    addObvPane(chart, candles);
    return overlayDivergence(chart, hitAtOrBefore(obvMap, indicatorStockId, indicatorEventTime), "obv", ["obv1", "obv2"]);
  }
  addMacdPane(chart, candles);
  return overlayDivergence(chart, hitAtOrBefore(macdMap, indicatorStockId, indicatorEventTime), "macd", ["hist1", "hist2"]);
}

export function indicatorLabel({ indicatorMode, indicatorStockId, indicatorEventTime, macdMap, obvMap }) {
  if (indicatorMode === "idx") return "0050";
  if (indicatorMode === "obv") {
    const hit = hitAtOrBefore(obvMap, indicatorStockId, indicatorEventTime);
    return hit ? OBV_KIND_TITLE[hit.kind] || "OBV背離" : "OBV";
  }
  const hit = hitAtOrBefore(macdMap, indicatorStockId, indicatorEventTime);
  return hit ? MACD_KIND_TITLE[hit.kind] || "MACD背離" : "MACD";
}

export default function TradingViewChart({
  data,
  dayLevels,
  extraSr,
  idxData,
  variant,
  timeframe,
  emptyMessage,
  showIndicatorPane = false,
  showVolume = true,
  daySrMode = "horizontal",
  indicatorMode = "macd",
  indicatorStockId,
  indicatorEventTime,
  macdMap,
  obvMap,
}) {
  const containerRef = useRef(null);

  useEffect(() => {
    const container = containerRef.current;
    const sourceCandles = data?.candles || [];
    if (!container || !sourceCandles.length) return undefined;

    container.innerHTML = "";
    const isFullDay = variant === "intraday" && ["1m", "3m", "5m"].includes(timeframe);
    const plottedCandles = isFullDay ? plotFullDay(sourceCandles, timeframe) : sourceCandles;
    const chart = createChart(container, baseOptions(container, variant));
    const candleSeries = addCandles(chart, plottedCandles);
    if (showVolume) addVolume(chart, plottedCandles, showIndicatorPane);

    if (data.pattern?.lines?.length) addPatternLines(chart, data.pattern.lines);

    let fullDaySlotCount = 0;
    if (variant === "intraday") {
      addVwap(chart, data.vwap);
      const startTime = plottedCandles[0]?.time ?? sourceCandles[0]?.time;
      const endTime = plottedCandles[plottedCandles.length - 1]?.time ?? sourceCandles[sourceCandles.length - 1]?.time;
      const bounds = intradayPriceBounds(sourceCandles);
      if (!data.pattern?.lines?.length && !extraSr) addProjectedSrLines(chart, dayLevels?.sr_lines, startTime, endTime, bounds, "日");
      addHorizontalSrLines(candleSeries, extraSr);
      fullDaySlotCount = padFullDay(chart, plottedCandles, timeframe);
    } else {
      if (daySrMode === "segments") addPatternLines(chart, data.sr_lines);
      else addLatestHorizontalSrLines(candleSeries, data.sr_lines);
      chart.timeScale().fitContent();
      chart.timeScale().applyOptions({ rightOffset: 20 });
    }

    const markers = [...patternMarkers(data.pattern)];
    if (showIndicatorPane && variant === "intraday") {
      markers.push(
        ...addIndicatorPane({
          chart,
          candles: sourceCandles,
          indicatorMode,
          indicatorStockId: indicatorStockId || data.stock_id,
          indicatorEventTime,
          macdMap,
          obvMap,
          idxData,
          timeframe,
        }),
      );
    }
    if (markers.length) candleSeries.setMarkers(markers.sort((a, b) => Number(a.time) - Number(b.time)));

    const resize = () => {
      const width = container.clientWidth;
      const height = container.clientHeight;
      if (width > 0 && height > 0) {
        chart.resize(width, height);
        if (variant === "intraday") keepFullDayRange(chart, fullDaySlotCount);
      }
    };
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    requestAnimationFrame(resize);

    return () => {
      observer.disconnect();
      chart.remove();
      container.innerHTML = "";
    };
  }, [
    data,
    dayLevels,
    extraSr,
    idxData,
    indicatorEventTime,
    indicatorMode,
    indicatorStockId,
    macdMap,
    obvMap,
    daySrMode,
    showIndicatorPane,
    showVolume,
    variant,
    timeframe,
  ]);

  if (!data?.candles?.length) {
    return (
      <div className="flex h-full min-h-[260px] items-center justify-center text-base-content/50">
        <div className="text-center">
          <div className="mb-2 text-2xl">K</div>
          <div className="text-xs">{emptyMessage || "尚無K線資料"}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="relative h-full min-h-[280px]">
      <div ref={containerRef} className="h-full w-full" />
    </div>
  );
}
