"""One row per (stock, day, M5 close) with the conditions known at that moment
and the result of a long and a short entered on the next M1 open.

Stock-days: daytrade universe with ATR14 >= 5% and first-5-minute volume PR >= 50
(the dashboard filters). Candidate times: the last minute of each M5 bar from
09:09 to 13:19; entry at the next M1 open; exit at +TP% / -SL% (stop wins when
one bar hits both, as in trade_stats_api._simulate), else the 13:24 close.

Every feature only uses data up to and including the candidate minute (D1
features use days before the trade date).

    python -m scripts.build_feature_table --from-date 2026-07-01 --to-date 2026-10-08 --out db/feature_table.parquet
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import trade_stats_api as ts
from data.adjustment_query import load_pattern_day
from pattern.vwap_activity import metrics_for_date as activity_for_date
from pattern.vwap_sr_scan import stock_ids_for_universe
from scripts.d1_div_vwap_report import find_divergences

LAST_ENTRY = "13:20"


def outcome(o, h, l, c, i: int, sign: int, tp: float, sl: float, cost: float) -> tuple[float, int]:
    """Net % and exit code (1 TP, -1 SL, 0 close) for an entry at bar i's open."""
    entry = o[i]
    tp_p, sl_p = entry * (1 + sign * tp / 100), entry * (1 - sign * sl / 100)
    hit_sl = (l[i:] <= sl_p) if sign > 0 else (h[i:] >= sl_p)
    hit_tp = (h[i:] >= tp_p) if sign > 0 else (l[i:] <= tp_p)
    j_sl = int(np.argmax(hit_sl)) if hit_sl.any() else 10**9
    j_tp = int(np.argmax(hit_tp)) if hit_tp.any() else 10**9
    if j_sl == j_tp == 10**9:
        exit_p, code = c[-1], 0
    elif j_sl <= j_tp:
        exit_p, code = sl_p, -1
    else:
        exit_p, code = tp_p, 1
    return sign * (exit_p - entry) / entry * 100 - cost, code


def d1_context(day: pd.DataFrame, divs: list[dict]) -> dict[tuple[str, str], dict]:
    """(stock_id, date) -> D1 features from the days before that date."""
    out = {}
    div_by_stock: dict[str, list[dict]] = {}
    for d in divs:
        div_by_stock.setdefault(d["stock_id"], []).append(d)
    for sid, g in day.groupby("stock_id", sort=False):
        g = g.sort_values("date").reset_index(drop=True)
        dates = g["date"].dt.strftime("%Y-%m-%d").tolist()
        close = g["close"].astype(float)
        ma5, ma10, ma20 = close.rolling(5).mean(), close.rolling(10).mean(), close.rolling(20).mean()
        diff = ma5 - ma10
        sdivs = div_by_stock.get(str(sid), [])
        for k in range(21, len(g)):
            p = k - 1  # the last day known before trading day k
            last_cross = 0
            for j in range(p, max(p - 5, 0), -1):
                if diff[j - 1] <= 0 < diff[j]:
                    last_cross = 1
                    break
                if diff[j - 1] >= 0 > diff[j]:
                    last_cross = -1
                    break
            recent = [x for x in sdivs if dates[max(p - 4, 0)] <= x["confirm_date"] <= dates[p]]
            out[(str(sid), dates[k])] = {
                "d1_prev_chg": (close[p] / close[p - 1] - 1) * 100,
                "d1_prev2_chg": (close[p - 1] / close[p - 2] - 1) * 100,
                "d1_vs_ma20": (close[p] / ma20[p] - 1) * 100,
                "d1_ma5_above_ma10": int(diff[p] > 0),
                "d1_ma_cross_5d": last_cross,
                "d1_div_bull_5d": int(any(x["kind"] == "bull" for x in recent)),
                "d1_div_bear_5d": int(any(x["kind"] == "bear" for x in recent)),
                "prev_close": float(close[p]),
            }
    return out


def day_rows(bars: pd.DataFrame, base: dict, rules: dict) -> list[dict]:
    o, h, l, c = (bars[k].astype(float).to_numpy() for k in ("open", "high", "low", "close"))
    v = bars["volume"].astype(float).to_numpy() if "volume" in bars else np.ones(len(bars))
    hh = bars["hhmm"].tolist()
    mins = np.array([int(x[:2]) * 60 + int(x[3:]) for x in hh])
    bucket = mins // 5
    vwap = np.cumsum(c * v) / np.maximum(np.cumsum(v), 1e-9)
    day_open, prev_close = o[0], base["prev_close"]
    tp, sl, cost = rules["take_profit_pct"], rules["stop_loss_pct"], rules["cost_pct"]
    rows = []
    for i in range(len(bars) - 1):
        if not (hh[i] >= "09:09" and hh[i + 1] <= LAST_ENTRY and bucket[i + 1] != bucket[i]):
            continue
        cur = bucket == bucket[i]
        prev = bucket == bucket[i] - 1
        prev2 = bucket == bucket[i] - 2
        if not prev.any() or i < 5:
            continue
        cur_o, cur_h, cur_l, cur_c = o[cur][0], h[cur].max(), l[cur].min(), c[i]
        p_o, p_h, p_l, p_c = o[prev][0], h[prev].max(), l[prev].min(), c[prev][-1]
        r = np.diff(c[i - 5:i + 1]) / c[i - 5:i] * 100
        early, late = r[:3].mean(), r[3:].mean()
        hi_so_far, lo_so_far = h[:i + 1].max(), l[:i + 1].min()
        cross_up = any(c[k - 1] <= vwap[k - 1] and c[k] > vwap[k] for k in range(max(i - 4, 1), i + 1))
        cross_dn = any(c[k - 1] >= vwap[k - 1] and c[k] < vwap[k] for k in range(max(i - 4, 1), i + 1))
        avg_m5_vol = v[:i + 1].sum() / max(bucket[i] - bucket[0] + 1, 1)
        row = {
            **{k: base[k] for k in base if k != "prev_close"},
            "time": hh[i],
            "minute": int(mins[i] - 540),
            "chg_from_prev_close": (c[i] / prev_close - 1) * 100,
            "gap_open": (day_open / prev_close - 1) * 100,
            "chg_from_open": (c[i] / day_open - 1) * 100,
            "from_day_high": (c[i] / hi_so_far - 1) * 100,
            "from_day_low": (c[i] / lo_so_far - 1) * 100,
            "vs_vwap": (c[i] / vwap[i] - 1) * 100,
            "vwap_cross_up_5m": int(cross_up),
            "vwap_cross_dn_5m": int(cross_dn),
            "m5_ret": (cur_c / cur_o - 1) * 100,
            "m5_range": (cur_h / cur_l - 1) * 100,
            "prev_m5_ret": (p_c / p_o - 1) * 100,
            "prev_m5_red": int(p_c > p_o),
            "prev_m5_black": int(p_c < p_o),
            "break_prev_m5_low": int(cur_c < p_l),
            "break_prev_m5_high": int(cur_c > p_h),
            "m1x5_move": (c[i] / c[i - 5] - 1) * 100,
            "m1x5_late_minus_early": late - early,
            "m5_vol_ratio": v[cur].sum() / max(avg_m5_vol, 1e-9),
            "prev2_m5_red": int(prev2.any() and c[prev2][-1] > o[prev2][0]),
        }
        row["long_net"], row["long_exit"] = outcome(o, h, l, c, i + 1, 1, tp, sl, cost)
        row["short_net"], row["short_exit"] = outcome(o, h, l, c, i + 1, -1, tp, sl, cost)
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--out", default="db/feature_table.parquet")
    args = parser.parse_args()
    rules = {**ts.RULES, "take_profit_pct": 3.5, "stop_loss_pct": 3.0}

    stock_ids = stock_ids_for_universe("daytrade")
    hist_start = (pd.Timestamp(args.from_date) - pd.Timedelta(days=240)).strftime("%Y-%m-%d")
    day = load_pattern_day(start_date=hist_start, end_date=args.to_date)
    day["stock_id"] = day["stock_id"].astype(str)
    day = day[day["stock_id"].isin(stock_ids)]
    divs = find_divergences(day, (pd.Timestamp(args.from_date) - pd.Timedelta(days=15)).strftime("%Y-%m-%d"),
                            args.to_date)
    ctx = d1_context(day, divs)
    trade_days = sorted(d for d in day["date"].dt.strftime("%Y-%m-%d").unique() if args.from_date <= d <= args.to_date)
    print(f"D1 背離 {len(divs)} 個，交易日 {len(trade_days)} 天", flush=True)

    rows = []
    for d in trade_days:
        n0 = len(rows)
        for sid, a in (activity_for_date(d) or {}).items():
            sid = str(sid)
            atr, vol = a.get("day_atr"), a.get("vol5_pr")
            if sid not in stock_ids or atr is None or vol is None:
                continue
            if atr < rules["min_day_atr"] or vol < rules["min_vol5_pr"] or (sid, d) not in ctx:
                continue
            bars = ts._day_bars(sid, d)
            if len(bars) < 30:
                continue
            base = {"date": d, "stock_id": sid, "day_atr": atr * 100, "vol5_pr": vol * 100,
                    "open5_rng": (a.get("open5_rng") or np.nan) * 100, **ctx[(sid, d)]}
            rows.extend(day_rows(bars, base, rules))
        print(f"{d}  +{len(rows) - n0} 列（累計 {len(rows)}）", flush=True)

    df = pd.DataFrame(rows)
    df.to_parquet(args.out, index=False, compression="zstd")
    print(f"\n完成：{len(df)} 列 × {df.shape[1]} 欄 → {args.out}")
    print(f"股票日 {df[['date', 'stock_id']].drop_duplicates().shape[0]}")
    for side in ("long", "short"):
        net = df[f"{side}_net"]
        print(f"{side}: 每個 5 分鐘點都進場 勝率 {(net > 0).mean() * 100:.1f}% 平均 {net.mean():+.3f}%")


if __name__ == "__main__":
    main()
