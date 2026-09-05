import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiUrl, fetchJson, patternDetailPath } from "./api.js";
import { formatTaipeiClock, previousTaipeiWeekdayIso, taipeiTodayIso } from "./date.js";
import { TIMEFRAME_LABEL, priceSummary } from "./chartData.js";
import TradingViewChart, { indicatorLabel } from "./TradingViewChart.jsx";

const DEFAULT_STOCK = "0050";
const PATTERN_TIMEFRAMES = [
  ["day", "日"],
  ["1m", "1分"],
  ["3m", "3分"],
  ["5m", "5分"],
];
const ACTIVITY_FILTERS = {
  day_atr: {
    label: "ATR",
    options: [
      ["0.015", "ATR>=1.5%"],
      ["0.02", "ATR>=2%"],
      ["0.025", "ATR>=2.5%"],
      ["0.03", "ATR>=3%"],
      ["0.04", "ATR>=4%"],
      ["0.05", "ATR>=5%"],
    ],
  },
  open5_rng: {
    label: "5分",
    options: [
      ["0.01", "5分>=1%"],
      ["0.02", "5分>=2%"],
      ["0.03", "5分>=3%"],
      ["0.04", "5分>=4%"],
      ["0.05", "5分>=5%"],
    ],
  },
  vol5_pr: {
    label: "量PR",
    options: [
      ["0.5", "量PR>=50"],
      ["0.7", "量PR>=70"],
      ["0.8", "量PR>=80"],
      ["0.9", "量PR>=90"],
    ],
  },
};
const ACTIVITY_STORAGE_KEYS = {
  day_atr: "vwapActDayAtr",
  open5_rng: "vwapActOpen5",
  vol5_pr: "vwapActVolPr",
};

function StatusBadge({ status }) {
  if (status === "connected") return <span className="badge badge-success badge-sm">已連線</span>;
  if (status === "error") return <span className="badge badge-error badge-sm">中斷</span>;
  return <span className="badge badge-warning badge-sm">連線中</span>;
}

function HealthLine({ health, clock }) {
  const coverage = health?.coverage?.total ? `${health.coverage.arrived}/${health.coverage.total}` : "-";
  return (
    <div className="hidden items-center gap-3 text-xs text-base-content/60 md:flex">
      <span>採集：{health?.collector || "-"}</span>
      <span>涵蓋率：{coverage}</span>
      <span>連線數：{health?.ws_clients ?? health?.sse_clients ?? "-"}</span>
      <span>更新：{clock}</span>
    </div>
  );
}

function PriceChange({ summary }) {
  if (!summary) return null;
  return (
    <div className="whitespace-nowrap text-[11px] text-base-content/50">
      <span>{summary.close.toFixed(2)}</span>
      {summary.chgPct != null ? (
        <span className={`ml-2 ${summary.chgPct >= 0 ? "text-error" : "text-success"}`}>
          {summary.chgPct >= 0 ? "+" : ""}
          {summary.chgPct.toFixed(2)}%
        </span>
      ) : null}
    </div>
  );
}

function initialSavedDate(key) {
  const saved = localStorage.getItem(key);
  if (saved != null) return saved;
  const legacy = localStorage.getItem("chartDate");
  return legacy == null ? previousTaipeiWeekdayIso() : legacy;
}

function Panel({ title, count, children, actions, className = "", width, focused = false, onFocusPanel }) {
  const style = width ? { width, minWidth: width, flex: `0 0 ${width}px` } : undefined;
  return (
    <section
      className={`flex min-h-0 flex-col overflow-hidden border bg-base-100 ${focused ? "border-primary" : "border-base-300"} ${className}`}
      style={style}
      tabIndex={-1}
      onFocusCapture={onFocusPanel}
      onMouseDown={onFocusPanel}
    >
      <div className="flex min-h-9 items-center justify-between gap-2 border-b border-base-300 bg-base-200 px-3">
        <div className="min-w-0 truncate text-xs font-semibold uppercase text-primary">
          {title} <span className="font-normal text-base-content/45">{count == null ? "" : `(${count})`}</span>
        </div>
        {actions}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">{children}</div>
    </section>
  );
}

function ChartPanel({ title, subtitle, loading, error, children, actions }) {
  return (
    <section className="flex min-h-0 flex-col overflow-hidden border border-base-300 bg-base-100">
      <div className="flex min-h-10 items-center justify-between border-b border-base-300 bg-base-200 px-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-base-content">{title}</div>
          {subtitle ? <div className="truncate text-[11px] text-base-content/50">{subtitle}</div> : null}
        </div>
        <div className="flex items-center gap-2">
          {actions}
          {loading ? <span className="loading loading-spinner loading-xs text-primary" /> : null}
        </div>
      </div>
      {error ? (
        <div className="flex flex-1 items-center justify-center p-4 text-center text-sm text-error">{error}</div>
      ) : (
        <div className="min-h-0 flex-1">{children}</div>
      )}
    </section>
  );
}

function StockCell({ row, children }) {
  return (
    <td className="max-w-[150px]">
      <div className="truncate text-sm font-bold">{row.stock_id}</div>
      <div className="truncate text-[11px] text-base-content/50">{row.stock_name || row.name || ""}</div>
      {children}
    </td>
  );
}

function Lamp({ on, kind, title }) {
  const color = kind === "bear" ? "bg-success" : kind === "both" ? "bg-primary" : "bg-error";
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${on ? color : "bg-base-300"}`} title={title} />;
}

function hm(value) {
  if (!value) return "--:--";
  if (/^\d{2}:\d{2}/.test(value)) return value.slice(0, 5);
  return new Date(value).toLocaleTimeString("zh-TW", {
    timeZone: "Asia/Taipei",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function eventKey(row) {
  return [row.stock_id, row.time || "", row.direction || "", row.sr_kind || "", row.vwap_dir || ""].join("|");
}

function latestByStock(rows) {
  const byStock = new Map();
  for (const row of rows || []) {
    const sid = String(row.stock_id);
    const prev = byStock.get(sid);
    if (!prev || (row.time || "") > (prev.time || "")) byStock.set(sid, row);
  }
  return [...byStock.values()];
}

function nowHm() {
  return new Date().toLocaleTimeString("en-GB", {
    timeZone: "Asia/Taipei",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function hitAtOrBefore(map, stockId, time) {
  const rec = map[String(stockId)];
  if (!rec) return null;
  const events = Array.isArray(rec.events) ? rec.events : rec.time ? [rec] : [];
  const cutoff = time || "";
  const hits = events.filter((item) => (item.time || "") <= cutoff);
  if (!hits.length) return null;
  const kinds = new Set(hits.map((item) => item.kind).filter(Boolean));
  const latest = hits.reduce((a, b) => ((a.time || "") >= (b.time || "") ? a : b));
  return { ...latest, kind: kinds.size > 1 ? "both" : latest.kind || "" };
}

function srEventAtOrBefore(rows, stockId, time) {
  const sid = String(stockId);
  const cutoff = time || "";
  let best = null;
  for (const row of rows || []) {
    if (String(row.stock_id) !== sid) continue;
    if (cutoff && (row.time || "") > cutoff) continue;
    if (!best || (row.time || "") > (best.time || "")) best = row;
  }
  return best || (rows || []).find((row) => String(row.stock_id) === sid) || null;
}

function sortRows(rows, key, dir) {
  return [...rows].sort((a, b) => {
    let va = a[key];
    let vb = b[key];
    if (va == null) va = key === "time" || key === "stock_id" ? "" : -Infinity;
    if (vb == null) vb = key === "time" || key === "stock_id" ? "" : -Infinity;
    if (typeof va === "string" || typeof vb === "string") {
      return dir * String(va).localeCompare(String(vb), "zh-Hant", { numeric: true });
    }
    return dir * (Number(va) - Number(vb));
  });
}

function nextShowAllSort(showAll, currentSort) {
  if (showAll && currentSort.key === "time") return { key: "chg_pct", dir: -1 };
  if (!showAll && currentSort.key === "chg_pct") return { key: "time", dir: -1 };
  return currentSort;
}

function ChartIndicatorControls({ value, onChange }) {
  return (
    <div className="join">
      {[
        ["macd", "MACD", "下方子面板顯示MACD柱體背離"],
        ["obv", "OBV", "下方子面板顯示OBV背離"],
        ["idx", "0050", "下方子面板顯示0050走勢"],
      ].map(([mode, label, title]) => (
        <button
          key={mode}
          type="button"
          title={title}
          className={`btn btn-xs join-item rounded-none ${value === mode ? "btn-primary" : ""}`}
          onClick={() => onChange(mode)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export default function App() {
  const [stockId, setStockId] = useState(() => localStorage.getItem("chartStock") || DEFAULT_STOCK);
  const [selectedEventKey, setSelectedEventKey] = useState("");
  const [chartContext, setChartContext] = useState({ kind: "manual" });
  const [chartIndicatorMode, setChartIndicatorMode] = useState(() => sessionStorage.getItem("chartIndicatorMode") || "macd");
  const [patternDate, setPatternDate] = useState(() => initialSavedDate("patternDate"));
  const [vwapDate, setVwapDate] = useState(() => initialSavedDate("vwapDate"));
  const [timeframe] = useState(() => localStorage.getItem("chartTimeframe") || "1m");
  const [dayData, setDayData] = useState(null);
  const [intradayData, setIntradayData] = useState(null);
  const [idxData, setIdxData] = useState(null);
  const [dayError, setDayError] = useState("");
  const [intradayError, setIntradayError] = useState("");
  const [loadingCharts, setLoadingCharts] = useState(false);
  const [reloadSeq, setReloadSeq] = useState(0);
  const [connection, setConnection] = useState("connecting");
  const [health, setHealth] = useState(null);
  const [clock, setClock] = useState(formatTaipeiClock());

  const [patternTypes, setPatternTypes] = useState([]);
  const [selectedPatternTypes, setSelectedPatternTypes] = useState(["triangle"]);
  const [patternTimeframe, setPatternTimeframe] = useState("day");
  const [patternLimit, setPatternLimit] = useState(120);
  const [patternMinVol, setPatternMinVol] = useState(1000);
  const [patternRows, setPatternRows] = useState([]);
  const [patternLoading, setPatternLoading] = useState(false);
  const [patternError, setPatternError] = useState("");
  const pendingPatternJobRef = useRef("");

  const [universe, setUniverse] = useState(() => localStorage.getItem("vwapUniverse") || "daytrade");
  const [universeSets, setUniverseSets] = useState({ daytrade: new Set(), full: new Set(), names: new Map() });
  const [vwapRowsRaw, setVwapRowsRaw] = useState([]);
  const [srRowsRaw, setSrRowsRaw] = useState([]);
  const [chgMap, setChgMap] = useState({});
  const [macdMap, setMacdMap] = useState({});
  const [obvMap, setObvMap] = useState({});
  const [activityMap, setActivityMap] = useState({});
  const [vwapLoading, setVwapLoading] = useState(false);
  const [vwapError, setVwapError] = useState("");
  const [vwapSearch, setVwapSearch] = useState("");
  const [repeatEvents, setRepeatEvents] = useState(() => sessionStorage.getItem("vwapRepeat") !== "0");
  const [showAllCandidates, setShowAllCandidates] = useState(() => sessionStorage.getItem("vwapShowAll") === "1");
  const [srOnly, setSrOnly] = useState(() => sessionStorage.getItem("vwapSrFilter") === "1");
  const [macdOnly, setMacdOnly] = useState(() => sessionStorage.getItem("vwapMacdFilter") === "1");
  const [obvOnly, setObvOnly] = useState(() => sessionStorage.getItem("vwapObvFilter") === "1");
  const [activityFilters, setActivityFilters] = useState(() => ({
    day_atr: sessionStorage.getItem(ACTIVITY_STORAGE_KEYS.day_atr) || "",
    open5_rng: sessionStorage.getItem(ACTIVITY_STORAGE_KEYS.open5_rng) || "",
    vol5_pr: sessionStorage.getItem(ACTIVITY_STORAGE_KEYS.vol5_pr) || "",
  }));
  const [vwapSort, setVwapSort] = useState({ key: "time", dir: -1 });
  const [obsStocks, setObsStocks] = useState(() => new Set(JSON.parse(localStorage.getItem("obsStocks") || "[]")));
  const [focusedPanel, setFocusedPanel] = useState("vwap");

  const stockIdRef = useRef(stockId);
  const activeChartDateRef = useRef("");
  const vwapDateRef = useRef(vwapDate);
  const today = useMemo(() => taipeiTodayIso(), [clock]);
  const stockName = dayData?.stock_name || intradayData?.stock_name || "";
  const titleStock = stockName && stockName !== stockId ? `${stockId} ${stockName}` : stockId;
  const daySummary = useMemo(() => priceSummary(dayData?.candles), [dayData]);
  const rightSummary = useMemo(() => priceSummary(intradayData?.candles), [intradayData]);
  const isVwapChart = chartContext.kind === "vwap" || chartContext.kind === "obs";
  const isPatternChart = chartContext.kind === "pattern";
  const activeChartTimeframe = isVwapChart ? "1m" : isPatternChart ? chartContext.timeframe || patternTimeframe : timeframe;
  const activeChartPatternType = isPatternChart ? chartContext.patternType || "none" : "none";
  const activeChartLimit = isPatternChart ? chartContext.limit || patternLimit || 120 : 120;
  const activeChartDate = isPatternChart ? patternDate : isVwapChart ? vwapDate : vwapDate || patternDate;
  const activeChartLabel = TIMEFRAME_LABEL[activeChartTimeframe] || activeChartTimeframe;
  const rightChartVariant = activeChartTimeframe === "day" ? "day" : "intraday";
  const showIndicatorPane = rightChartVariant === "intraday";

  useEffect(() => {
    stockIdRef.current = stockId;
    activeChartDateRef.current = activeChartDate;
    vwapDateRef.current = vwapDate;
  }, [stockId, activeChartDate, vwapDate]);

  const loadCharts = useCallback(async () => {
    const sid = stockId.trim();
    if (!sid) return;
    setLoadingCharts(true);
    setDayError("");
    setIntradayError("");
    setIdxData(null);
    const forceLive = !activeChartDate || activeChartDate === today;
    const chartForceLive = isPatternChart && activeChartDate ? false : forceLive;
    const shouldFetchIdx = rightChartVariant === "intraday" && chartIndicatorMode === "idx" && sid !== DEFAULT_STOCK;
    const [dayResult, intradayResult, idxResult] = await Promise.allSettled([
      fetchJson(patternDetailPath(sid, { timeframe: "day", date: activeChartDate, limit: 120, forceLive })),
      fetchJson(
        patternDetailPath(sid, {
          patternType: activeChartPatternType,
          timeframe: activeChartTimeframe,
          date: activeChartDate,
          limit: activeChartLimit,
          fullDay: isVwapChart || (!isPatternChart && activeChartTimeframe !== "day"),
          forceLive: chartForceLive,
        }),
      ),
      shouldFetchIdx
        ? fetchJson(
            patternDetailPath(DEFAULT_STOCK, {
              timeframe: activeChartTimeframe,
              date: activeChartDate,
              limit: activeChartLimit,
              fullDay: true,
              forceLive: chartForceLive,
            }),
          )
        : Promise.resolve(null),
    ]);
    if (dayResult.status === "fulfilled") {
      setDayData(dayResult.value);
    } else {
      setDayData(null);
      setDayError(dayResult.reason?.message || "日K載入失敗");
    }
    if (intradayResult.status === "fulfilled") {
      setIntradayData(intradayResult.value);
    } else {
      setIntradayData(null);
      setIntradayError(intradayResult.reason?.message || `${activeChartLabel}載入失敗`);
    }
    if (idxResult.status === "fulfilled") setIdxData(idxResult.value);
    setLoadingCharts(false);
  }, [
    activeChartLabel,
    activeChartLimit,
    activeChartPatternType,
    activeChartTimeframe,
    activeChartDate,
    chartIndicatorMode,
    isPatternChart,
    isVwapChart,
    rightChartVariant,
    stockId,
    today,
  ]);

  const selectStock = useCallback((sid, key = "", kind = "manual") => {
    const next = String(sid);
    setStockId(next);
    setSelectedEventKey(key);
    setChartContext({ kind });
    if (kind === "vwap" || kind === "obs" || kind === "pattern") setFocusedPanel(kind);
  }, []);

  const loadVwapTables = useCallback(async () => {
    setVwapLoading(true);
    setVwapError("");
    try {
      if (vwapDate) {
        const qs = new URLSearchParams({ date: vwapDate, universe });
        const [replay, activity, macd, obv] = await Promise.all([
          fetchJson(`/vwap_sr_replay?${qs.toString()}`),
          fetchJson(`/vwap_activity?date=${encodeURIComponent(vwapDate)}`),
          fetchJson(`/vwap_macd_div?${qs.toString()}`),
          fetchJson(`/vwap_obv_div?${qs.toString()}`),
        ]);
        setVwapRowsRaw(replay.vwap || []);
        setSrRowsRaw(replay.sr || []);
        setChgMap(replay.chg || {});
        setActivityMap(activity.stocks || {});
        setMacdMap(macd.stocks || {});
        setObvMap(obv.stocks || {});
      } else {
        const qs = new URLSearchParams({ universe });
        const [vwap, sr, chg, activity, macd, obv] = await Promise.all([
          fetchJson("/vwap_breakout/today"),
          fetchJson("/sr_vwap_cross/today"),
          fetchJson("/vwap_chg"),
          fetchJson("/vwap_activity"),
          fetchJson(`/vwap_macd_div?${qs.toString()}`),
          fetchJson(`/vwap_obv_div?${qs.toString()}`),
        ]);
        setVwapRowsRaw(vwap || []);
        setSrRowsRaw(sr || []);
        setChgMap(chg || {});
        setActivityMap(activity.stocks || {});
        setMacdMap(macd.stocks || {});
        setObvMap(obv.stocks || {});
      }
    } catch (error) {
      setVwapError(error.message || "VWAP 載入失敗");
    } finally {
      setVwapLoading(false);
    }
  }, [vwapDate, universe]);

  useEffect(() => {
    localStorage.setItem("chartStock", stockId);
    localStorage.setItem("patternDate", patternDate);
    localStorage.setItem("vwapDate", vwapDate);
    localStorage.setItem("chartTimeframe", timeframe);
  }, [stockId, patternDate, vwapDate, timeframe]);

  useEffect(() => {
    localStorage.setItem("vwapUniverse", universe);
  }, [universe]);

  useEffect(() => {
    sessionStorage.setItem("vwapRepeat", repeatEvents ? "1" : "0");
    sessionStorage.setItem("vwapShowAll", showAllCandidates ? "1" : "0");
    sessionStorage.setItem("vwapSrFilter", srOnly ? "1" : "0");
    sessionStorage.setItem("vwapMacdFilter", macdOnly ? "1" : "0");
    sessionStorage.setItem("vwapObvFilter", obvOnly ? "1" : "0");
  }, [repeatEvents, showAllCandidates, srOnly, macdOnly, obvOnly]);

  useEffect(() => {
    sessionStorage.setItem("chartIndicatorMode", chartIndicatorMode);
  }, [chartIndicatorMode]);

  useEffect(() => {
    for (const [key, storageKey] of Object.entries(ACTIVITY_STORAGE_KEYS)) {
      sessionStorage.setItem(storageKey, activityFilters[key] || "");
    }
  }, [activityFilters]);

  useEffect(() => {
    localStorage.setItem("obsStocks", JSON.stringify([...obsStocks]));
  }, [obsStocks]);

  useEffect(() => {
    loadCharts();
  }, [loadCharts, reloadSeq]);

  useEffect(() => {
    let stopped = false;
    async function loadUniverse() {
      try {
        const [daytrade, full] = await Promise.all([
          fetchJson("/api/pattern/stocks/daytrade"),
          fetchJson("/api/pattern/stocks/full"),
        ]);
        if (stopped) return;
        const daySet = new Set((daytrade.stocks || []).map((row) => String(row.stock_id)));
        const fullSet = new Set((full.stocks || []).map((row) => String(row.stock_id)));
        daySet.forEach((sid) => fullSet.add(sid));
        const names = new Map();
        for (const row of [...(daytrade.stocks || []), ...(full.stocks || [])]) {
          names.set(String(row.stock_id), row.name || "");
        }
        setUniverseSets({ daytrade: daySet, full: fullSet, names });
      } catch {
        if (!stopped) setUniverseSets({ daytrade: new Set(), full: new Set(), names: new Map() });
      }
    }
    loadUniverse();
    return () => {
      stopped = true;
    };
  }, []);

  useEffect(() => {
    let stopped = false;
    async function loadPatternTypes() {
      try {
        const data = await fetchJson("/api/pattern/types");
        if (!stopped) setPatternTypes(data.patterns || []);
      } catch (error) {
        if (!stopped) setPatternError(error.message || "型態清單載入失敗");
      }
    }
    loadPatternTypes();
    return () => {
      stopped = true;
    };
  }, []);

  useEffect(() => {
    let stopped = false;
    async function pollHealth() {
      try {
        const data = await fetchJson("/health");
        if (!stopped) setHealth(data);
      } catch {
        if (!stopped) setHealth(null);
      }
      if (!stopped) setClock(formatTaipeiClock());
    }
    pollHealth();
    const timer = setInterval(pollHealth, 10000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    loadVwapTables();
  }, [loadVwapTables]);

  useEffect(() => {
    if (!selectedPatternTypes.length) {
      setPatternRows([]);
      setPatternLoading(false);
      setPatternError("");
      pendingPatternJobRef.current = "";
      return;
    }

    let stopped = false;
    let fallbackTimer = 0;
    const patternType = selectedPatternTypes.includes("all") ? "all" : selectedPatternTypes.join(",");
    async function submitPatternScan() {
      setPatternLoading(true);
      setPatternError("");
      setPatternRows([]);
      try {
        const params = new URLSearchParams({
          pattern_type: patternType,
          timeframe: patternTimeframe,
          limit: String(patternLimit || 120),
          min_vol_lots: String(patternMinVol || 0),
        });
        if (patternDate) params.set("date", patternDate);
        const data = await fetchJson(`/api/pattern/scan/submit?${params.toString()}`);
        if (stopped) return;
        pendingPatternJobRef.current = data.job_id;
        fallbackTimer = window.setTimeout(async () => {
          if (stopped || pendingPatternJobRef.current !== data.job_id) return;
          try {
            const direct = await fetchJson(`/api/pattern/scan?${params.toString()}`);
            if (stopped || pendingPatternJobRef.current !== data.job_id) return;
            pendingPatternJobRef.current = "";
            setPatternRows(direct.results || []);
            setPatternLoading(false);
          } catch (error) {
            if (!stopped && pendingPatternJobRef.current === data.job_id) {
              setPatternError(error.message || "型態掃描失敗");
              setPatternLoading(false);
            }
          }
        }, 8000);
      } catch (error) {
        if (!stopped) {
          setPatternError(error.message || "型態掃描送出失敗");
          setPatternLoading(false);
        }
      }
    }
    submitPatternScan();
    return () => {
      stopped = true;
      window.clearTimeout(fallbackTimer);
    };
  }, [selectedPatternTypes, patternTimeframe, patternLimit, patternMinVol, patternDate]);

  useEffect(() => {
    const es = new EventSource(apiUrl("/stream"));
    es.onopen = () => setConnection("connected");
    es.onerror = () => setConnection("error");
    es.onmessage = (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      if (message.type === "pattern_scan_done" && message.job_id === pendingPatternJobRef.current) {
        pendingPatternJobRef.current = "";
        setPatternLoading(false);
        if (message.error) {
          setPatternError(message.error);
          setPatternRows([]);
        } else {
          setPatternRows(message.data?.results || []);
        }
      }
      if (!vwapDateRef.current) {
        if (["vwap_breakout", "sr_vwap_cross", "vwap_macd_div", "vwap_obv_div"].includes(message.type)) {
          loadVwapTables();
        }
        if (message.type === "vwap_chg") setChgMap(message.stocks || {});
        if (message.type === "candles" && !activeChartDateRef.current && String(message.stock_id) === String(stockIdRef.current)) {
          setReloadSeq((n) => n + 1);
        }
      }
    };
    return () => es.close();
  }, [loadVwapTables]);

  const allowedUniverse = universe === "full" ? universeSets.full : universeSets.daytrade;
  const firstSrByStock = useMemo(() => {
    const out = new Map();
    for (const row of srRowsRaw) {
      const sid = String(row.stock_id);
      const t = row.time || "";
      if (!out.has(sid) || t < out.get(sid)) out.set(sid, t);
    }
    return out;
  }, [srRowsRaw]);

  const vwapRows = useMemo(() => {
    let source = [...vwapRowsRaw];
    if (showAllCandidates && allowedUniverse.size) {
      const latest = new Map(latestByStock(vwapRowsRaw).map((row) => [String(row.stock_id), row]));
      source = [...allowedUniverse].map((sid) => latest.get(sid) || { stock_id: sid, name: universeSets.names.get(sid) || "", time: "" });
    } else {
      const existing = new Set(source.map((row) => String(row.stock_id)));
      for (const row of srRowsRaw) {
        const sid = String(row.stock_id);
        if (!existing.has(sid)) {
          existing.add(sid);
          source.push({ stock_id: sid, name: row.name || universeSets.names.get(sid) || "", time: "" });
        }
      }
    }

    if (allowedUniverse.size && !showAllCandidates && vwapDate === "") {
      source = source.filter((row) => allowedUniverse.has(String(row.stock_id)));
    }

    if (!repeatEvents || showAllCandidates) source = latestByStock(source);

    let rows = source.map((row) => {
      const cutoff = row.time || nowHm();
      const macd = hitAtOrBefore(macdMap, row.stock_id, cutoff);
      const obv = hitAtOrBefore(obvMap, row.stock_id, cutoff);
      return {
        ...row,
        name: row.name || universeSets.names.get(String(row.stock_id)) || "",
        sr_on: firstSrByStock.has(String(row.stock_id)) ? 1 : 0,
        macd_on: macd ? 1 : 0,
        macd_kind: macd?.kind || "",
        obv_on: obv ? 1 : 0,
        obv_kind: obv?.kind || "",
        chg_pct: chgMap[String(row.stock_id)],
      };
    });

    const search = vwapSearch.trim().toLowerCase();
    if (search) {
      rows = rows.filter((row) => String(row.stock_id).includes(search) || String(row.name || "").toLowerCase().includes(search));
    }
    const hasActivityFilter = Object.values(activityFilters).some(Boolean);
    if (hasActivityFilter && Object.keys(activityMap).length) {
      for (const [key, value] of Object.entries(activityFilters)) {
        if (!value) continue;
        const threshold = Number(value);
        rows = rows.filter((row) => {
          const metric = activityMap[String(row.stock_id)]?.[key];
          return metric != null && Number(metric) >= threshold;
        });
      }
    }
    if (srOnly) rows = rows.filter((row) => row.sr_on);
    if (macdOnly && Object.keys(macdMap).length) rows = rows.filter((row) => row.macd_on);
    if (obvOnly && Object.keys(obvMap).length) rows = rows.filter((row) => row.obv_on);
    return sortRows(rows, vwapSort.key, vwapSort.dir);
  }, [
    activityFilters,
    activityMap,
    allowedUniverse,
    chgMap,
    firstSrByStock,
    macdMap,
    macdOnly,
    obvMap,
    obvOnly,
    repeatEvents,
    showAllCandidates,
    srOnly,
    srRowsRaw,
    universeSets.names,
    vwapRowsRaw,
    vwapDate,
    vwapSearch,
    vwapSort,
  ]);

  const obsRows = useMemo(() => {
    const latest = new Map(latestByStock(vwapRowsRaw).map((row) => [String(row.stock_id), row]));
    return [...obsStocks]
      .map((sid) => latest.get(sid) || { stock_id: sid, name: universeSets.names.get(sid) || "", time: "" })
      .map((row) => {
        const cutoff = row.time || nowHm();
        const macd = hitAtOrBefore(macdMap, row.stock_id, cutoff);
        const obv = hitAtOrBefore(obvMap, row.stock_id, cutoff);
        return {
          ...row,
          name: row.name || universeSets.names.get(String(row.stock_id)) || "",
          sr_on: firstSrByStock.has(String(row.stock_id)) ? 1 : 0,
          macd_on: macd ? 1 : 0,
          macd_kind: macd?.kind || "",
          obv_on: obv ? 1 : 0,
          obv_kind: obv?.kind || "",
          chg_pct: chgMap[String(row.stock_id)],
        };
      })
      .sort((a, b) => String(a.stock_id).localeCompare(String(b.stock_id), "zh-Hant", { numeric: true }));
  }, [chgMap, firstSrByStock, macdMap, obvMap, obsStocks, universeSets.names, vwapRowsRaw]);

  const selectedChartRow = useMemo(() => {
    if (!isVwapChart) return null;
    const rows = chartContext.kind === "obs" ? obsRows : vwapRows;
    if (selectedEventKey) {
      return rows.find((row) => eventKey(row) === selectedEventKey) || rows.find((row) => String(row.stock_id) === String(stockId)) || null;
    }
    return rows.find((row) => String(row.stock_id) === String(stockId)) || null;
  }, [chartContext.kind, isVwapChart, obsRows, selectedEventKey, stockId, vwapRows]);

  const selectedSrEvent = useMemo(() => {
    if (!showIndicatorPane || !selectedChartRow?.sr_on) return null;
    return srEventAtOrBefore(srRowsRaw, stockId, selectedChartRow.time || nowHm());
  }, [selectedChartRow, showIndicatorPane, srRowsRaw, stockId]);

  const extraSr = useMemo(
    () => (selectedSrEvent ? { resistance: selectedSrEvent.resistance, support: selectedSrEvent.support } : null),
    [selectedSrEvent],
  );
  const indicatorEventTime = selectedChartRow?.time || nowHm();
  const chartIndicatorLabel = showIndicatorPane
    ? indicatorLabel({
        indicatorMode: chartIndicatorMode,
        indicatorStockId: stockId,
        indicatorEventTime,
        macdMap,
        obvMap,
      })
    : "";
  const patternTitle = isPatternChart && intradayData?.pattern_name ? `・${intradayData.pattern_name}` : "";
  const srTitle = showIndicatorPane && extraSr ? "・壓力支撐" : "";
  const indicatorTitle = showIndicatorPane && chartIndicatorLabel ? `・${chartIndicatorLabel}` : "";
  const rightChartTitle = `${titleStock} ${activeChartLabel}${patternTitle}${srTitle}${indicatorTitle}`;
  const selectPatternRow = useCallback((row) => {
    const sid = String(row.stock_id);
    setStockId(sid);
    setSelectedEventKey("");
    setFocusedPanel("pattern");
    setChartContext({
      kind: "pattern",
      patternType: row.pattern_type,
      timeframe: patternTimeframe,
      limit: patternLimit,
    });
  }, [patternLimit, patternTimeframe]);

  function togglePatternType(id) {
    setSelectedPatternTypes((prev) => {
      if (id === "all") return prev.includes("all") ? [] : ["all"];
      const base = prev.filter((x) => x !== "all");
      return base.includes(id) ? base.filter((x) => x !== id) : [...base, id];
    });
  }

  function toggleObsStock(stock) {
    const sid = String(stock);
    setObsStocks((prev) => {
      const next = new Set(prev);
      if (next.has(sid)) next.delete(sid);
      else next.add(sid);
      return next;
    });
  }

  function sortVwap(key) {
    setVwapSort((prev) => (prev.key === key ? { key, dir: -prev.dir } : { key, dir: key === "stock_id" ? 1 : -1 }));
  }

  async function replayVwap() {
    const targetDate = vwapDate || today;
    if (targetDate !== today) {
      await loadVwapTables();
      return;
    }
    setVwapLoading(true);
    setVwapError("");
    try {
      const data = await fetchJson("/vwap_sr_catchup");
      const qs = new URLSearchParams({ universe });
      const [activity, macd, obv, chg] = await Promise.all([
        fetchJson("/vwap_activity"),
        fetchJson(`/vwap_macd_div?${qs.toString()}`),
        fetchJson(`/vwap_obv_div?${qs.toString()}`),
        fetchJson("/vwap_chg"),
      ]);
      setVwapDate("");
      setVwapRowsRaw(data.vwap || []);
      setSrRowsRaw(data.sr || []);
      setActivityMap(activity.stocks || {});
      setMacdMap(macd.stocks || {});
      setObvMap(obv.stocks || {});
      setChgMap(chg || {});
    } catch (error) {
      setVwapError(error.message || "重現失敗");
    } finally {
      setVwapLoading(false);
    }
  }

  function toggleShowAllCandidates() {
    setShowAllCandidates((current) => {
      const next = !current;
      setVwapSort((prev) => nextShowAllSort(next, prev));
      return next;
    });
  }

  function setActivityFilter(key, value) {
    setActivityFilters((prev) => ({ ...prev, [key]: value }));
  }

  useEffect(() => {
    function onKeyDown(event) {
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      const target = event.target;
      const tag = (target?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || tag === "button" || target?.isContentEditable) return;

      const direction = event.key === "ArrowDown" ? 1 : -1;
      const rows = focusedPanel === "pattern" ? patternRows : focusedPanel === "obs" ? obsRows : vwapRows;
      if (!rows.length) return;

      event.preventDefault();

      let index = -1;
      if (focusedPanel === "pattern") {
        index = rows.findIndex(
          (row) =>
            String(row.stock_id) === String(stockId) &&
            (!isPatternChart || String(row.pattern_type || "") === String(chartContext.patternType || "")),
        );
      } else if (selectedEventKey) {
        index = rows.findIndex((row) => eventKey(row) === selectedEventKey);
      } else {
        index = rows.findIndex((row) => String(row.stock_id) === String(stockId));
      }

      const nextIndex =
        index === -1 ? (direction > 0 ? 0 : rows.length - 1) : Math.max(0, Math.min(rows.length - 1, index + direction));
      const next = rows[nextIndex];
      if (!next) return;

      if (focusedPanel === "pattern") {
        selectPatternRow(next);
      } else {
        selectStock(next.stock_id, eventKey(next), focusedPanel === "obs" ? "obs" : "vwap");
      }

      window.requestAnimationFrame(() => {
        document.querySelector(`[data-panel="${focusedPanel}"][data-row-index="${nextIndex}"]`)?.scrollIntoView({ block: "nearest" });
      });
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [
    chartContext.patternType,
    focusedPanel,
    isPatternChart,
    obsRows,
    patternRows,
    selectPatternRow,
    selectStock,
    selectedEventKey,
    stockId,
    vwapRows,
  ]);

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-base-100 font-mono text-base-content">
      <header className="border-b border-base-300 bg-base-200">
        <div className="flex min-h-12 flex-wrap items-center justify-between gap-2 px-3 py-2">
          <div className="flex items-center gap-3">
            <div className="text-sm font-bold text-primary">VWAP 型態監控</div>
            <StatusBadge status={connection} />
          </div>
          <HealthLine health={health} clock={clock} />
        </div>
      </header>

      <main className="grid min-h-0 flex-1 grid-rows-[minmax(220px,45%)_minmax(260px,55%)] gap-2 p-2">
        <div className="flex min-h-0 gap-2 overflow-x-auto overflow-y-hidden">
          <Panel
            title="型態掃描"
            count={patternRows.length}
            actions={patternLoading ? <span className="loading loading-spinner loading-xs text-primary" /> : null}
            width={370}
            focused={focusedPanel === "pattern"}
            onFocusPanel={() => setFocusedPanel("pattern")}
          >
            <div className="flex flex-wrap gap-1 border-b border-base-300 bg-base-200 p-2">
              <select className="select select-bordered select-xs rounded" value={patternTimeframe} onChange={(e) => setPatternTimeframe(e.target.value)}>
                {PATTERN_TIMEFRAMES.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <input
                type="date"
                className="input input-bordered input-xs w-32 rounded"
                value={patternDate}
                title="基準日期，留空＝最新交易日"
                onChange={(e) => setPatternDate(e.target.value)}
              />
              <input
                type="number"
                className="input input-bordered input-xs w-16 rounded"
                value={patternLimit}
                min="20"
                max="500"
                step="10"
                onChange={(e) => setPatternLimit(Number(e.target.value) || 120)}
              />
              <input
                type="number"
                className="input input-bordered input-xs w-20 rounded"
                value={patternMinVol}
                min="0"
                step="100"
                onChange={(e) => setPatternMinVol(Number(e.target.value) || 0)}
              />
              <details className="dropdown">
                <summary className="btn btn-xs rounded">
                  型態 {selectedPatternTypes.includes("all") ? "全部" : selectedPatternTypes.length || "未選"}
                </summary>
                <div className="menu dropdown-content z-20 mt-1 grid max-h-72 w-72 grid-cols-2 overflow-auto rounded border border-base-300 bg-base-200 p-2 shadow">
                  <label className="label col-span-2 cursor-pointer justify-start gap-2 border-b border-base-300 pb-2 text-xs">
                    <input
                      type="checkbox"
                      className="checkbox checkbox-primary checkbox-xs"
                      checked={selectedPatternTypes.includes("all")}
                      onChange={() => togglePatternType("all")}
                    />
                    <span>全部型態</span>
                  </label>
                  {patternTypes.map((type) => (
                    <label key={type.id} className="label cursor-pointer justify-start gap-2 py-1 text-xs">
                      <input
                        type="checkbox"
                        className="checkbox checkbox-primary checkbox-xs"
                        checked={!selectedPatternTypes.includes("all") && selectedPatternTypes.includes(type.id)}
                        onChange={() => togglePatternType(type.id)}
                      />
                      <span className="truncate">{type.name}</span>
                    </label>
                  ))}
                </div>
              </details>
            </div>
            {patternError ? (
              <div className="p-4 text-center text-sm text-error">{patternError}</div>
            ) : patternLoading ? (
              <div className="p-6 text-center text-sm text-base-content/50">掃描中...</div>
            ) : patternRows.length ? (
              <table className="table table-xs table-pin-rows">
                <thead>
                  <tr>
                    <th>股票</th>
                    <th>型態</th>
                    <th>信心</th>
                    <th>日期</th>
                  </tr>
                </thead>
                <tbody>
                  {patternRows.map((row, idx) => (
                    <tr
                      key={`${row.stock_id}-${row.pattern_type}-${idx}`}
                      data-panel="pattern"
                      data-row-index={idx}
                      className={String(stockId) === String(row.stock_id) ? "bg-primary/15" : ""}
                      onClick={() => selectPatternRow(row)}
                    >
                      <StockCell row={row} />
                      <td>
                        <div className="max-w-[96px] truncate text-xs">{row.pattern_name || row.pattern_type}</div>
                        <div className="text-[10px] text-base-content/45">{row.sub_type || ""}</div>
                      </td>
                      <td>{row.score}</td>
                      <td className="text-base-content/50">{row.date || "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="p-6 text-center text-sm text-base-content/50">無符合條件的股票</div>
            )}
          </Panel>

          <Panel
            title="VWAP突破"
            count={vwapRows.length}
            actions={vwapLoading ? <span className="loading loading-spinner loading-xs text-primary" /> : null}
            width={470}
            focused={focusedPanel === "vwap"}
            onFocusPanel={() => setFocusedPanel("vwap")}
          >
            <div className="flex flex-wrap gap-1 border-b border-base-300 bg-base-200 p-2">
              <input
                type="date"
                className="input input-bordered input-xs w-32 rounded"
                value={vwapDate}
                title="留空＝今日補齊後繼續即時；選過去日期則凍結該日"
                onChange={(e) => setVwapDate(e.target.value)}
              />
              <input
                className="input input-bordered input-xs w-20 rounded uppercase"
                placeholder="代號"
                value={vwapSearch}
                onChange={(e) => setVwapSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && vwapRows[0]) selectStock(vwapRows[0].stock_id, eventKey(vwapRows[0]), "vwap");
                }}
              />
              <select className="select select-bordered select-xs rounded" value={universe} onChange={(e) => setUniverse(e.target.value)}>
                <option value="daytrade">當沖 {universeSets.daytrade.size ? `(${universeSets.daytrade.size})` : ""}</option>
                <option value="full">全市場 {universeSets.full.size ? `(${universeSets.full.size})` : ""}</option>
              </select>
              <button className="btn btn-xs rounded" onClick={replayVwap}>
                重現
              </button>
              <button className={`btn btn-xs rounded ${repeatEvents ? "btn-primary" : ""}`} onClick={() => setRepeatEvents((v) => !v)}>
                重複
              </button>
              <button className={`btn btn-xs rounded ${srOnly ? "btn-primary" : ""}`} onClick={() => setSrOnly((v) => !v)}>
                SR
              </button>
              <button className={`btn btn-xs rounded ${macdOnly ? "btn-primary" : ""}`} onClick={() => setMacdOnly((v) => !v)}>
                MACD
              </button>
              <button className={`btn btn-xs rounded ${obvOnly ? "btn-primary" : ""}`} onClick={() => setObvOnly((v) => !v)}>
                OBV
              </button>
              <button className={`btn btn-xs rounded ${showAllCandidates ? "btn-primary" : ""}`} onClick={toggleShowAllCandidates}>
                全部候選股
              </button>
              {Object.entries(ACTIVITY_FILTERS).map(([key, config]) => (
                <select
                  key={key}
                  className="select select-bordered select-xs rounded"
                  value={activityFilters[key]}
                  onChange={(e) => setActivityFilter(key, e.target.value)}
                >
                  <option value="">{config.label}</option>
                  {config.options.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              ))}
            </div>
            {vwapError ? (
              <div className="p-4 text-center text-sm text-error">{vwapError}</div>
            ) : vwapRows.length ? (
              <table className="table table-xs table-pin-rows">
                <thead>
                  <tr>
                    <th className="cursor-pointer" onClick={() => sortVwap("stock_id")}>
                      股票
                    </th>
                    <th className="cursor-pointer" onClick={() => sortVwap("chg_pct")}>
                      漲幅
                    </th>
                    <th className="cursor-pointer text-center" onClick={() => sortVwap("sr_on")}>
                      SR
                    </th>
                    <th className="cursor-pointer text-center" onClick={() => sortVwap("macd_on")}>
                      MACD
                    </th>
                    <th className="cursor-pointer text-center" onClick={() => sortVwap("obv_on")}>
                      OBV
                    </th>
                    <th className="cursor-pointer text-right" onClick={() => sortVwap("time")}>
                      時間
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {vwapRows.map((row, idx) => {
                    const key = eventKey(row);
                    const selected = selectedEventKey ? key === selectedEventKey : String(stockId) === String(row.stock_id);
                    return (
                      <tr
                        key={`${key}-${idx}`}
                        data-panel="vwap"
                        data-row-index={idx}
                        className={selected ? "bg-primary/15" : ""}
                        onClick={() => selectStock(row.stock_id, key, "vwap")}
                        onDoubleClick={() => toggleObsStock(row.stock_id)}
                      >
                        <StockCell row={row}>
                          {row.direction ? (
                            <div className={`mt-1 text-[10px] ${row.direction === "up" ? "text-error" : "text-success"}`}>
                              {row.direction === "up" ? "突破" : "跌破"} @ {Number(row.price).toFixed(2)} (VWAP {Number(row.vwap).toFixed(2)})
                            </div>
                          ) : null}
                        </StockCell>
                        <td className={row.chg_pct == null ? "text-base-content/35" : row.chg_pct >= 0 ? "text-error" : "text-success"}>
                          {row.chg_pct == null ? "" : `${row.chg_pct >= 0 ? "+" : ""}${Number(row.chg_pct).toFixed(2)}%`}
                        </td>
                        <td className="text-center">
                          <Lamp on={row.sr_on} kind="both" title="SR" />
                        </td>
                        <td className="text-center">
                          <Lamp on={row.macd_on} kind={row.macd_kind} title="MACD" />
                        </td>
                        <td className="text-center">
                          <Lamp on={row.obv_on} kind={row.obv_kind} title="OBV" />
                        </td>
                        <td className="text-right text-base-content/50">{hm(row.time)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : (
              <div className="p-6 text-center text-sm text-base-content/50">該日尚無 VWAP / SR 訊號</div>
            )}
          </Panel>

          <Panel
            title="觀察"
            count={obsRows.length}
            width={370}
            focused={focusedPanel === "obs"}
            onFocusPanel={() => setFocusedPanel("obs")}
          >
            {obsRows.length ? (
              <table className="table table-xs table-pin-rows">
                <thead>
                  <tr>
                    <th>股票</th>
                    <th>漲幅</th>
                    <th className="text-center">SR</th>
                    <th className="text-center">MACD</th>
                    <th className="text-center">OBV</th>
                    <th className="text-right">時間</th>
                  </tr>
                </thead>
                <tbody>
                  {obsRows.map((row, idx) => {
                    const key = eventKey(row);
                    const selected = selectedEventKey ? key === selectedEventKey : String(stockId) === String(row.stock_id);
                    return (
                      <tr
                        key={row.stock_id}
                        data-panel="obs"
                        data-row-index={idx}
                        className={selected ? "bg-primary/15" : ""}
                        onClick={() => selectStock(row.stock_id, key, "obs")}
                        onDoubleClick={() => toggleObsStock(row.stock_id)}
                      >
                        <StockCell row={row} />
                        <td className={row.chg_pct == null ? "text-base-content/35" : row.chg_pct >= 0 ? "text-error" : "text-success"}>
                          {row.chg_pct == null ? "" : `${row.chg_pct >= 0 ? "+" : ""}${Number(row.chg_pct).toFixed(2)}%`}
                        </td>
                        <td className="text-center">
                          <Lamp on={row.sr_on} kind="both" title="SR" />
                        </td>
                        <td className="text-center">
                          <Lamp on={row.macd_on} kind={row.macd_kind} title="MACD" />
                        </td>
                        <td className="text-center">
                          <Lamp on={row.obv_on} kind={row.obv_kind} title="OBV" />
                        </td>
                        <td className="text-right text-base-content/50">{hm(row.time)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : (
              <div className="p-6 text-center text-sm text-base-content/50">在 VWAP 突破框雙擊股票加入觀察</div>
            )}
          </Panel>
        </div>

        <div className="grid min-h-0 gap-2 lg:grid-cols-[minmax(360px,42%)_minmax(0,1fr)]">
          <ChartPanel
            title={`${titleStock} 日K${activeChartDate ? `・${activeChartDate.slice(5).replace("-", "/")}` : ""}`}
            loading={loadingCharts}
            error={dayError}
            actions={<PriceChange summary={daySummary} />}
          >
            <TradingViewChart data={dayData} variant="day" emptyMessage="尚無日K資料" showVolume={false} />
          </ChartPanel>

          <ChartPanel
            title={rightChartTitle}
            loading={loadingCharts}
            error={intradayError}
            actions={
              <>
                <ChartIndicatorControls value={chartIndicatorMode} onChange={setChartIndicatorMode} />
                <PriceChange summary={rightSummary} />
              </>
            }
          >
            <div className="h-full min-h-0">
              <TradingViewChart
                data={intradayData}
                dayLevels={dayData}
                extraSr={extraSr}
                idxData={idxData}
                variant={rightChartVariant}
                timeframe={activeChartTimeframe}
                emptyMessage={`尚無${activeChartLabel}資料`}
                showVolumeProfile
                showIndicatorPane={showIndicatorPane}
                daySrMode={isPatternChart ? "segments" : "horizontal"}
                indicatorMode={chartIndicatorMode}
                indicatorStockId={stockId}
                indicatorEventTime={indicatorEventTime}
                macdMap={macdMap}
                obvMap={obvMap}
              />
            </div>
          </ChartPanel>
        </div>
      </main>
    </div>
  );
}
