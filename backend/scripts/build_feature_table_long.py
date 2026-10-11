"""Same table as build_feature_table, over any range of months in db/m1.

vwap_activity only starts in 2026-07, so the two filters are computed here
the same way pattern.vwap_activity does:
- ATR14 = mean true range of the 14 days up to yesterday (adjusted D1) / today's open.
- Volume PR = share of the previous 20 days (at least 10) whose 09:00-09:04
  volume is below today's (from M1).

M1 is raw (not adjusted), so the previous close used by the intraday features
is today's first M1 open divided by the adjusted D1 gap.

Stock-days whose D1 change or gap is beyond +-11% are dropped (adjustment
errors; the daily limit is 10%) and counted.

    python -m scripts.build_feature_table_long --from-month 2024-01 --to-month 2024-12 --out db/ft_2024.parquet
    add --check to compare the two filters with vwap_activity where it exists
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

import trade_stats_api as ts
from data.adjustment_query import load_pattern_day
from pattern.vwap_sr_scan import stock_ids_for_universe
from scripts.build_feature_table import d1_context, day_rows
from scripts.d1_div_vwap_report import find_divergences

M1_DIR = Path(__file__).resolve().parent.parent / "db/m1"
ATR_N, LOOKBACK, MIN_HIST = 14, 20, 10
MAX_MOVE = 11.0


def atr_table(day: pd.DataFrame) -> dict[tuple[str, str], tuple[float, float]]:
    """(stock_id, date) -> (ATR14 of the days before / today's open, today's open / yesterday's close)."""
    day = day.sort_values(["stock_id", "date"]).copy()
    g = day.groupby("stock_id", sort=False)
    prev = g["close"].shift(1)
    tr = np.maximum(np.maximum(day["high"] - day["low"], (day["high"] - prev).abs()), (day["low"] - prev).abs())
    atr = tr.groupby(day["stock_id"]).transform(lambda s: s.rolling(ATR_N, min_periods=ATR_N).mean())
    atr_prev = atr.groupby(day["stock_id"]).shift(1)
    ratio = atr_prev / day["open"].replace(0, np.nan)
    gap = day["open"] / prev
    keys = zip(day["stock_id"], day["date"].dt.strftime("%Y-%m-%d"))
    return {k: (float(r), float(gp)) for k, r, gp in zip(keys, ratio, gap) if np.isfinite(r) and np.isfinite(gp)}


def load_m1_month(stem: str, stock_ids: set[str]) -> pd.DataFrame:
    path = M1_DIR / f"{stem}.parquet"
    if not path.exists():
        return pd.DataFrame()
    t = pq.read_table(path, columns=["stock_id", "date", "open", "high", "low", "close", "volume"])
    sid = t["stock_id"].cast("string")
    t = t.set_column(0, "stock_id", sid).filter(pc.is_in(sid, value_set=pa.array(sorted(stock_ids))))
    df = t.to_pandas()
    df["date"] = pd.to_datetime(df["date"], format="mixed")
    if getattr(df["date"].dt, "tz", None) is not None:
        df["date"] = df["date"].dt.tz_localize(None)
    df = df.drop_duplicates(["stock_id", "date"], keep="last")
    df["day"] = df["date"].dt.strftime("%Y-%m-%d")
    df["hhmm"] = df["date"].dt.strftime("%H:%M")
    df = df[(df["hhmm"] >= "09:00") & (df["hhmm"] <= ts.RULES["last_bar"])]
    return df.sort_values(["stock_id", "date"]).reset_index(drop=True)


def months(a: str, b: str) -> list[str]:
    return [p.strftime("%Y_%m") for p in pd.period_range(a, b, freq="M")]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-month", required=True, help="YYYY-MM")
    parser.add_argument("--to-month", required=True, help="YYYY-MM")
    parser.add_argument("--out", required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--tp", type=float, default=3.5, help="take profit %%; 0 = none (hold to the close)")
    args = parser.parse_args()
    rules = {**ts.RULES, "take_profit_pct": args.tp or 1e6, "stop_loss_pct": 3.0}

    stock_ids = stock_ids_for_universe("daytrade")
    first = pd.Timestamp(args.from_month + "-01")
    last = (pd.Period(args.to_month, freq="M").end_time).normalize()
    day = load_pattern_day(start_date=(first - pd.Timedelta(days=240)).strftime("%Y-%m-%d"),
                           end_date=last.strftime("%Y-%m-%d"))
    day["stock_id"] = day["stock_id"].astype(str)
    day = day[day["stock_id"].isin(stock_ids) & (day["open"] > 0)]
    atrs = atr_table(day)
    divs = find_divergences(day, (first - pd.Timedelta(days=15)).strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d"))
    ctx = d1_context(day, divs)
    print(f"股票 {len(stock_ids)} 檔，D1 {len(day)} 列，背離 {len(divs)} 個", flush=True)

    # The month before the range only feeds the 20-day volume history.
    vol_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=LOOKBACK))
    check: list[tuple[str, str, float, float]] = []
    rows, dropped, kept_days = [], 0, 0
    stems = months((first - pd.offsets.MonthBegin(1)).strftime("%Y-%m"), args.to_month)
    for n, stem in enumerate(stems):
        m1 = load_m1_month(stem, stock_ids)
        if m1.empty:
            print(f"{stem}  沒有 M1", flush=True)
            continue
        warmup = n == 0
        n0 = len(rows)
        for d, dg in m1.groupby("day", sort=True):
            for sid, bars in dg.groupby("stock_id", sort=False):
                v5 = float(bars.loc[bars["hhmm"] < "09:05", "volume"].sum())
                past = np.array(vol_hist[sid], dtype=float)
                vol_hist[sid].append(v5)
                if warmup or len(past) < MIN_HIST or v5 <= 0:
                    continue
                pr = float(np.mean(past < v5))
                a = atrs.get((sid, d))
                if a is None or (sid, d) not in ctx:
                    continue
                atr, gap = a
                if args.check:
                    check.append((d, sid, atr, pr))
                if atr < rules["min_day_atr"] or pr < rules["min_vol5_pr"]:
                    continue
                base = ctx[(sid, d)]
                if abs(base["d1_prev_chg"]) > MAX_MOVE or abs(gap - 1) * 100 > MAX_MOVE:
                    dropped += 1
                    continue
                bars = bars.reset_index(drop=True)
                if len(bars) < 30:
                    continue
                kept_days += 1
                base = {"date": d, "stock_id": sid, "day_atr": atr * 100, "vol5_pr": pr * 100,
                        "open5_rng": np.nan, **{**base, "prev_close": float(bars["open"].iloc[0]) / gap}}
                first5 = bars[bars["hhmm"] < "09:05"]
                if not first5.empty:
                    base["open5_rng"] = (first5["high"].max() - first5["low"].min()) / first5["open"].iloc[0] * 100
                rows.extend(day_rows(bars, base, rules))
        print(f"{stem}  +{len(rows) - n0} 列（累計 {len(rows)}，股票日 {kept_days}，剔除異常 {dropped}）", flush=True)

    df = pd.DataFrame(rows)
    float_cols = df.select_dtypes("float64").columns
    df[float_cols] = df[float_cols].astype("float32")
    df.to_parquet(args.out, index=False, compression="zstd")
    print(f"\n完成：{len(df)} 列 × {df.shape[1]} 欄 → {args.out}；股票日 {kept_days}；剔除異常 {dropped}")

    if args.check and check:
        from pattern.vwap_activity import metrics_for_date

        c = pd.DataFrame(check, columns=["date", "stock_id", "atr", "pr"])
        c = c[c["date"] >= "2026-07-01"]
        agree = n_both = 0
        for d, g in c.groupby("date"):
            act = metrics_for_date(d) or {}
            for r in g.itertuples(index=False):
                a = act.get(r.stock_id)
                if not a or a.get("day_atr") is None or a.get("vol5_pr") is None:
                    continue
                n_both += 1
                mine = r.atr >= rules["min_day_atr"] and r.pr >= rules["min_vol5_pr"]
                theirs = a["day_atr"] >= rules["min_day_atr"] and a["vol5_pr"] >= rules["min_vol5_pr"]
                agree += mine == theirs
        print(f"檢查：與 vwap_activity 比對 {n_both} 個股票日，過濾結果一致 {agree / max(n_both, 1) * 100:.1f}%")


if __name__ == "__main__":
    main()
