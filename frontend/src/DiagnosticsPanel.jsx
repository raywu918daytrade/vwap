import { useCallback, useEffect, useState } from "react";
import { fetchJson } from "./api.js";

function value(input) {
  return input == null || input === "" ? "-" : String(input);
}

function clock(input) {
  const text = value(input);
  return text === "-" ? text : text.replace("T", " ").slice(5, 19);
}

function coverageText(coverage) {
  const arrived = Number(coverage?.arrived || 0);
  const total = Number(coverage?.total || 0);
  if (!total) return `${arrived}/${total}`;
  return `${arrived}/${total} (${((arrived / total) * 100).toFixed(1)}%)`;
}

function directionText(event) {
  if (event.direction === "up") return "突破";
  if (event.direction === "down") return "跌破";
  return event.direction || event.sr_kind || "-";
}

export default function DiagnosticsPanel() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fetchJson("/api/diagnostics/live"));
      setError("");
    } catch (requestError) {
      setError(requestError.message || "診斷資料載入失敗");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    refresh();
    const timer = window.setInterval(refresh, 15000);
    return () => window.clearInterval(timer);
  }, [open, refresh]);

  const health = data?.health || {};
  const m1 = data?.m1 || {};
  const signals = data?.signals || {};
  const quote = m1.quote_0050 || {};

  return (
    <>
      <button
        type="button"
        className={`btn btn-sm fixed bottom-3 right-3 z-[60] rounded shadow-lg ${data?.ok ? "btn-success" : "btn-primary"}`}
        onClick={() => setOpen(true)}
      >
        診斷
      </button>
      {open ? (
        <>
          <button type="button" className="fixed inset-0 z-[70] bg-black/45" aria-label="關閉診斷" onClick={() => setOpen(false)} />
          <aside className="fixed right-0 top-0 z-[80] flex h-dvh w-[min(520px,100vw)] flex-col border-l border-base-300 bg-base-100 shadow-2xl">
            <header className="flex min-h-12 items-center justify-between border-b border-base-300 bg-base-200 px-3">
              <div>
                <span className="font-semibold text-primary">Oracle 盤中診斷</span>
                <span className={`ml-2 badge badge-sm ${data?.ok ? "badge-success" : "badge-error"}`}>
                  {data?.ok ? "正常" : "異常／檢查中"}
                </span>
              </div>
              <div className="flex gap-2">
                <button type="button" className="btn btn-xs rounded" onClick={refresh} disabled={loading}>更新</button>
                <button type="button" className="btn btn-ghost btn-square btn-xs rounded" onClick={() => setOpen(false)}>×</button>
              </div>
            </header>
            <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3 text-xs">
              {error ? <div className="alert alert-error py-2">{error}</div> : null}
              <div className="text-base-content/55">報告時間：{clock(data?.generated_at)}　版本：{value(health.version)}</div>

              <section className="rounded border border-base-300">
                <h2 className="border-b border-base-300 bg-base-200 px-3 py-2 font-semibold">服務與資料流</h2>
                <div className="grid grid-cols-2 gap-x-3 gap-y-2 p-3">
                  <span>API</span><span>{value(health.status)}</span>
                  <span>Collector</span><span>{value(health.collector)}／{value(health.message)}</span>
                  <span>SSE clients</span><span>{value(health.sse_clients)}</span>
                  <span>分鐘涵蓋率</span><span>{coverageText(health.coverage)}</span>
                  <span>錯誤累計</span><span>{value(data?.operations?.error_count)}</span>
                </div>
              </section>

              <section className="rounded border border-base-300">
                <h2 className="border-b border-base-300 bg-base-200 px-3 py-2 font-semibold">當日 M1 報價</h2>
                <div className="grid grid-cols-2 gap-x-3 gap-y-2 p-3">
                  <span>第一分鐘</span><span>{clock(m1.first_minute)}</span>
                  <span>最新分鐘</span><span>{clock(m1.latest_minute)}</span>
                  <span>延遲</span><span className={m1.freshness_ok ? "text-success" : "text-error"}>{value(m1.latest_delay_seconds)} 秒</span>
                  <span>資料筆數／股票數</span><span>{value(m1.rows)}／{value(m1.stocks)}</span>
                  <span>分鐘數</span><span>{value(m1.minutes)}</span>
                  <span>最新分鐘股票數</span><span>{value(m1.latest_minute_stocks)}</span>
                  <span>完全缺少分鐘數</span><span className={m1.missing_market_minutes_count ? "text-warning" : "text-success"}>{value(m1.missing_market_minutes_count)}</span>
                </div>
              </section>

              <section className="rounded border border-base-300">
                <h2 className="border-b border-base-300 bg-base-200 px-3 py-2 font-semibold">0050 最新報價</h2>
                <div className="grid grid-cols-3 gap-2 p-3 text-center">
                  <div><div className="text-base-content/45">時間</div>{clock(quote._minute)}</div>
                  <div><div className="text-base-content/45">收</div>{value(quote.close)}</div>
                  <div><div className="text-base-content/45">量</div>{value(quote.volume)}</div>
                  <div><div className="text-base-content/45">開</div>{value(quote.open)}</div>
                  <div><div className="text-base-content/45">高</div>{value(quote.high)}</div>
                  <div><div className="text-base-content/45">低</div>{value(quote.low)}</div>
                </div>
              </section>

              <section className="rounded border border-base-300">
                <h2 className="border-b border-base-300 bg-base-200 px-3 py-2 font-semibold">
                  VWAP／SR 訊號（{value(signals.vwap_count)}／{value(signals.sr_count)}）
                </h2>
                <div className="overflow-auto">
                  <table className="table table-xs min-w-max">
                    <thead><tr><th>時間</th><th>類型</th><th>股票</th><th>判斷</th><th>價／VWAP</th></tr></thead>
                    <tbody>
                      {(signals.latest_events || []).slice().reverse().map((event, index) => (
                        <tr key={`${event.kind}-${event.stock_id}-${event.time}-${index}`}>
                          <td>{value(event.time)}</td><td>{value(event.kind)}</td>
                          <td>{event.stock_id} {event.name}</td><td>{directionText(event)}</td>
                          <td>{value(event.price)}／{value(event.vwap)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section className="rounded border border-base-300">
                <h2 className="border-b border-base-300 bg-base-200 px-3 py-2 font-semibold">安全運作紀錄</h2>
                <div className="space-y-1 p-3 font-mono">
                  {(data?.operations?.safe_logs || []).map((row, index) => (
                    <div key={`${row.time}-${index}`}>{clock(row.time)}　{row.msg}</div>
                  ))}
                  {!(data?.operations?.safe_logs || []).length ? <div className="text-base-content/45">目前沒有可公開的運作紀錄</div> : null}
                </div>
              </section>
            </div>
          </aside>
        </>
      ) : null}
    </>
  );
}
