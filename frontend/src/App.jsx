import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiUrl, fetchJson, patternDetailPath } from "./api.js";
import { formatTaipeiClock, previousTaipeiWeekdayIso, taipeiTodayIso } from "./date.js";
import { TIMEFRAME_LABEL, priceSummary } from "./chartData.js";
import TradingViewChart, { indicatorLabel } from "./TradingViewChart.jsx";

const DEFAULT_STOCK = "0050";
const PRODUCT_NAME = "盤勢雷達";
const APP_VERSION = import.meta.env.VITE_APP_VERSION || "local";
const SIGNAL_LABEL = "盤中訊號";
const BASELINE_LABEL = "基準線";
const PATTERN_TIMEFRAME = "day";
const PATTERN_TIMEFRAME_LABEL = "D1";
const PATTERN_TYPE_META = {
  w_bottom: { side: "bull", rank: 0 },
  head_shoulders_bottom: { side: "bull", rank: 1 },
  abcd_bull: { side: "bull", rank: 10 },
  cup_handle: { side: "bull", rank: 20 },
  breakout_retest: { side: "bull", rank: 30 },
  macd_hist_bull: { side: "bull", rank: 40 },
  triangle: { side: "bull", rank: 50 },
  m_top: { side: "bear", rank: 0 },
  head_shoulders_top: { side: "bear", rank: 1 },
  abcd_bear: { side: "bear", rank: 10 },
  breakdown_retest: { side: "bear", rank: 20 },
  macd_hist_bear: { side: "bear", rank: 30 },
};
const PATTERN_SIDE_ORDER = { bull: 0, bear: 1 };
const PATTERN_GROUPS = [
  { side: "bull", label: "多方" },
  { side: "bear", label: "空方" },
];
const PATTERN_SIDE_STYLE = {
  bull: {
    group: "border-error/40 bg-error/5",
    label: "text-error",
    head: "bg-error/10 text-error",
    cell: "bg-error/5",
    buttonOn: "border-error bg-error/20 text-error hover:bg-error/25",
    buttonOff: "border-error/25 bg-base-100/40 text-base-content/80 hover:border-error/50 hover:bg-error/10",
  },
  bear: {
    group: "border-success/40 bg-success/5",
    label: "text-success",
    head: "bg-success/10 text-success",
    cell: "bg-success/5",
    buttonOn: "border-success bg-success/20 text-success hover:bg-success/25",
    buttonOff: "border-success/25 bg-base-100/40 text-base-content/80 hover:border-success/50 hover:bg-success/10",
  },
};
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
const ACTIVITY_DEFAULT_VERSION = "2";
const ACTIVITY_DEFAULT_VERSION_KEY = "vwapActivityDefaultVersion";
const ACTIVITY_DEFAULTS = {
  day_atr: "0.05",
  open5_rng: "",
  vol5_pr: "0.5",
};
const VWAP_FILTER_DEFAULT_VERSION = "4";
const VWAP_FILTER_DEFAULT_VERSION_KEY = "vwapFilterDefaultVersion";
const CHART_INDICATOR_DEFAULT_VERSION = "2";
const CHART_INDICATOR_DEFAULT_VERSION_KEY = "chartIndicatorDefaultVersion";

function patternTypeId(type) {
  return String(type?.id || type?.pattern_type || type || "");
}

function patternTypeName(type) {
  return type?.name || type?.pattern_name || type?.pattern_type || type?.id || "型態";
}

function patternSide(type) {
  const key = patternTypeId(type);
  const meta = PATTERN_TYPE_META[key];
  if (meta) return meta.side;
  if (key.includes("bear") || key.includes("top") || key.includes("breakdown")) return "bear";
  return "bull";
}

function patternRank(type) {
  return PATTERN_TYPE_META[patternTypeId(type)]?.rank ?? 100;
}

function sortPatternTypes(types) {
  return [...types].sort((a, b) => {
    const sideCmp = PATTERN_SIDE_ORDER[patternSide(a)] - PATTERN_SIDE_ORDER[patternSide(b)];
    if (sideCmp) return sideCmp;
    const rankCmp = patternRank(a) - patternRank(b);
    if (rankCmp) return rankCmp;
    return patternTypeName(a).localeCompare(patternTypeName(b), "zh-Hant", { numeric: true });
  });
}

function patternSideStyle(typeOrSide) {
  const side = typeOrSide === "bear" || typeOrSide === "bull" ? typeOrSide : patternSide(typeOrSide);
  return PATTERN_SIDE_STYLE[side] || PATTERN_SIDE_STYLE.bull;
}

function patternButtonClass(type, selected) {
  const style = patternSideStyle(type);
  return `btn btn-xs shrink-0 rounded border ${selected ? style.buttonOn : style.buttonOff}`;
}

function StatusBadge({ status }) {
  if (status === "connected") return <span className="badge badge-success badge-sm">已連線</span>;
  if (status === "error") return <span className="badge badge-error badge-sm">中斷</span>;
  return <span className="badge badge-warning badge-sm">連線中</span>;
}

function HealthLine({ health, clock, version }) {
  return (
    <div className="hidden items-center gap-3 text-xs text-base-content/60 md:flex">
      <span>連線數：{health?.ws_clients ?? health?.sse_clients ?? "-"}</span>
      <span>更新：{clock}</span>
      <span>版本：{version}</span>
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

function initialPatternFilters() {
  try {
    const saved = JSON.parse(sessionStorage.getItem("vwapPatternFilters") || "null");
    if (Array.isArray(saved)) return saved.filter(Boolean);
  } catch {
    // Ignore old or malformed session values.
  }
  return [];
}

function initialActivityFilters() {
  const seedDefaults = sessionStorage.getItem(ACTIVITY_DEFAULT_VERSION_KEY) !== ACTIVITY_DEFAULT_VERSION;
  return Object.fromEntries(
    Object.entries(ACTIVITY_STORAGE_KEYS).map(([key, storageKey]) => {
      const saved = sessionStorage.getItem(storageKey);
      return [key, seedDefaults ? ACTIVITY_DEFAULTS[key] || "" : saved != null ? saved : ACTIVITY_DEFAULTS[key] || ""];
    }),
  );
}

function initialChartIndicatorMode() {
  if (sessionStorage.getItem(CHART_INDICATOR_DEFAULT_VERSION_KEY) !== CHART_INDICATOR_DEFAULT_VERSION) return "idx";
  const saved = sessionStorage.getItem("chartIndicatorMode");
  return ["macd", "obv", "idx"].includes(saved) ? saved : "idx";
}

function initialSessionFlag(storageKey, defaultValue) {
  const seedDefaults = sessionStorage.getItem(VWAP_FILTER_DEFAULT_VERSION_KEY) !== VWAP_FILTER_DEFAULT_VERSION;
  if (seedDefaults) return defaultValue;
  const saved = sessionStorage.getItem(storageKey);
  return saved == null ? defaultValue : saved === "1";
}

function Panel({ title, count, children, actions, className = "", bodyClassName = "overflow-auto", width, focused = false, onFocusPanel }) {
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
      <div className={`min-h-0 flex-1 ${bodyClassName}`}>{children}</div>
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

function isoDate(value) {
  if (!value) return "";
  return String(value).slice(0, 10);
}

function displayDate(value) {
  return isoDate(value).replaceAll("-", "/");
}

function monthKey(value) {
  const date = isoDate(value);
  return /^\d{4}-\d{2}-\d{2}$/.test(date) ? date.slice(0, 7) : "";
}

function shiftMonth(month, delta) {
  const match = /^(\d{4})-(\d{2})$/.exec(month || "");
  if (!match) return monthKey(taipeiTodayIso());
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1 + delta, 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

function clampMonth(month, minMonth, maxMonth) {
  if (minMonth && month < minMonth) return minMonth;
  if (maxMonth && month > maxMonth) return maxMonth;
  return month;
}

function calendarCells(month) {
  const match = /^(\d{4})-(\d{2})$/.exec(month || "");
  if (!match) return [];
  const year = Number(match[1]);
  const monthIndex = Number(match[2]) - 1;
  const firstWeekday = new Date(Date.UTC(year, monthIndex, 1)).getUTCDay();
  const daysInMonth = new Date(Date.UTC(year, monthIndex + 1, 0)).getUTCDate();
  const cellCount = Math.ceil((firstWeekday + daysInMonth) / 7) * 7;
  return Array.from({ length: cellCount }, (_, index) => {
    const day = index - firstWeekday + 1;
    if (day < 1 || day > daysInMonth) return null;
    return {
      date: `${year}-${String(monthIndex + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`,
      day,
    };
  });
}

function daysBetweenIso(fromDate, toDate) {
  const from = isoDate(fromDate);
  const to = isoDate(toDate);
  if (!from || !to) return null;
  const fromMs = Date.parse(`${from}T00:00:00+08:00`);
  const toMs = Date.parse(`${to}T00:00:00+08:00`);
  if (!Number.isFinite(fromMs) || !Number.isFinite(toMs)) return null;
  return Math.max(0, Math.round((toMs - fromMs) / 86400000));
}

function latestObjectDate(items, keys) {
  let latest = "";
  for (const item of items || []) {
    for (const key of keys) {
      const value = isoDate(item?.[key]);
      if (value && value > latest) latest = value;
    }
  }
  return latest;
}

function patternEventDate(row) {
  return isoDate(
    row?.event_date ||
      row?.details?.event_date ||
      row?.details?.break_date ||
      row?.details?.trigger_date ||
      row?.details?.completion_date ||
      row?.details?.h2_date ||
      row?.details?.right_shoulder_date ||
      latestObjectDate(row?.lines, ["end_date", "start_date"]) ||
      latestObjectDate(row?.pivots, ["date"]) ||
      row?.date,
  );
}

function patternSignalKind(patternType) {
  const key = String(patternType || "");
  if (key === "triangle") return "both";
  return patternSide(key) === "bear" ? "bear" : "bull";
}

function patternDisplayName(row) {
  return row?.pattern_name || row?.pattern_type || "型態";
}

function bestPatternHit(hits, preferredTypes = []) {
  const hitList = preferredTypes.length ? preferredTypes.map((type) => hits?.get(type)).filter(Boolean) : [...(hits?.values() || [])];
  return hitList.sort((a, b) => {
    const dateCmp = String(b.event_date || "").localeCompare(String(a.event_date || ""));
    if (dateCmp) return dateCmp;
    return Number(b.score || 0) - Number(a.score || 0);
  })[0] || null;
}

function ageLabel(days) {
  if (days == null) return "";
  if (days === 0) return "今日";
  return `${days}天`;
}

function ageClass(days) {
  if (days == null) return "text-base-content/35";
  if (days <= 5) return "text-error";
  if (days <= 20) return "text-warning";
  return "text-base-content/55";
}

function PatternSignalCell({ hit, label, onSelect, type }) {
  const active = Boolean(hit);
  const age = hit?.age_days ?? null;
  const style = patternSideStyle(type || hit?.pattern_type);
  return (
    <td
      className={`min-w-16 border-l border-base-300/40 text-center ${style.cell} ${active ? "cursor-pointer" : ""} ${ageClass(age)}`}
      onClick={
        active && onSelect
          ? (event) => {
              event.stopPropagation();
              onSelect(hit);
            }
          : undefined
      }
    >
      <div className="flex flex-col items-center leading-none">
        <div className="flex items-center justify-center">
          <Lamp
            on={active}
            kind={hit?.signal_kind}
            title={
              active
                ? `${hit.pattern_name || label || "型態"} ${hit.event_date || ""} ${ageLabel(age)}`
                : label || "型態"
            }
          />
        </div>
        {active ? <span className="mt-1 text-[10px] font-semibold">{ageLabel(age)}</span> : null}
      </div>
    </td>
  );
}

function rowSortValue(row, key) {
  if (String(key).startsWith("pattern:")) return row.pattern_hits?.get(String(key).slice(8))?.age_days;
  return row[key];
}

function sortRows(rows, key, dir) {
  return [...rows].sort((a, b) => {
    let va = rowSortValue(a, key);
    let vb = rowSortValue(b, key);
    if (va == null) va = key === "time" || key === "stock_id" ? "" : dir > 0 ? Infinity : -Infinity;
    if (vb == null) vb = key === "time" || key === "stock_id" ? "" : dir > 0 ? Infinity : -Infinity;
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

function DateCalendarPicker({ value, dates, today, onChange }) {
  const sortedDates = useMemo(() => [...new Set(dates || [])].filter(Boolean).sort(), [dates]);
  const dateSet = useMemo(() => new Set(sortedDates), [sortedDates]);
  const minMonth = monthKey(sortedDates[0]);
  const maxMonth = monthKey(sortedDates[sortedDates.length - 1]);
  const fallbackMonth = monthKey(value) || maxMonth || monthKey(today);
  const [open, setOpen] = useState(false);
  const [viewMonth, setViewMonth] = useState(fallbackMonth);
  const [popupStyle, setPopupStyle] = useState({ left: 0, top: 0 });
  const rootRef = useRef(null);
  const popupRef = useRef(null);

  useEffect(() => {
    const nextMonth = clampMonth(monthKey(value) || maxMonth || monthKey(today), minMonth, maxMonth);
    setViewMonth(nextMonth);
  }, [maxMonth, minMonth, today, value]);

  useEffect(() => {
    if (!open) return undefined;

    function updatePopupPosition() {
      const rect = rootRef.current?.getBoundingClientRect();
      if (!rect) return;
      const width = 256;
      const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
      setPopupStyle({ left, top: rect.bottom + 4 });
    }

    function closeOnOutsidePointer(event) {
      if (!rootRef.current?.contains(event.target) && !popupRef.current?.contains(event.target)) setOpen(false);
    }

    function closeOnEscape(event) {
      if (event.key === "Escape") setOpen(false);
    }

    updatePopupPosition();
    window.addEventListener("resize", updatePopupPosition);
    window.addEventListener("scroll", updatePopupPosition, true);
    document.addEventListener("pointerdown", closeOnOutsidePointer, true);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("resize", updatePopupPosition);
      window.removeEventListener("scroll", updatePopupPosition, true);
      document.removeEventListener("pointerdown", closeOnOutsidePointer, true);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  const cells = useMemo(() => calendarCells(viewMonth), [viewMonth]);
  const canPrev = Boolean(minMonth) && viewMonth > minMonth;
  const canNext = Boolean(maxMonth) && viewMonth < maxMonth;

  const calendarPopup =
    open && typeof document !== "undefined"
      ? createPortal(
          <div
            ref={popupRef}
            className="fixed z-[1000] w-64 rounded border border-base-300 bg-base-200 p-3 shadow"
            style={popupStyle}
          >
            <div className="mb-2 flex items-center justify-between gap-2">
              <button
                type="button"
                className={`btn btn-xs rounded ${value ? "" : "btn-primary"}`}
                onClick={() => {
                  onChange("");
                  setOpen(false);
                }}
              >
                即時/今日
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-square btn-xs rounded"
                title="關閉"
                aria-label="關閉"
                onClick={() => setOpen(false)}
              >
                ×
              </button>
            </div>
            <div className="mb-2 flex items-center justify-between">
              <button
                type="button"
                className="btn btn-square btn-xs rounded"
                disabled={!canPrev}
                aria-label="上一個月"
                onClick={() => setViewMonth((month) => clampMonth(shiftMonth(month, -1), minMonth, maxMonth))}
              >
                ‹
              </button>
              <div className="text-sm font-semibold text-base-content">{viewMonth?.replace("-", "/")}</div>
              <button
                type="button"
                className="btn btn-square btn-xs rounded"
                disabled={!canNext}
                aria-label="下一個月"
                onClick={() => setViewMonth((month) => clampMonth(shiftMonth(month, 1), minMonth, maxMonth))}
              >
                ›
              </button>
            </div>
            <div className="grid grid-cols-7 gap-1 text-center text-[10px] font-semibold text-base-content/45">
              {["日", "一", "二", "三", "四", "五", "六"].map((label) => (
                <div key={label}>{label}</div>
              ))}
            </div>
            <div className="mt-1 grid grid-cols-7 gap-1">
              {cells.map((cell, index) => {
                if (!cell) return <div key={`blank-${index}`} className="h-7" />;
                const enabled = dateSet.has(cell.date);
                const selected = value === cell.date;
                return (
                  <button
                    key={cell.date}
                    type="button"
                    className={`btn btn-square btn-xs h-7 min-h-7 w-7 rounded text-xs ${
                      selected ? "btn-primary" : enabled ? "bg-base-100" : "btn-disabled bg-base-300/30 text-base-content/25"
                    }`}
                    disabled={!enabled}
                    title={enabled ? displayDate(cell.date) : "此日無資料"}
                    onClick={() => {
                      onChange(cell.date);
                      setOpen(false);
                    }}
                  >
                    {cell.day}
                  </button>
                );
              })}
            </div>
            {!sortedDates.length ? <div className="mt-2 text-center text-xs text-warning">尚未載入日期資料</div> : null}
          </div>,
          document.body,
        )
      : null;

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        className="btn btn-xs w-36 justify-between rounded border-base-300 bg-base-100"
        title="選擇已有資料的日期"
        aria-label="選擇日期"
        onClick={() => setOpen((current) => !current)}
      >
        <span>{value ? displayDate(value) : "即時/今日"}</span>
        <span className="text-[10px] text-base-content/45">日曆</span>
      </button>
      {calendarPopup}
    </div>
  );
}

export default function App() {
  const [stockId, setStockId] = useState(() => localStorage.getItem("chartStock") || DEFAULT_STOCK);
  const [selectedEventKey, setSelectedEventKey] = useState("");
  const [chartContext, setChartContext] = useState({ kind: "manual" });
  const [chartIndicatorMode, setChartIndicatorMode] = useState(initialChartIndicatorMode);
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
  const [selectedPatternTypes, setSelectedPatternTypes] = useState(initialPatternFilters);
  const [patternLimit, setPatternLimit] = useState(120);
  const [patternRows, setPatternRows] = useState([]);
  const [patternScanDates, setPatternScanDates] = useState([]);
  const [patternLoading, setPatternLoading] = useState(false);
  const [patternError, setPatternError] = useState("");
  const [vwapMenuOpen, setVwapMenuOpen] = useState(false);
  const [patternMenuOpen, setPatternMenuOpen] = useState(false);
  const vwapMenuRef = useRef(null);
  const patternMenuRef = useRef(null);
  const watchDrawerRef = useRef(null);

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
  const [repeatEvents, setRepeatEvents] = useState(false);
  const [showAllCandidates, setShowAllCandidates] = useState(() => initialSessionFlag("vwapShowAll", false));
  const [srOnly, setSrOnly] = useState(() => initialSessionFlag("vwapSrFilter", true));
  const [macdOnly, setMacdOnly] = useState(() => initialSessionFlag("vwapMacdFilter", false));
  const [obvOnly, setObvOnly] = useState(() => initialSessionFlag("vwapObvFilter", false));
  const [activityFilters, setActivityFilters] = useState(initialActivityFilters);
  const [vwapSort, setVwapSort] = useState({ key: "time", dir: -1 });
  const [obsStocks, setObsStocks] = useState(() => new Set(JSON.parse(localStorage.getItem("obsStocks") || "[]")));
  const [watchDrawerOpen, setWatchDrawerOpen] = useState(false);
  const [focusedPanel, setFocusedPanel] = useState("vwap");
  const [signalLoadedKey, setSignalLoadedKey] = useState("");
  const [selectedChartDate, setSelectedChartDate] = useState(() => vwapDate);

  const stockIdRef = useRef(stockId);
  const activeChartDateRef = useRef("");
  const vwapDateRef = useRef(vwapDate);
  const signalLoadSeqRef = useRef(0);
  const chartLoadSeqRef = useRef(0);
  const idxCacheRef = useRef(new Map());
  const today = useMemo(() => taipeiTodayIso(), [clock]);
  const stockName = dayData?.stock_name || intradayData?.stock_name || "";
  const titleStock = stockName && stockName !== stockId ? `${stockId} ${stockName}` : stockId;
  const daySummary = useMemo(() => priceSummary(dayData?.candles), [dayData]);
  const rightSummary = useMemo(() => priceSummary(intradayData?.candles), [intradayData]);
  const isVwapChart = chartContext.kind === "vwap" || chartContext.kind === "obs";
  const dayChartPatternType = chartContext.patternType || "none";
  const dayChartLimit = patternLimit || 120;
  const activeChartTimeframe = isVwapChart ? "1m" : timeframe;
  const activeChartPatternType = "none";
  const activeChartLimit = 120;
  const activeChartDate = vwapDate;
  const activeChartLabel = TIMEFRAME_LABEL[activeChartTimeframe] || activeChartTimeframe;
  const rightChartVariant = activeChartTimeframe === "day" ? "day" : "intraday";
  const showIndicatorPane = rightChartVariant === "intraday";
  const signalLoadKey = `${vwapDate || "today"}:${universe}:${repeatEvents ? "repeat" : "latest"}`;

  useEffect(() => {
    if (sessionStorage.getItem(VWAP_FILTER_DEFAULT_VERSION_KEY) === VWAP_FILTER_DEFAULT_VERSION) return;
    setRepeatEvents(false);
    setShowAllCandidates(false);
    setSrOnly(true);
    setMacdOnly(false);
    setObvOnly(false);
  }, []);

  useEffect(() => {
    stockIdRef.current = stockId;
    activeChartDateRef.current = activeChartDate;
    vwapDateRef.current = vwapDate;
  }, [stockId, activeChartDate, vwapDate]);

  useEffect(() => {
    if (!vwapMenuOpen && !patternMenuOpen) return undefined;

    function closeOnOutsidePointer(event) {
      if (vwapMenuOpen && !vwapMenuRef.current?.contains(event.target)) {
        setVwapMenuOpen(false);
      }
      if (patternMenuOpen && !patternMenuRef.current?.contains(event.target)) {
        setPatternMenuOpen(false);
      }
    }

    function closeOnEscape(event) {
      if (event.key === "Escape") {
        setVwapMenuOpen(false);
        setPatternMenuOpen(false);
      }
    }

    document.addEventListener("pointerdown", closeOnOutsidePointer, true);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer, true);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [patternMenuOpen, vwapMenuOpen]);

  useEffect(() => {
    if (!watchDrawerOpen) return undefined;
    window.requestAnimationFrame(() => {
      watchDrawerRef.current?.focus({ preventScroll: true });
    });

    function closeOnEscape(event) {
      if (event.key === "Escape") {
        closeWatchDrawer();
      }
    }

    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [watchDrawerOpen]);

  const loadCharts = useCallback(async () => {
    const sid = stockId.trim();
    if (!sid || selectedChartDate !== activeChartDate) return;
    const requestId = chartLoadSeqRef.current + 1;
    chartLoadSeqRef.current = requestId;
    const isCurrent = () => requestId === chartLoadSeqRef.current;
    setLoadingCharts(true);
    setDayError("");
    setIntradayError("");
    const forceLive = !activeChartDate || activeChartDate === today;
    const hasPatternOverlay = dayChartPatternType !== "none";
    const chartForceLive = hasPatternOverlay && activeChartDate ? false : forceLive;

    const dayPromise = fetchJson(
      patternDetailPath(sid, {
        patternType: dayChartPatternType,
        timeframe: "day",
        date: activeChartDate,
        limit: dayChartLimit,
        forceLive: chartForceLive,
      }),
    )
      .then((result) => {
        if (isCurrent()) setDayData(result);
      })
      .catch((error) => {
        if (!isCurrent()) return;
        setDayData(null);
        setDayError(error?.message || "日K載入失敗");
      });

    const intradayPromise = fetchJson(
      patternDetailPath(sid, {
        patternType: activeChartPatternType,
        timeframe: activeChartTimeframe,
        date: activeChartDate,
        limit: activeChartLimit,
        fullDay: rightChartVariant === "intraday",
        forceLive: chartForceLive,
      }),
    )
      .then((result) => {
        if (isCurrent()) setIntradayData(result);
      })
      .catch((error) => {
        if (!isCurrent()) return;
        setIntradayData(null);
        setIntradayError(error?.message || `${activeChartLabel}載入失敗`);
      });

    await Promise.allSettled([dayPromise, intradayPromise]);
    if (isCurrent()) setLoadingCharts(false);
  }, [
    activeChartLabel,
    activeChartLimit,
    activeChartPatternType,
    activeChartTimeframe,
    activeChartDate,
    dayChartLimit,
    dayChartPatternType,
    rightChartVariant,
    selectedChartDate,
    stockId,
    today,
  ]);

  const loadDateIndex = useCallback(async () => {
    if (rightChartVariant !== "intraday" || chartIndicatorMode !== "idx") {
      setIdxData(null);
      return;
    }
    const key = `${activeChartDate || "today"}:${activeChartTimeframe}`;
    const cached = idxCacheRef.current.get(key);
    if (cached) {
      setIdxData(cached);
      return;
    }
    try {
      const result = await fetchJson(
        patternDetailPath(DEFAULT_STOCK, {
          timeframe: activeChartTimeframe,
          date: activeChartDate,
          limit: activeChartLimit,
          fullDay: true,
        }),
      );
      idxCacheRef.current.set(key, result);
      setIdxData(result);
    } catch {
      setIdxData(null);
    }
  }, [activeChartDate, activeChartLimit, activeChartTimeframe, chartIndicatorMode, rightChartVariant]);

  const selectStock = useCallback((sid, key = "", kind = "manual", chartMeta = {}) => {
    const next = String(sid);
    setStockId(next);
    setSelectedEventKey(key);
    setSelectedChartDate(vwapDate);
    setChartContext({ kind, ...chartMeta });
    if (kind === "vwap" || kind === "obs") setFocusedPanel(kind);
  }, [vwapDate]);

  const loadVwapTables = useCallback(async () => {
    const requestId = signalLoadSeqRef.current + 1;
    signalLoadSeqRef.current = requestId;
    const isCurrent = () => requestId === signalLoadSeqRef.current;
    setVwapLoading(true);
    setVwapError("");
    setSignalLoadedKey("");
    try {
      const qs = new URLSearchParams({ universe, repeat: repeatEvents ? "1" : "0" });
      if (vwapDate) qs.set("date", vwapDate);
      const bundle = await fetchJson(`/vwap_signal/bundle?${qs.toString()}`);
      if (!isCurrent()) return;
      setVwapRowsRaw(bundle.vwap || []);
      setSrRowsRaw(bundle.sr || []);
      setChgMap(bundle.chg || {});
      setActivityMap(bundle.activity || {});
      setMacdMap(bundle.macd || {});
      setObvMap(bundle.obv || {});
    } catch (error) {
      if (isCurrent()) setVwapError(error.message || `${SIGNAL_LABEL}載入失敗`);
    } finally {
      if (isCurrent()) {
        setVwapLoading(false);
        setSignalLoadedKey(signalLoadKey);
      }
    }
  }, [repeatEvents, signalLoadKey, universe, vwapDate]);

  useEffect(() => {
    localStorage.setItem("chartStock", stockId);
    localStorage.setItem("vwapDate", vwapDate);
    localStorage.setItem("chartTimeframe", timeframe);
  }, [stockId, vwapDate, timeframe]);

  useEffect(() => {
    localStorage.setItem("vwapUniverse", universe);
  }, [universe]);

  useEffect(() => {
    sessionStorage.setItem("vwapShowAll", showAllCandidates ? "1" : "0");
    sessionStorage.setItem("vwapSrFilter", srOnly ? "1" : "0");
    sessionStorage.setItem("vwapMacdFilter", macdOnly ? "1" : "0");
    sessionStorage.setItem("vwapObvFilter", obvOnly ? "1" : "0");
    sessionStorage.setItem(VWAP_FILTER_DEFAULT_VERSION_KEY, VWAP_FILTER_DEFAULT_VERSION);
  }, [showAllCandidates, srOnly, macdOnly, obvOnly]);

  useEffect(() => {
    sessionStorage.setItem("vwapPatternFilters", JSON.stringify(selectedPatternTypes));
  }, [selectedPatternTypes]);

  useEffect(() => {
    sessionStorage.setItem("chartIndicatorMode", chartIndicatorMode);
    sessionStorage.setItem(CHART_INDICATOR_DEFAULT_VERSION_KEY, CHART_INDICATOR_DEFAULT_VERSION);
  }, [chartIndicatorMode]);

  useEffect(() => {
    for (const [key, storageKey] of Object.entries(ACTIVITY_STORAGE_KEYS)) {
      sessionStorage.setItem(storageKey, activityFilters[key] || "");
    }
    sessionStorage.setItem(ACTIVITY_DEFAULT_VERSION_KEY, ACTIVITY_DEFAULT_VERSION);
  }, [activityFilters]);

  useEffect(() => {
    localStorage.setItem("obsStocks", JSON.stringify([...obsStocks]));
  }, [obsStocks]);

  useEffect(() => {
    loadDateIndex();
  }, [loadDateIndex]);

  useEffect(() => {
    chartLoadSeqRef.current += 1;
    setSelectedEventKey("");
    setDayData(null);
    setIntradayData(null);
    setDayError("");
    setIntradayError("");
    setLoadingCharts(false);
  }, [vwapDate]);

  useEffect(() => {
    if (isVwapChart && signalLoadedKey !== signalLoadKey) return undefined;
    if (selectedChartDate !== activeChartDate) return undefined;
    loadCharts();
    return undefined;
  }, [activeChartDate, isVwapChart, loadCharts, reloadSeq, selectedChartDate, signalLoadedKey, signalLoadKey]);

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
    async function loadPatternScanDates() {
      try {
        const data = await fetchJson("/vwap_signal/dates");
        if (!stopped) setPatternScanDates(data.dates || []);
      } catch {
        if (!stopped) setPatternScanDates([]);
      }
    }
    loadPatternScanDates();
    return () => {
      stopped = true;
    };
  }, []);

  useEffect(() => {
    if (!vwapDate || !patternScanDates.length) return;
    if (!patternScanDates.includes(vwapDate)) {
      setVwapDate(patternScanDates[patternScanDates.length - 1] || "");
    }
  }, [patternScanDates, vwapDate]);

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
    let stopped = false;
    const patternType = "all";
    async function loadPatternScan() {
      setPatternLoading(true);
      setPatternError("");
      setPatternRows([]);
      try {
        const params = new URLSearchParams({
          pattern_type: patternType,
          timeframe: PATTERN_TIMEFRAME,
          limit: String(patternLimit || 120),
        });
        if (vwapDate) params.set("date", vwapDate);
        const data = await fetchJson(`/api/pattern/scan?${params.toString()}`);
        if (stopped) return;
        setPatternRows(data.results || []);
        setPatternLoading(false);
      } catch (error) {
        if (!stopped) {
          setPatternError(error.message || "型態結果載入失敗");
          setPatternLoading(false);
        }
      }
    }
    if (signalLoadedKey !== signalLoadKey) {
      setPatternRows([]);
      setPatternLoading(false);
      setPatternError("");
      return undefined;
    }
    loadPatternScan();
    return () => {
      stopped = true;
    };
  }, [patternLimit, signalLoadedKey, signalLoadKey, vwapDate]);

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

  const patternColumns = useMemo(() => {
    if (patternTypes.length) return sortPatternTypes(patternTypes);
    const seen = new Map();
    for (const row of patternRows) {
      const id = String(row.pattern_type || "");
      if (id && !seen.has(id)) seen.set(id, { id, name: patternDisplayName(row) });
    }
    return sortPatternTypes([...seen.values()]);
  }, [patternRows, patternTypes]);

  const patternFilterGroups = useMemo(
    () =>
      PATTERN_GROUPS.map((group) => ({
        ...group,
        types: patternColumns.filter((type) => patternSide(type) === group.side),
      })).filter((group) => group.types.length),
    [patternColumns],
  );

  const patternsByStock = useMemo(() => {
    const out = new Map();
    for (const row of patternRows) {
      const sid = String(row.stock_id);
      const patternType = String(row.pattern_type || "");
      if (!patternType) continue;
      const eventDate = patternEventDate(row);
      const enriched = {
        ...row,
        event_date: eventDate,
        pattern_name: patternDisplayName(row),
        signal_kind: patternSignalKind(row.pattern_type),
        age_days: daysBetweenIso(eventDate, today),
      };
      let byType = out.get(sid);
      if (!byType) {
        byType = new Map();
        out.set(sid, byType);
      }
      const prev = byType.get(patternType);
      if (
        !prev ||
        (eventDate && eventDate > (prev.event_date || "")) ||
        (eventDate === prev.event_date && Number(row.score || 0) > Number(prev.score || 0))
      ) {
        byType.set(patternType, enriched);
      }
    }
    return out;
  }, [patternRows, today]);

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
      const patternHits = patternsByStock.get(String(row.stock_id)) || new Map();
      const pattern = bestPatternHit(patternHits, selectedPatternTypes);
      return {
        ...row,
        name: row.name || universeSets.names.get(String(row.stock_id)) || "",
        sr_on: firstSrByStock.has(String(row.stock_id)) ? 1 : 0,
        macd_on: macd ? 1 : 0,
        macd_kind: macd?.kind || "",
        obv_on: obv ? 1 : 0,
        obv_kind: obv?.kind || "",
        pattern_on: patternHits.size ? 1 : 0,
        pattern_hits: patternHits,
        pattern_primary: pattern || null,
        pattern_type: pattern?.pattern_type || "",
        pattern_name: pattern?.pattern_name || "",
        pattern_kind: pattern?.signal_kind || "",
        pattern_age_days: pattern?.age_days ?? null,
        pattern_event_date: pattern?.event_date || "",
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
    if (selectedPatternTypes.length) rows = rows.filter((row) => selectedPatternTypes.some((type) => row.pattern_hits?.has(type)));
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
    patternsByStock,
    repeatEvents,
    selectedPatternTypes,
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
        const patternHits = patternsByStock.get(String(row.stock_id)) || new Map();
        const pattern = bestPatternHit(patternHits, selectedPatternTypes);
        return {
          ...row,
          name: row.name || universeSets.names.get(String(row.stock_id)) || "",
          sr_on: firstSrByStock.has(String(row.stock_id)) ? 1 : 0,
          macd_on: macd ? 1 : 0,
          macd_kind: macd?.kind || "",
          obv_on: obv ? 1 : 0,
          obv_kind: obv?.kind || "",
          pattern_on: patternHits.size ? 1 : 0,
          pattern_hits: patternHits,
          pattern_primary: pattern || null,
          pattern_type: pattern?.pattern_type || "",
          pattern_name: pattern?.pattern_name || "",
          pattern_kind: pattern?.signal_kind || "",
          pattern_age_days: pattern?.age_days ?? null,
          pattern_event_date: pattern?.event_date || "",
          chg_pct: chgMap[String(row.stock_id)],
        };
      })
      .sort((a, b) => String(a.stock_id).localeCompare(String(b.stock_id), "zh-Hant", { numeric: true }));
  }, [chgMap, firstSrByStock, macdMap, obvMap, obsStocks, patternsByStock, selectedPatternTypes, universeSets.names, vwapRowsRaw]);

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
  const dayPatternTitle = dayChartPatternType !== "none" && dayData?.pattern_name ? `・${dayData.pattern_name}` : "";
  const srTitle = showIndicatorPane && extraSr ? "・壓力支撐" : "";
  const indicatorTitle = showIndicatorPane && chartIndicatorLabel ? `・${chartIndicatorLabel}` : "";
  const rightChartTitle = `${titleStock} ${activeChartLabel}${srTitle}${indicatorTitle}`;

  const selectMarketRow = useCallback(
    (row, key = "", kind = "vwap", patternType = "", usePrimaryPattern = true) => {
      const pattern = patternType ? row?.pattern_hits?.get(patternType) : usePrimaryPattern ? row?.pattern_primary : null;
      const chartMeta = pattern ? { patternType: pattern.pattern_type, limit: patternLimit || 120 } : {};
      selectStock(row.stock_id, key, kind, chartMeta);
    },
    [patternLimit, selectStock],
  );

  function togglePatternType(id) {
    setSelectedPatternTypes((prev) => {
      return prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
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

  function closeWatchDrawer() {
    setWatchDrawerOpen(false);
    setFocusedPanel("vwap");
  }

  function sortVwap(key) {
    setVwapSort((prev) =>
      prev.key === key
        ? { key, dir: -prev.dir }
        : { key, dir: key === "stock_id" || String(key).startsWith("pattern:") ? 1 : -1 },
    );
  }

  async function recalcTodayVwap() {
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
        fetchJson(`/vwap_activity?${qs.toString()}`),
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
      setVwapError(error.message || "今日重算失敗");
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
      const rows = focusedPanel === "obs" ? obsRows : vwapRows;
      if (!rows.length) return;

      event.preventDefault();

      let index = -1;
      if (selectedEventKey) {
        index = rows.findIndex((row) => eventKey(row) === selectedEventKey);
      } else {
        index = rows.findIndex((row) => String(row.stock_id) === String(stockId));
      }

      const nextIndex =
        index === -1 ? (direction > 0 ? 0 : rows.length - 1) : Math.max(0, Math.min(rows.length - 1, index + direction));
      const next = rows[nextIndex];
      if (!next) return;

      selectMarketRow(next, eventKey(next), focusedPanel === "obs" ? "obs" : "vwap", "", focusedPanel !== "obs");

      window.requestAnimationFrame(() => {
        document.querySelector(`[data-panel="${focusedPanel}"][data-row-index="${nextIndex}"]`)?.scrollIntoView({ block: "nearest" });
      });
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [focusedPanel, obsRows, selectMarketRow, selectedEventKey, stockId, vwapRows]);

  return (
    <div className="flex min-h-dvh flex-col overflow-x-hidden bg-base-100 font-mono text-base-content lg:h-dvh lg:overflow-hidden">
      <header className="border-b border-base-300 bg-base-200">
        <div className="flex min-h-12 flex-wrap items-center justify-between gap-2 px-3 py-2">
          <div className="flex items-center gap-3">
            <div className="text-sm font-bold text-primary">{PRODUCT_NAME}</div>
            <StatusBadge status={connection} />
          </div>
          <div className="flex items-center gap-2">
            <HealthLine health={health} clock={clock} version={APP_VERSION} />
            <button
              type="button"
              className={`btn btn-xs rounded ${watchDrawerOpen ? "btn-primary" : ""}`}
              onClick={() => {
                setWatchDrawerOpen(true);
                setFocusedPanel("obs");
              }}
            >
              觀察 {obsRows.length}
            </button>
          </div>
        </div>
      </header>

      <main className="grid min-h-0 flex-1 grid-rows-[minmax(220px,45%)_auto] gap-2 p-2 lg:grid-rows-[minmax(220px,45%)_minmax(260px,55%)]">
        <div className="grid min-h-0 grid-cols-1 overflow-hidden">
          <Panel
            title={SIGNAL_LABEL}
            count={vwapRows.length}
            actions={vwapLoading || patternLoading ? <span className="loading loading-spinner loading-xs text-primary" /> : null}
            bodyClassName="flex flex-col overflow-hidden"
            focused={focusedPanel === "vwap"}
            onFocusPanel={() => setFocusedPanel("vwap")}
          >
            <div className="relative z-20 shrink-0 border-b border-base-300 bg-base-200">
              <div className="grid w-full min-w-0 grid-cols-[minmax(0,1fr)_1.5rem] items-start gap-1 px-2 py-1">
                <div className="flex min-w-0 w-full gap-1 overflow-x-auto pb-1">
                  <DateCalendarPicker value={vwapDate} dates={patternScanDates} today={today} onChange={setVwapDate} />
                  <input
                    className="input input-bordered input-xs w-20 shrink-0 rounded uppercase"
                    placeholder="代號"
                    value={vwapSearch}
                    onChange={(e) => setVwapSearch(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && vwapRows[0]) selectMarketRow(vwapRows[0], eventKey(vwapRows[0]), "vwap");
                    }}
                  />
                  <button className="btn btn-xs shrink-0 rounded" onClick={recalcTodayVwap}>
                    今日重算
                  </button>
                  <button className={`btn btn-xs shrink-0 rounded ${srOnly ? "btn-primary" : ""}`} onClick={() => setSrOnly((v) => !v)}>
                    SR
                  </button>
                  <button className={`btn btn-xs shrink-0 rounded ${macdOnly ? "btn-primary" : ""}`} onClick={() => setMacdOnly((v) => !v)}>
                    MACD
                  </button>
                  <button className={`btn btn-xs shrink-0 rounded ${obvOnly ? "btn-primary" : ""}`} onClick={() => setObvOnly((v) => !v)}>
                    OBV
                  </button>
                </div>
                <details ref={vwapMenuRef} className="dropdown dropdown-end relative z-30 w-6 justify-self-end" open={vwapMenuOpen}>
                  <summary className="btn btn-square btn-xs rounded" title={`${SIGNAL_LABEL}條件`} aria-label={`${SIGNAL_LABEL}條件`} onClick={(event) => { event.preventDefault(); setVwapMenuOpen((open) => !open); }}>☰</summary>
                  <div className="dropdown-content z-50 mt-1 max-h-44 w-60 overflow-y-auto rounded border border-base-300 bg-base-200 p-3 shadow">
                    <div className="mb-2 flex items-center justify-between border-b border-base-300 pb-2">
                      <span className="text-xs font-semibold text-base-content/70">{SIGNAL_LABEL}條件</span>
                      <button type="button" className="btn btn-ghost btn-square btn-xs rounded" title="關閉" aria-label="關閉" onClick={() => setVwapMenuOpen(false)}>×</button>
                    </div>
                    <label className="form-control mb-2 w-full">
                      <div className="label py-1"><span className="label-text text-xs">股票清單</span></div>
                      <select className="select select-bordered select-xs rounded" value={universe} onChange={(e) => setUniverse(e.target.value)}>
                        <option value="daytrade">當沖 {universeSets.daytrade.size ? `(${universeSets.daytrade.size})` : ""}</option>
                        <option value="full">全市場 {universeSets.full.size ? `(${universeSets.full.size})` : ""}</option>
                      </select>
                    </label>
                    <label className="label cursor-pointer justify-start gap-2 py-1 text-xs"><input type="checkbox" className="checkbox checkbox-primary checkbox-xs" checked={repeatEvents} onChange={() => setRepeatEvents((v) => !v)} /><span>重複事件</span></label>
                    <label className="label cursor-pointer justify-start gap-2 py-1 text-xs"><input type="checkbox" className="checkbox checkbox-primary checkbox-xs" checked={showAllCandidates} onChange={toggleShowAllCandidates} /><span>全部候選股</span></label>
                    {Object.entries(ACTIVITY_FILTERS).map(([key, config]) => (
                      <label key={key} className="form-control mt-2 w-full">
                        <div className="label py-1"><span className="label-text text-xs">{config.label}</span></div>
                        <select className="select select-bordered select-xs rounded" value={activityFilters[key]} onChange={(e) => setActivityFilter(key, e.target.value)}>
                          <option value="">不限</option>
                          {config.options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                        </select>
                      </label>
                    ))}
                  </div>
                </details>
              </div>
              <div className="grid w-full min-w-0 grid-cols-[minmax(0,1fr)_1.5rem] items-start gap-1 border-t border-base-300/70 px-2 py-1">
                <div className="flex min-w-0 w-full items-start gap-1 overflow-hidden">
                  <div className="shrink-0 pt-1 text-[11px] font-semibold text-base-content/60">D1型態</div>
                  <div className="flex min-w-0 flex-1 gap-2 overflow-x-auto pb-1">
                    {patternFilterGroups.map((group) => {
                      const style = patternSideStyle(group.side);
                      return <div key={group.side} className={`flex shrink-0 items-center gap-1 rounded border px-1 py-0.5 ${style.group}`}>
                        <span className={`shrink-0 px-1 text-[10px] font-semibold ${style.label}`}>{group.label}</span>
                        {group.types.map((type) => <button key={type.id} className={patternButtonClass(type, selectedPatternTypes.includes(type.id))} title={selectedPatternTypes.includes(type.id) ? `取消${type.name}過濾` : `只看有${type.name}的股票`} onClick={() => togglePatternType(type.id)}>{type.name}</button>)}
                      </div>;
                    })}
                  </div>
                </div>
                <details ref={patternMenuRef} className="dropdown dropdown-end relative z-30 w-6 justify-self-end" open={patternMenuOpen}>
                  <summary className="btn btn-square btn-xs rounded" title="D1型態設定" aria-label="D1型態設定" onClick={(event) => { event.preventDefault(); setPatternMenuOpen((open) => !open); }}>☰</summary>
                  <div className="dropdown-content z-50 mt-1 max-h-36 w-56 overflow-y-auto rounded border border-base-300 bg-base-200 p-3 shadow">
                    <div className="mb-2 flex items-center justify-between border-b border-base-300 pb-2"><span className="text-xs font-semibold text-base-content/70">D1型態</span><button type="button" className="btn btn-ghost btn-square btn-xs rounded" title="關閉" aria-label="關閉" onClick={() => setPatternMenuOpen(false)}>×</button></div>
                    <button type="button" className="btn btn-xs mb-2 w-full rounded" onClick={() => setSelectedPatternTypes([])}>清除型態過濾</button>
                    <label className="form-control w-full"><div className="label py-1"><span className="label-text text-xs">週期</span></div><select className="select select-bordered select-xs rounded" value={PATTERN_TIMEFRAME} disabled><option value={PATTERN_TIMEFRAME}>{PATTERN_TIMEFRAME_LABEL}</option></select></label>
                    <label className="form-control mt-2 w-full"><div className="label py-1"><span className="label-text text-xs">日K根數</span></div><input type="number" className="input input-bordered input-xs rounded" value={patternLimit} min="20" max="500" step="10" onChange={(e) => setPatternLimit(Number(e.target.value) || 120)} /></label>
                  </div>
                </details>
              </div>
            </div>
            {patternError ? <div className="border-b border-base-300 px-3 py-1 text-xs text-warning">型態：{patternError}</div> : null}
            {vwapError ? <div className="flex flex-1 items-center justify-center p-4 text-center text-sm text-error">{vwapError}</div> : vwapRows.length ? (
              <div className="min-h-0 flex-1 overflow-auto">
                <table className="table table-xs table-pin-rows min-w-max">
                  <thead><tr>
                    <th className="cursor-pointer" onClick={() => sortVwap("stock_id")}>股票</th><th className="cursor-pointer" onClick={() => sortVwap("chg_pct")}>漲幅</th><th className="cursor-pointer text-right" onClick={() => sortVwap("time")}>時間</th><th className="cursor-pointer text-center" onClick={() => sortVwap("sr_on")}>SR</th><th className="cursor-pointer text-center" onClick={() => sortVwap("macd_on")}>MACD</th><th className="cursor-pointer text-center" onClick={() => sortVwap("obv_on")}>OBV</th>
                    {patternColumns.map((type) => { const style = patternSideStyle(type); const checked = selectedPatternTypes.includes(type.id); return <th key={type.id} className={`cursor-pointer border-l border-base-300/40 text-center ${style.head}`} onClick={() => sortVwap(`pattern:${type.id}`)}><div className="flex items-center justify-center gap-1.5 whitespace-nowrap"><span>{type.name}</span><input type="checkbox" className="checkbox checkbox-xs" checked={checked} title={checked ? `取消${type.name}過濾` : `只看有${type.name}的股票`} aria-label={checked ? `取消${type.name}過濾` : `只看有${type.name}的股票`} onClick={(event) => event.stopPropagation()} onChange={(event) => { event.stopPropagation(); togglePatternType(type.id); }} /></div></th>; })}
                  </tr></thead>
                  <tbody>{vwapRows.map((row, idx) => { const key = eventKey(row); const selected = selectedEventKey ? key === selectedEventKey : String(stockId) === String(row.stock_id); return <tr key={`${key}-${idx}`} data-panel="vwap" data-row-index={idx} className={selected ? "bg-primary/15" : ""} onClick={() => selectMarketRow(row, key, "vwap")} onDoubleClick={() => toggleObsStock(row.stock_id)}><StockCell row={row}>{row.direction ? <div className={`mt-1 text-[10px] ${row.direction === "up" ? "text-error" : "text-success"}`}>{row.direction === "up" ? "突破" : "跌破"} @ {Number(row.price).toFixed(2)} ({BASELINE_LABEL} {Number(row.vwap).toFixed(2)})</div> : null}</StockCell><td className={row.chg_pct == null ? "text-base-content/35" : row.chg_pct >= 0 ? "text-error" : "text-success"}>{row.chg_pct == null ? "" : `${row.chg_pct >= 0 ? "+" : ""}${Number(row.chg_pct).toFixed(2)}%`}</td><td className="text-right text-base-content/50">{hm(row.time)}</td><td className="text-center"><Lamp on={row.sr_on} kind="both" title="SR" /></td><td className="text-center"><Lamp on={row.macd_on} kind={row.macd_kind} title="MACD" /></td><td className="text-center"><Lamp on={row.obv_on} kind={row.obv_kind} title="OBV" /></td>{patternColumns.map((type) => <PatternSignalCell key={type.id} type={type} hit={row.pattern_hits?.get(type.id)} label={type.name} onSelect={() => selectMarketRow(row, key, "vwap", type.id)} />)}</tr>; })}</tbody>
                </table>
              </div>
            ) : <div className="flex flex-1 items-center justify-center p-6 text-center text-sm text-base-content/50">該日尚無盤中 / SR 訊號</div>}
          </Panel>
        </div>

        <div className="grid min-h-0 gap-2 lg:grid-cols-[minmax(360px,42%)_minmax(0,1fr)]">
          <div className="min-h-[320px] sm:min-h-[380px] lg:min-h-0">
            <ChartPanel title={`${titleStock} 日K${dayPatternTitle}${activeChartDate ? `・${activeChartDate.slice(5).replace("-", "/")}` : ""}`} loading={loadingCharts} error={dayError} actions={<PriceChange summary={daySummary} />}>
              <TradingViewChart data={dayData} variant="day" emptyMessage="尚無日K資料" showVolume={false} />
            </ChartPanel>
          </div>

          <div className="min-h-[360px] sm:min-h-[440px] lg:min-h-0">
            <ChartPanel title={rightChartTitle} loading={loadingCharts} error={intradayError} actions={<><ChartIndicatorControls value={chartIndicatorMode} onChange={setChartIndicatorMode} /><PriceChange summary={rightSummary} /></>}>
              <div className="h-full min-h-0"><TradingViewChart data={intradayData} dayLevels={dayData} extraSr={extraSr} idxData={idxData} variant={rightChartVariant} timeframe={activeChartTimeframe} emptyMessage={`尚無${activeChartLabel}資料`} showIndicatorPane={showIndicatorPane} daySrMode="horizontal" indicatorMode={chartIndicatorMode} indicatorStockId={stockId} indicatorEventTime={indicatorEventTime} macdMap={macdMap} obvMap={obvMap} /></div>
            </ChartPanel>
          </div>
        </div>
      </main>

      {watchDrawerOpen ? <><button type="button" className="fixed inset-0 z-40 cursor-default bg-black/35" aria-label="關閉觀察清單" onClick={closeWatchDrawer} /><aside ref={watchDrawerRef} tabIndex={-1} className="fixed right-0 top-0 z-50 flex h-dvh w-[min(420px,calc(100vw-1rem))] flex-col border-l border-base-300 bg-base-100 shadow-2xl" aria-label="觀察清單" onFocusCapture={() => setFocusedPanel("obs")} onMouseDown={() => setFocusedPanel("obs")}><div className="flex min-h-12 items-center justify-between border-b border-base-300 bg-base-200 px-3"><div className="min-w-0 truncate text-sm font-semibold text-primary">觀察 <span className="font-normal text-base-content/45">({obsRows.length})</span></div><button type="button" className="btn btn-ghost btn-square btn-xs rounded" title="關閉" aria-label="關閉" onClick={closeWatchDrawer}>×</button></div><div className="min-h-0 flex-1 overflow-auto">{obsRows.length ? <table className="table table-xs table-pin-rows min-w-max"><thead><tr><th>股票</th><th>漲幅</th><th className="text-right">時間</th><th className="text-center">SR</th><th className="text-center">MACD</th><th className="text-center">OBV</th></tr></thead><tbody>{obsRows.map((row, idx) => { const key = eventKey(row); const selected = selectedEventKey ? key === selectedEventKey : String(stockId) === String(row.stock_id); return <tr key={row.stock_id} data-panel="obs" data-row-index={idx} className={selected ? "bg-primary/15" : ""} onClick={() => selectMarketRow(row, key, "obs", "", false)} onDoubleClick={() => toggleObsStock(row.stock_id)}><StockCell row={row} /><td className={row.chg_pct == null ? "text-base-content/35" : row.chg_pct >= 0 ? "text-error" : "text-success"}>{row.chg_pct == null ? "" : `${row.chg_pct >= 0 ? "+" : ""}${Number(row.chg_pct).toFixed(2)}%`}</td><td className="text-right text-base-content/50">{hm(row.time)}</td><td className="text-center"><Lamp on={row.sr_on} kind="both" title="SR" /></td><td className="text-center"><Lamp on={row.macd_on} kind={row.macd_kind} title="MACD" /></td><td className="text-center"><Lamp on={row.obv_on} kind={row.obv_kind} title="OBV" /></td></tr>; })}</tbody></table> : <div className="flex h-full items-center justify-center p-6 text-center text-sm text-base-content/50">在盤中訊號框雙擊股票加入觀察</div>}</div></aside></> : null}
    </div>
  );
}
