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

function statusClass(status) {
  if (/異常|延遲|等待資料/.test(status || "")) return "text-error";
  if (/尚無/.test(status || "")) return "text-warning";
  return "text-success";
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
      setError(requestError.message || "報價監控資料載入失敗");
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

  const pipeline = data?.pipeline || {};
  const quote = data?.quote || {};
  const consumer = data?.consumer || {};
  const render = data?.render || {};

  return (
    <>
      <button
        type="button"
        className={`btn btn-sm fixed bottom-3 right-3 z-[60] rounded shadow-lg ${data?.ok ? "btn-success" : "btn-primary"}`}
        onClick={() => setOpen(true)}
      >
        報價監控
      </button>
      {open ? (
        <>
          <button type="button" className="fixed inset-0 z-[70] bg-black/45" aria-label="關閉報價監控" onClick={() => setOpen(false)} />
          <aside className="fixed right-0 top-0 z-[80] flex h-dvh w-[min(480px,100vw)] flex-col border-l border-base-300 bg-base-100 shadow-2xl">
            <header className="flex min-h-12 items-center justify-between border-b border-base-300 bg-base-200 px-3">
              <div>
                <span className="font-semibold text-primary">報價資料流監控</span>
                <span className={`ml-2 badge badge-sm ${data?.ok ? "badge-success" : "badge-error"}`}>
                  {data?.ok ? value(data?.phase) : "需要檢查"}
                </span>
              </div>
              <div className="flex gap-2">
                <button type="button" className="btn btn-xs rounded" onClick={refresh} disabled={loading}>更新</button>
                <button type="button" className="btn btn-ghost btn-square btn-xs rounded" aria-label="關閉" onClick={() => setOpen(false)}>×</button>
              </div>
            </header>
            <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3 text-xs">
              {error ? <div className="alert alert-error py-2">{error}</div> : null}
              <div className="text-base-content/55">報告時間：{clock(data?.generated_at)}　版本：{value(render.version)}</div>

              <section className="rounded border border-base-300">
                <h2 className="border-b border-base-300 bg-base-200 px-3 py-2 font-semibold">Oracle → HF → Render</h2>
                <div className="grid grid-cols-2 gap-x-3 gap-y-2 p-3">
                  <span>Oracle 收報價</span><span className={statusClass(pipeline.oracle)}>{value(pipeline.oracle)}</span>
                  <span>HF 發布資料</span><span className={statusClass(pipeline.hf)}>{value(pipeline.hf)}</span>
                  <span>Render 看盤</span><span className={statusClass(pipeline.render)}>{value(pipeline.render)}</span>
                </div>
              </section>

              <section className="rounded border border-base-300">
                <h2 className="border-b border-base-300 bg-base-200 px-3 py-2 font-semibold">最新 M1 發布</h2>
                <div className="grid grid-cols-2 gap-x-3 gap-y-2 p-3">
                  <span>交易日</span><span>{value(quote.trading_date)}</span>
                  <span>最新分鐘</span><span>{clock(quote.latest_minute)}</span>
                  <span>發布時間</span><span>{clock(quote.published_at)}</span>
                  <span>盤中延遲</span><span className={quote.fresh ? "text-success" : data?.phase === "盤中" ? "text-error" : ""}>{data?.phase === "盤中" ? `${value(quote.delay_seconds)} 秒` : "不適用"}</span>
                  <span>分鐘涵蓋率</span><span>{coverageText(quote.coverage)}</span>
                  <span>本次差異筆數</span><span>{value(quote.delta?.rows)}</span>
                </div>
              </section>

              <section className="rounded border border-base-300">
                <h2 className="border-b border-base-300 bg-base-200 px-3 py-2 font-semibold">Render 消費狀態</h2>
                <div className="grid grid-cols-2 gap-x-3 gap-y-2 p-3">
                  <span>最後檢查 HF</span><span>{clock(consumer.checked_at)}</span>
                  <span>最後套用</span><span>{clock(consumer.applied_at)}</span>
                  <span>套用方式</span><span>{consumer.apply_mode === "delta" ? "分鐘差異" : consumer.apply_mode === "snapshot" ? "完整快照" : value(consumer.apply_mode)}</span>
                  <span>同步錯誤</span><span className={consumer.error && data?.phase === "盤中" ? "text-error" : "text-success"}>{consumer.error ? (data?.phase === "盤中" ? consumer.error : "非交易時段不影響") : "無"}</span>
                  <span>API</span><span>{value(render.status)}</span>
                  <span>SSE clients</span><span>{value(render.sse_clients)}</span>
                </div>
              </section>
            </div>
          </aside>
        </>
      ) : null}
    </>
  );
}
