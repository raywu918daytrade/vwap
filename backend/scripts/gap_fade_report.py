"""隔日沖賣壓：昨天大漲、今天開高，跌破開盤價（或開盤 5 分鐘低點）就當沖放空。

1. D1（還原日K）：昨天漲幅 >= --min-gain 且今天開盤 > 昨天收盤。
   m5_res（Ray 2026-10-09）：那根收盤跌破的 M5 還要「最高 >= D1 壓力線 > 收盤」，壓力線同網頁
   （pattern.horizontal_sr，用當天之前的還原日K）；沒有壓力線的股票不做。
   進場改成（Ray 2026-10-09）：M5 收紅 K，下一根 M5 收盤收在這根紅 K 低點之下，再下一根 M5 開盤放空。
2. 當天 ATR14 >= 5% 且開盤 5 分鐘量 PR >= 50（網頁同一個數字）。
3. M1：09:05 ~ 11:00 第一次觸發就放空（舊版觸發價：開盤價或 09:00-09:04 最低），
   下一分鐘開盤進場；停利 3.5% / 停損 3%，否則 13:24 收盤平倉。成本 0.435%。

參數只在 7～8 月比較（漲幅門檻 x 觸發價），用 7～8 月平均最好的那組去跑 9～10 月。
對照組：同期間通過 ATR／量篩選的股票，在 09:05 ~ 11:00 隨機一分鐘放空（看大盤漂移）。

    python -m scripts.gap_fade_report --train 2026-07-01 2026-08-31 --test 2026-09-01 2026-10-08
"""

from __future__ import annotations

import argparse
import random

import numpy as np
import pandas as pd

import trade_stats_api as ts
from data.adjustment_query import load_pattern_day
from pattern.horizontal_sr import horizontal_sr_prices
from pattern.vwap_activity import metrics_for_date as activity_for_date
from pattern.vwap_sr_scan import stock_ids_for_universe
from scripts.trade_stats_report import one_at_a_time, streaks

FIRST, LAST = "09:05", "11:00"
GAINS = (0.05, 0.07, 0.095)
TRIGGERS = {"m5": "紅M5後下一根M5收盤跌破其低點"}


def candidates(day: pd.DataFrame, trade_days: list[str], min_gain: float) -> list[dict]:
    """(date, stock) where yesterday rose >= min_gain and today opens above yesterday's close."""
    d = day.sort_values(["stock_id", "date"]).copy()
    g = d.groupby("stock_id", sort=False)["close"]
    d["prev_close"] = g.shift(1)
    d["prev_gain"] = d["prev_close"] / g.shift(2) - 1
    d["ds"] = d["date"].dt.strftime("%Y-%m-%d")
    sel = d[d["ds"].isin(trade_days) & (d["prev_gain"] >= min_gain) & (d["open"] > d["prev_close"])]
    by_stock = {str(k): v for k, v in day.groupby("stock_id", sort=False)} if not sel.empty else {}
    out = []
    for r in sel.itertuples(index=False):
        g = by_stock.get(str(r.stock_id))
        hist = g[g["date"] < r.date] if g is not None else None
        res = horizontal_sr_prices(hist)[0] if hist is not None else None
        out.append({"stock_id": str(r.stock_id), "date": r.ds, "prev_gain": float(r.prev_gain),
                    "gap": float(r.open / r.prev_close - 1), "res": res})
    return out


def m5_break_time(bars: pd.DataFrame, res: float | None = None) -> str | None:
    """Last minute of the first M5 bar that closes below the low of the previous M5 bar, when that bar was up.

    The trade then opens at the next minute's open, i.e. the next M5 bar's open.
    """
    hh = bars["hhmm"]
    bucket = hh.str[:3] + (hh.str[3:].astype(int) // 5 * 5).astype(str).str.zfill(2)
    m5 = bars.groupby(bucket, sort=True).agg(open=("open", "first"), close=("close", "last"),
                                             low=("low", "min"), high=("high", "max"),
                                             last=("hhmm", "last"))
    rows = list(m5.itertuples())
    for p, c in zip(rows, rows[1:]):
        if not float(p.close) > float(p.open):
            continue
        if res is not None and not (float(c.high) >= res > float(c.close)):
            continue
        if float(c.close) < float(p.low) and FIRST <= c.last <= LAST:
            return str(c.last)
    return None


def trigger_time(bars: pd.DataFrame, how: str, res: float | None = None) -> str | None:
    if bars.empty:
        return None
    if how == "m5":
        return m5_break_time(bars)
    if how == "m5_res":
        return m5_break_time(bars, res) if res is not None else None
    level = float(bars["open"].iloc[0]) if how == "open" else float(bars[bars["hhmm"] < FIRST]["low"].min())
    if not np.isfinite(level):
        return None
    win = bars[(bars["hhmm"] >= FIRST) & (bars["hhmm"] <= LAST)]
    hit = win[win["close"].astype(float) < level]
    return str(hit["hhmm"].iloc[0]) if not hit.empty else None


def stats(trades: list[dict]) -> str:
    if not trades:
        return "| 0 | - | - | - | - | - | - | - |"
    net = [t["net_pct"] for t in trades]
    one = streaks(one_at_a_time(trades))
    exits = "/".join(str(sum(t["status"] == k for t in trades)) for k in ("停利", "停損", "收盤平倉"))
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[t["date"]] = by_day.get(t["date"], 0.0) + t["net_pct"]
    top = max(by_day, key=by_day.get)
    rest = [t["net_pct"] for t in trades if t["date"] != top]
    rest_s = f"{np.mean(rest):+.3f}%（扣 {top[5:]}）" if rest else "-"
    return (f"| {len(net)} | {sum(x > 0 for x in net) / len(net) * 100:.1f}% | {np.mean(net):+.3f}% | {exits} "
            f"| {one['n']} 筆 {one['flat']:+.1f}% | {one['max_streak']} | {one['mart']:+.1f}% | {rest_s} |")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", nargs=2, required=True)
    parser.add_argument("--test", nargs=2, required=True)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--window", nargs=2, default=[FIRST, LAST], help="entry window, e.g. 10:00 13:00")
    parser.add_argument("--baseline-n", type=int, default=1500, help="random stock-days per period")
    args = parser.parse_args()
    global FIRST, LAST
    FIRST, LAST = args.window
    rules = {**ts.RULES, "take_profit_pct": 3.5, "stop_loss_pct": 3.0}
    periods = {"7～8月（找參數）": tuple(args.train), "9～10月（驗證）": tuple(args.test)}

    stock_ids = stock_ids_for_universe("daytrade")
    start = (pd.Timestamp(args.train[0]) - pd.Timedelta(days=200)).strftime("%Y-%m-%d")
    day = load_pattern_day(start_date=start, end_date=args.test[1])
    day["stock_id"] = day["stock_id"].astype(str)
    day = day[day["stock_id"].isin(stock_ids)]
    all_days = sorted(day["date"].dt.strftime("%Y-%m-%d").unique())

    act_cache: dict[str, dict] = {}

    def passes(sid: str, d: str) -> bool:
        if d not in act_cache:
            act_cache[d] = {str(k): v for k, v in (activity_for_date(d) or {}).items()}
        m = act_cache[d].get(sid) or {}
        return (m.get("day_atr") or 0) >= 0.05 and (m.get("vol5_pr") or 0) >= 0.5

    bars_cache: dict[tuple[str, str], pd.DataFrame] = {}

    def bars(sid: str, d: str) -> pd.DataFrame:
        if (sid, d) not in bars_cache:
            bars_cache[(sid, d)] = ts._day_bars(sid, d)
        return bars_cache[(sid, d)]

    def run(cands: list[dict], how: str) -> list[dict]:
        out = []
        for c in cands:
            b = bars(c["stock_id"], c["date"])
            t = trigger_time(b, how, c.get("res"))
            if t is None:
                continue
            trade = ts._simulate({**c, "side": "short", "signal_time": t}, b, True, rules)
            if trade["net_pct"] is not None:
                out.append(trade)
        return out

    header = ("| 組合 | 筆數 | 勝率 | 平均 | 停利/停損/收盤 | 一次一筆 | 最多連輸 | 輸加倍 1-2-4-8 | 扣最好一天的平均 |\n"
              "|---|---|---|---|---|---|---|---|---|")
    results: dict[str, dict[tuple, list[dict]]] = {}
    for label, (a, b) in periods.items():
        tdays = [d for d in all_days if a <= d <= b]
        results[label] = {}
        print(f"\n===== {label} {a} ~ {b}（{len(tdays)} 天，訊號 {FIRST}～{LAST}，只做空，停利 3.5 / 停損 3，成本 {rules['cost_pct']}%）=====")
        print(header)
        for g in GAINS:
            cands = [c for c in candidates(day, tdays, g) if passes(c["stock_id"], c["date"])]
            for how, name in TRIGGERS.items():
                trades = run(cands, how)
                results[label][(g, how)] = trades
                print(f"| 昨漲≥{g * 100:g}%＋開高＋{name}（候選 {len(cands)}）{stats(trades)}", flush=True)

        # Baseline: random filtered stock-days, short at a random minute in the same window.
        rng = random.Random(args.seed)
        pool = [(sid, d) for d in tdays for sid in sorted(stock_ids) if passes(sid, d)]
        sample = rng.sample(pool, min(args.baseline_n, len(pool)))
        base = []
        for sid, d in sample:
            b = bars(sid, d)
            mins = b[(b["hhmm"] >= FIRST) & (b["hhmm"] <= LAST)]["hhmm"].tolist() if not b.empty else []
            if not mins:
                continue
            trade = ts._simulate({"stock_id": sid, "date": d, "side": "short", "signal_time": rng.choice(mins)},
                                 b, True, rules)
            if trade["net_pct"] is not None:
                base.append(trade)
            bars_cache.pop((sid, d), None)
        print(f"| 對照：ATR＋量篩選後隨機時間放空（抽 {len(sample)} 個股票日）{stats(base)}", flush=True)

    train, test = list(periods)
    scored = [(np.mean([t["net_pct"] for t in v]), k) for k, v in results[train].items() if len(v) >= 20]
    if scored:
        best = max(scored)[1]
        print(f"\n7～8月平均最好（至少 20 筆）：昨漲≥{best[0] * 100:g}%＋{TRIGGERS[best[1]]}")
        print(header)
        for label in periods:
            print(f"| {label} {stats(results[label][best])}")
        sample = results[test][best][:]
        print("\n9～10月 這組的交易：")
        for t in sorted(sample, key=lambda t: (t["date"], t["entry_time"])):
            print(f"  {t['date']} {t['stock_id']} 昨漲 {t['prev_gain'] * 100:.1f}% 開高 {t['gap'] * 100:.1f}% "
                  f"進 {t['entry_time']} @{t['entry_price']} 出 {t['exit_time']} {t['status']} {t['net_pct']:+.2f}%")


if __name__ == "__main__":
    main()
