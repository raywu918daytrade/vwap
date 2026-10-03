import { useCallback, useEffect, useRef, useState } from "react";
import { fetchJson } from "./api.js";
import { taipeiTodayIso } from "./date.js";

const REFRESH_MS = 20000;

function pct(input, digits = 2) {
  if (input == null || Number.isNaN(Number(input))) return "-";
  const n = Number(input);
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

function pnlClass(input) {
  if (input == null) return "text-base-content/40";
  return Number(input) > 0 ? "text-error" : Number(input) < 0 ? "text-success" : "";
}

function price(input) {
  return input == null ? "-" : Number(input).toFixed(2);
}

function shiftWeekday(iso, step) {
  const d = new Date(`${iso}T12:00:00Z`);
  do {
    d.setUTCDate(d.getUTCDate() + step);
  } while (d.getUTCDay() === 0 || d.getUTCDay() === 6);
  return d.toISOString().slice(0, 10);
}

const STRATEGIES = [
  { key: "sr_short", label: "VWAP＋支撐做空" },
  { key: "vwap_cross", label: "VWAP 穿越" },
];

const STATUS_CLASS = {
  停利: "badge-error",
  停損: "badge-success",
  收盤平倉: "badge-ghost",
  持有中: "badge-warning",
};

export default function TradeStatsPanel() {
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState(taipeiTodayIso());
  const [strategy, setStrategy] = useState(STRATEGIES[0].key);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const today = taipeiTodayIso();
  const isToday = date === today;
  const dateRef = useRef(date);
  dateRef.current = date;

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await fetchJson(`/api/trade_stats?date=${encodeURIComponent(date)}&strategy=${strategy}`);
      if (dateRef.current !== date) return;
      setData(payload);
      setError("");
    } catch (requestError) {
      setError(requestError.message || "交易統計載入失敗");
    } finally {
      setLoading(false);
    }
  }, [date, strategy]);

  useEffect(() => {
    if (!open) return undefined;
    setData(null);
    refresh();
    if (!isToday) return undefined;
    const timer = window.setInterval(refresh, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [open, isToday, refresh]);

  const rules = data?.rules;
  const summary = data?.summary || {};
  const trades = data?.trades || [];

  return (
    <>
      <button
        type="button"
        className="btn btn-sm btn-secondary fixed bottom-[calc(0.75rem+env(safe-area-inset-bottom))] right-[6.75rem] z-[60] rounded shadow-lg"
        onClick={() => setOpen(true)}
      >
        交易統計
      </button>
      {open ? (
        <>
          <button type="button" className="fixed inset-0 z-[70] bg-black/45" aria-label="關閉交易統計" onClick={() => setOpen(false)} />
          <aside className="fixed right-0 top-0 z-[80] flex h-dvh w-[min(560px,100vw)] flex-col border-l border-base-300 bg-base-100 shadow-2xl">
            <header className="flex min-h-12 items-center justify-between gap-2 border-b border-base-300 bg-base-200 px-3">
              <span className="font-semibold text-primary">交易統計</span>
              <div className="flex items-center gap-1">
                <button type="button" className="btn btn-xs rounded" aria-label="前一天" onClick={() => setDate(shiftWeekday(date, -1))}>‹</button>
                <input
                  type="date"
                  className="input input-xs input-bordered rounded"
                  value={date}
                  max={today}
                  onChange={(event) => event.target.value && setDate(event.target.value)}
                />
                <button type="button" className="btn btn-xs rounded" aria-label="後一天" disabled={isToday} onClick={() => setDate(shiftWeekday(date, 1) > today ? today : shiftWeekday(date, 1))}>›</button>
                <button type="button" className={`btn btn-xs rounded ${isToday ? "btn-primary" : ""}`} onClick={() => setDate(today)}>今天</button>
                <button type="button" className="btn btn-xs rounded" onClick={refresh} disabled={loading}>更新</button>
                <button type="button" className="btn btn-ghost btn-square btn-xs rounded" aria-label="關閉" onClick={() => setOpen(false)}>×</button>
              </div>
            </header>
            <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3 text-xs">
              <div className="join">
                {STRATEGIES.map((s) => (
                  <button key={s.key} type="button" className={`btn btn-xs join-item rounded ${strategy === s.key ? "btn-primary" : ""}`} onClick={() => setStrategy(s.key)}>{s.label}</button>
                ))}
              </div>
              {error ? <div className="alert alert-error py-2">{error}</div> : null}
              {rules ? (
                <div className="text-base-content/60">
                  {rules.signal}・{rules.window[0]}–{rules.window[1]} 訊號，下一分鐘開盤進場・停利 {rules.take_profit_pct}% / 停損 {rules.stop_loss_pct}%・{rules.last_bar} 收盤平倉・ATR ≥ {rules.min_day_atr * 100}%、量 PR ≥ {rules.min_vol5_pr * 100}・成本 {rules.cost_pct}%・每檔每天一筆
                </div>
              ) : null}

              <section className="grid grid-cols-4 gap-2 rounded border border-base-300 p-3 text-center">
                <div><div className="text-base-content/55">筆數</div><div className="text-base font-semibold">{summary.signals ?? "-"}</div></div>
                <div><div className="text-base-content/55">勝率</div><div className="text-base font-semibold">{summary.win_rate == null ? "-" : `${summary.win_rate}%`}</div><div className="text-base-content/45">{summary.wins ?? 0}/{summary.closed ?? 0}</div></div>
                <div><div className="text-base-content/55">已平倉合計</div><div className={`text-base font-semibold ${pnlClass(summary.total_net_pct)}`}>{pct(summary.total_net_pct)}</div><div className="text-base-content/45">平均 {pct(summary.avg_net_pct)}</div></div>
                <div><div className="text-base-content/55">持有中</div><div className="text-base font-semibold">{summary.open ?? 0}</div><div className={pnlClass(summary.open_net_pct)}>{summary.open ? pct(summary.open_net_pct) : ""}</div></div>
              </section>

              <div className="overflow-x-auto rounded border border-base-300">
                <table className="table table-xs min-w-max">
                  <thead>
                    <tr>
                      <th>股票</th>
                      <th>方向</th>
                      <th>訊號</th>
                      <th>進場</th>
                      <th>出場</th>
                      <th>結果</th>
                      <th className="text-right">損益(扣成本)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.length ? trades.map((t) => (
                      <tr key={t.stock_id}>
                        <td><div className="font-semibold">{t.stock_id}</div><div className="text-base-content/50">{t.name}</div></td>
                        <td>{t.side === "long" ? <span className="text-error">做多</span> : <span className="text-success">做空</span>}</td>
                        <td>{t.signal_time}</td>
                        <td>{t.entry_time ? <>{t.entry_time}<div className="text-base-content/50">{price(t.entry_price)}</div></> : "-"}</td>
                        <td>
                          {t.exit_time ? <>{t.exit_time}<div className="text-base-content/50">{price(t.exit_price)}</div></> : t.status === "持有中" ? <span className="text-base-content/50">現價 {price(t.last_price)}</span> : "-"}
                        </td>
                        <td><span className={`badge badge-sm ${STATUS_CLASS[t.status] || "badge-outline"}`}>{t.status}</span></td>
                        <td className={`text-right font-semibold ${pnlClass(t.net_pct)}`}>{pct(t.net_pct)}</td>
                      </tr>
                    )) : (
                      <tr>
                        <td colSpan={7} className="py-8 text-center text-base-content/50">
                          {loading ? "載入中…" : data && !data.activity_ready ? "這天還沒有過濾用的活動度資料" : "這天沒有符合條件的訊號"}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              {data?.generated_at ? <div className="text-base-content/45">計算時間：{data.generated_at.replace("T", " ")}{isToday ? "（盤中每 20 秒更新）" : ""}</div> : null}
            </div>
          </aside>
        </>
      ) : null}
    </>
  );
}
