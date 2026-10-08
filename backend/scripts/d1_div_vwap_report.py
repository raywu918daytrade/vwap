"""Daily MACD histogram divergence, then an intraday VWAP cross within 5 days.

1. D1: find MACD histogram divergences on daily bars (bull and bear), using the
   same pairing rules as the dashboard (pattern.macd_hist_bull.detector).
   A divergence is known after the close of its confirmation day.
2. M1: in the next 5 trading days, the first VWAP cross in the divergence's
   direction (bull: cross up -> long, bear: cross down -> short, 09:05-13:24)
   opens the trade at the next minute's open. One trade per divergence.
3. Exit at +TP% / -SL% (stop wins when one bar hits both), else the 13:24 close.

With --ma-cross, step 2 first waits for a D1 MA5/MA10 cross in the same
direction (bull: MA5 crosses above MA10, bear: below) in the 5 trading days after
the divergence; the VWAP window then starts the day after that cross.

With --min-atr 0.05, a VWAP cross only counts on a day whose ATR14 (as of the
day before, same number the dashboard filter uses) is at least 5%.

Prints win rate, streaks and the 1-2-4-8 runs for long / short / both, and
saves 3 random charts per step to --chart-dir for checking by eye.

    python -m scripts.d1_div_vwap_report --from-date 2026-07-01 --to-date 2026-10-08
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np
import pandas as pd

import trade_stats_api as ts
from data.adjustment_query import load_pattern_day
from pattern.macd_hist_bull.detector import iter_macd_hist_div_pairs, macd_histogram
from pattern.vwap_activity import metrics_for_date as activity_for_date
from pattern.vwap_signal_store import read_vwap_signals
from pattern.vwap_sr_scan import stock_ids_for_universe
from scripts.trade_stats_report import one_at_a_time, streaks

WINDOW_DAYS = 5
FIRST_CROSS = "09:05"


def find_divergences(day: pd.DataFrame, from_date: str, to_date: str) -> list[dict]:
    """Every D1 divergence confirmed between from_date and to_date."""
    out = []
    for sid, g in day.groupby("stock_id", sort=False):
        g = g.sort_values("date").reset_index(drop=True)
        if len(g) < 40:
            continue
        close = g["close"].astype(float).to_numpy()
        if not np.isfinite(close).all():
            continue
        hist = macd_histogram(close)
        highs, lows = g["high"].astype(float).to_numpy(), g["low"].astype(float).to_numpy()
        dates = g["date"].dt.strftime("%Y-%m-%d").to_numpy()
        for kind in ("bull", "bear"):
            for pair in iter_macd_hist_div_pairs(hist, highs, lows, side=kind):
                c = int(pair["confirmed"])
                if c < 39 or c >= len(g) or not (from_date <= dates[c] <= to_date):
                    continue
                out.append({"stock_id": str(sid), "kind": kind, "confirm_date": dates[c],
                            "d1": dates[int(pair["p1"])], "d2": dates[int(pair["p2"])],
                            "price1": pair["price1"], "price2": pair["price2"]})
    return sorted(out, key=lambda d: (d["confirm_date"], d["stock_id"], d["kind"]))


def find_ma_crosses(divs: list[dict], day: pd.DataFrame) -> list[dict]:
    """Divergences followed by a same-direction D1 MA5/MA10 cross within 5 trading days."""
    by_stock = {}
    for sid, g in day.groupby("stock_id", sort=False):
        g = g.sort_values("date")
        close = g["close"].astype(float)
        by_stock[str(sid)] = (g["date"].dt.strftime("%Y-%m-%d").tolist(),
                              close.rolling(5).mean().to_numpy(), close.rolling(10).mean().to_numpy())
    out = []
    for div in divs:
        dates, ma5, ma10 = by_stock[div["stock_id"]]
        if div["confirm_date"] not in dates:
            continue
        c = dates.index(div["confirm_date"])
        for j in range(c + 1, min(c + 1 + WINDOW_DAYS, len(dates))):
            diff_prev, diff = ma5[j - 1] - ma10[j - 1], ma5[j] - ma10[j]
            if not (np.isfinite(diff_prev) and np.isfinite(diff)):
                continue
            if (div["kind"] == "bull" and diff_prev <= 0 < diff) or (div["kind"] == "bear" and diff_prev >= 0 > diff):
                out.append({**div, "ma_date": dates[j]})
                break
    return out


def find_entries(divs: list[dict], trade_days: list[str], stock_ids: set[str],
                 min_atr: float | None = None) -> list[dict]:
    """First matching VWAP cross in the 5 trading days after each divergence (or MA cross)."""
    crosses: dict[tuple[str, str], list[dict]] = {}
    for d in trade_days:
        atr = {}
        if min_atr is not None:
            atr = {str(k): v.get("day_atr") for k, v in (activity_for_date(d) or {}).items()}
        bundle = read_vwap_signals(d, stock_ids=stock_ids) or {}
        for row in bundle.get("vwap") or []:
            hhmm = str(row.get("time") or "")[:5]
            if min_atr is not None and not ((atr.get(str(row.get("stock_id"))) or 0) >= min_atr):
                continue
            if FIRST_CROSS <= hhmm < ts.RULES["last_bar"]:
                crosses.setdefault((d, str(row.get("stock_id"))), []).append({**row, "time": hhmm})
    entries = []
    for div in divs:
        want = "up" if div["kind"] == "bull" else "down"
        start = div.get("ma_date") or div["confirm_date"]
        later = [d for d in trade_days if d > start][:WINDOW_DAYS]
        for d in later:
            hits = sorted((r for r in crosses.get((d, div["stock_id"]), []) if r.get("direction") == want),
                          key=lambda r: r["time"])
            if hits:
                entries.append({**div, "date": d, "signal_time": hits[0]["time"],
                                "side": "long" if want == "up" else "short", "name": hits[0].get("name") or ""})
                break
    return entries


def _vwap(bars: pd.DataFrame) -> pd.Series:
    vol = bars["volume"].astype(float) if "volume" in bars else pd.Series(1.0, index=bars.index)
    return (bars["close"].astype(float) * vol).cumsum() / vol.cumsum().replace(0, np.nan)


def chart_divergence(div: dict, day: pd.DataFrame, path: Path, title: str | None = None) -> None:
    import matplotlib.pyplot as plt

    end = div.get("ma_date") or div["confirm_date"]
    full = day[(day["stock_id"] == div["stock_id"]) & (day["date"] <= pd.Timestamp(end))]
    full = full.sort_values("date")
    hist = macd_histogram(full["close"].astype(float).to_numpy())
    g = full.tail(80).reset_index(drop=True)
    hist = hist[-len(g):]
    x = np.arange(len(g))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    up = g["close"] >= g["open"]
    ax1.vlines(x, g["low"], g["high"], color="black", linewidth=0.6)
    ax1.bar(x, (g["close"] - g["open"]).abs().clip(lower=1e-6), bottom=g[["open", "close"]].min(axis=1),
            color=np.where(up, "tab:red", "tab:green"), width=0.7)
    ax2.bar(x, hist, color=np.where(hist >= 0, "tab:red", "tab:green"), width=0.7)
    ds = g["date"].dt.strftime("%Y-%m-%d").tolist()
    for key, price in (("d1", div["price1"]), ("d2", div["price2"])):
        if div[key] in ds:
            i = ds.index(div[key])
            ax1.annotate(key, (i, price), xytext=(0, -14 if div["kind"] == "bull" else 10),
                         textcoords="offset points", ha="center", color="blue", fontsize=9)
            ax1.plot(i, price, "o", color="blue")
            ax2.axvline(i, color="blue", linewidth=0.8, linestyle="--")
    if div["confirm_date"] in ds:
        ax1.axvline(ds.index(div["confirm_date"]), color="purple", linewidth=0.8, linestyle=":")
    if div.get("ma_date"):
        close = full["close"].astype(float)
        ax1.plot(x, close.rolling(5).mean().to_numpy()[-len(g):], color="tab:orange", linewidth=1, label="MA5")
        ax1.plot(x, close.rolling(10).mean().to_numpy()[-len(g):], color="tab:blue", linewidth=1, label="MA10")
        ax1.axvline(len(g) - 1, color="tab:orange", linewidth=1, linestyle="--")
        ax1.legend(loc="upper left")
    ax1.set_title(title or f"Step 1  D1 MACD {div['kind']} divergence  {div['stock_id']}  confirmed {div['confirm_date']}")
    step = max(1, len(g) // 10)
    ax2.set_xticks(x[::step], [d[5:] for d in ds[::step]])
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)


def chart_intraday(ent: dict, bars: pd.DataFrame, trade: dict | None, title: str, path: Path) -> None:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 5))
    x = np.arange(len(bars))
    ax.plot(x, bars["close"].astype(float), color="black", linewidth=0.9, label="close")
    ax.plot(x, _vwap(bars), color="orange", linewidth=1.2, label="VWAP")
    hh = bars["hhmm"].tolist()
    if ent["signal_time"] in hh:
        i = hh.index(ent["signal_time"])
        ax.axvline(i, color="purple", linestyle=":", linewidth=1)
        ax.annotate(f"cross {ent['signal_time']}", (i, float(bars['close'].iloc[i])), xytext=(5, 10),
                    textcoords="offset points", color="purple", fontsize=9)
    if trade and trade.get("entry_time") in hh:
        e = hh.index(trade["entry_time"])
        ax.plot(e, trade["entry_price"], "^" if ent["side"] == "long" else "v", color="blue", markersize=10)
        sign = 1 if ent["side"] == "long" else -1
        ax.axhline(trade["entry_price"] * (1 + sign * TP / 100), color="tab:red", linestyle="--", linewidth=0.8)
        ax.axhline(trade["entry_price"] * (1 - sign * SL / 100), color="tab:green", linestyle="--", linewidth=0.8)
        if trade.get("exit_time") in hh:
            ax.plot(hh.index(trade["exit_time"]), trade["exit_price"], "X", color="black", markersize=10)
    step = max(1, len(bars) // 12)
    ax.set_xticks(x[::step], hh[::step])
    ax.legend(loc="best")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)


TP, SL = 3.5, 3.0


def main() -> None:
    global TP, SL
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--tp", type=float, default=TP)
    parser.add_argument("--sl", type=float, default=SL)
    parser.add_argument("--chart-dir", default="db/d1_div_vwap_charts")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--min-atr", type=float, default=None, help="e.g. 0.05 = ATR14 >= 5%% on the entry day")
    parser.add_argument("--ma-cross", action="store_true", help="require a D1 MA5/MA10 cross after the divergence")
    args = parser.parse_args()
    TP, SL = args.tp, args.sl
    rules = {**ts.RULES, "take_profit_pct": TP, "stop_loss_pct": SL}

    stock_ids = stock_ids_for_universe("daytrade")
    hist_start = (pd.Timestamp(args.from_date) - pd.Timedelta(days=240)).strftime("%Y-%m-%d")
    day = load_pattern_day(start_date=hist_start, end_date=args.to_date)
    day["stock_id"] = day["stock_id"].astype(str)
    day = day[day["stock_id"].isin(stock_ids)]
    trade_days = sorted(d for d in day["date"].dt.strftime("%Y-%m-%d").unique() if args.from_date <= d <= args.to_date)
    div_from = (pd.Timestamp(args.from_date) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")

    divs = find_divergences(day, div_from, args.to_date)
    print(f"Step 1  D1 背離：{len(divs)} 個（底 {sum(d['kind'] == 'bull' for d in divs)}、頂 {sum(d['kind'] == 'bear' for d in divs)}）", flush=True)
    ma_divs = None
    if args.ma_cross:
        ma_divs = find_ma_crosses(divs, day)
        print(f"Step 2  5 日內 MA5/MA10 同向交叉：{len(ma_divs)} 個（金叉 {sum(d['kind'] == 'bull' for d in ma_divs)}、"
              f"死叉 {sum(d['kind'] == 'bear' for d in ma_divs)}）", flush=True)
    n = 3 if args.ma_cross else 2
    entries = find_entries(ma_divs if args.ma_cross else divs, trade_days, stock_ids, args.min_atr)
    atr_note = f"ATR≥{args.min_atr * 100:g}% 且" if args.min_atr is not None else ""
    print(f"Step {n}  5 日內{atr_note}穿越 VWAP：{len(entries)} 筆（多 {sum(e['side'] == 'long' for e in entries)}、空 {sum(e['side'] == 'short' for e in entries)}）", flush=True)

    trades, bars_cache = [], {}
    for ent in entries:
        key = (ent["stock_id"], ent["date"])
        if key not in bars_cache:
            bars_cache[key] = ts._day_bars(ent["stock_id"], ent["date"])
        trade = ts._simulate(ent, bars_cache[key], True, rules)
        if trade["net_pct"] is not None:
            trades.append({**trade, "date": ent["date"]})
    print(f"Step {n + 1}  完成交易：{len(trades)} 筆", flush=True)

    name = "D1 MACD 背離 → 5 日內 MA5/MA10 交叉 → 5 日內 M1 穿越 VWAP" if args.ma_cross else "D1 MACD 背離 → 5 日內 M1 穿越 VWAP"
    if args.min_atr is not None:
        name = name.replace("M1 穿越 VWAP", f"ATR≥{args.min_atr * 100:g}% 的 M1 穿越 VWAP")
    print(f"\n===== {name}（停利 {TP:g}% / 停損 {SL:g}%，成本 {rules['cost_pct']}%）=====")
    print("| 方向 | 筆數 | 勝率 | 平均 | 合計 | 停利/停損/收盤 |")
    print("|---|---|---|---|---|---|")
    for side, label in (("long", "多"), ("short", "空"), ("all", "多＋空")):
        sel = [t for t in trades if side == "all" or t["side"] == side]
        if not sel:
            continue
        net = [t["net_pct"] for t in sel]
        exits = "/".join(str(sum(t["status"] == k for t in sel)) for k in ("停利", "停損", "收盤平倉"))
        print(f"| {label} | {len(sel)} | {sum(x > 0 for x in net) / len(net) * 100:.1f}% | {sum(net) / len(net):+.3f}% "
              f"| {sum(net):+.1f}% | {exits} |")
    for mode, pick in (("所有訊號都下", lambda x: x), ("一次只拿一筆", one_at_a_time)):
        print(f"\n----- 連勝連輸與加倍（{mode}）-----")
        print("| 方向 | 筆數 | 最多連勝 | 最多連輸 | 連輸4次 | 每筆1單位 | 輸加倍 | 贏加倍 | 連勝1/2/3/4/5+ | 連輸1/2/3/4/5+ |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for side, label in (("long", "多"), ("short", "空"), ("all", "多＋空")):
            sel = [t for t in trades if side == "all" or t["side"] == side]
            if not sel:
                continue
            r = streaks(pick(sel))
            print(f"| {label} | {r['n']} | {r['max_win']} | {r['max_streak']} | {r['busts']} | {r['flat']:+.1f}% "
                  f"| {r['mart']:+.1f}% | {r['anti']:+.1f}% | {'/'.join(map(str, r['dist']['win']))} "
                  f"| {'/'.join(map(str, r['dist']['loss']))} |")

    out = Path(args.chart_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    def sample3(items, key):
        # 3 random picks, with at least one of each direction when both exist.
        groups = {}
        for it in items:
            groups.setdefault(it[key], []).append(it)
        picks = [rng.choice(g) for g in groups.values()][:3]
        rest = [it for it in items if all(it is not p for p in picks)]
        picks += rng.sample(rest, min(3 - len(picks), len(rest)))
        return picks

    print("\n抽樣圖：")
    for i, div in enumerate(sample3(divs, "kind"), 1):
        p = out / f"step1_{i}_{div['stock_id']}_{div['confirm_date']}_{div['kind']}.png"
        chart_divergence(div, day, p)
        print(f"  {p.name}  背離 {div['kind']} 點 {div['d1']} / {div['d2']}")
    for i, div in enumerate(sample3(ma_divs or [], "kind"), 1):
        cross = "golden" if div["kind"] == "bull" else "death"
        p = out / f"step2_{i}_{div['stock_id']}_{div['ma_date']}_{div['kind']}.png"
        chart_divergence(div, day, p, f"Step 2  MA5/MA10 {cross} cross  {div['stock_id']}  {div['ma_date']}  "
                                      f"(MACD {div['kind']} confirmed {div['confirm_date']})")
        print(f"  {p.name}  MA 交叉 {div['ma_date']}（背離確認 {div['confirm_date']}）")
    for i, ent in enumerate(sample3(entries, "side"), 1):
        bars = bars_cache.get((ent["stock_id"], ent["date"]))
        if bars is None or bars.empty:
            continue
        p = out / f"step{n}_{i}_{ent['stock_id']}_{ent['date']}_{ent['side']}.png"
        chart_intraday(ent, bars, None, f"Step {n}  VWAP cross {ent['side']}  {ent['stock_id']}  {ent['date']} "
                                        f"(D1 {ent['kind']} confirmed {ent['confirm_date']})", p)
        print(f"  {p.name}  穿越 {ent['signal_time']}")
    for i, t in enumerate(sample3(trades, "side"), 1):
        bars = bars_cache[(t["stock_id"], t["date"])]
        p = out / f"step{n + 1}_{i}_{t['stock_id']}_{t['date']}_{t['side']}.png"
        chart_intraday(t, bars, t, f"Step {n + 1}  trade {t['side']}  {t['stock_id']}  {t['date']}  "
                                   f"in {t['entry_time']} @{t['entry_price']}  out {t['exit_time']} @{t['exit_price']}  "
                                   f"net {t['net_pct']:+.2f}%", p)
        print(f"  {p.name}  {t['status']} {t['net_pct']:+.2f}%")


if __name__ == "__main__":
    main()
