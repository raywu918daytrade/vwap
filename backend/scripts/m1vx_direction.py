"""Split the VWAP ±2σ-then-cross signals (scripts.m1_vwap_band --mode cross) by the direction at entry
(Ray 2026-10-13: 是不是太常在沒有方向的時候進場), with a random-entry control in the same bins.

    python -m scripts.m1vx_direction --signals 'm1vx_20??.parquet' --ft 'ft_20??.parquet' --d1 d1_vwap_band.parquet --chips chips_daily.parquet
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import half
from scripts.d1_rebound_short import SPLIT

TEST_MIN, TEST_MAX = 10, 180  # 09:10-12:00


def tick_cost(px: np.ndarray) -> np.ndarray:
    tick = np.select([px < 10, px < 50, px < 100, px < 500, px < 1000], [0.01, 0.05, 0.1, 0.5, 1.0], 5.0)
    return 2 * tick / px * 100


def stat(s: pd.DataFrame, col: str) -> str:
    if len(s) < 30:
        return f"{len(s)} | - | - | -"
    g = s.groupby(half(s["date"]))[col].mean()
    r = s[col]
    return f"{len(s)} | {(r > 0).mean() * 100:.1f}% | {r.mean():+.3f}% | {(g > 0).sum()}/{len(g)}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", required=True)
    ap.add_argument("--ft", required=True)
    ap.add_argument("--d1", required=True)
    ap.add_argument("--chips", required=True)
    ap.add_argument("--adx", help="scripts.d1_adx output: adds ADX / DI / 20-day breakout bins")
    args = ap.parse_args()
    cols = ["date", "stock_id", "minute", "time", "chg_from_prev_close", "gap_open", "day_atr", "vol5_pr",
            "d1_prev_chg", "d1_vs_ma20", "from_day_high", "from_day_low", "short_net", "long_net", "short_hold", "long_hold"]
    ft = pd.concat([pd.read_parquet(p, columns=cols) for p in sorted(glob.glob(args.ft))], ignore_index=True)
    ft = ft[(ft["day_atr"] >= 5) & (ft["vol5_pr"] >= 50) & (ft["date"] >= SPLIT)]
    ft["stock_id"] = ft["stock_id"].astype(str)
    d1 = pd.read_parquet(args.d1, columns=["date", "stock_id", "z", "prev_close"])
    d1["stock_id"] = d1["stock_id"].astype(str)
    ch = pd.read_parquet(args.chips, columns=["stock_id", "date", "liq_amt20"])
    ch["stock_id"] = ch["stock_id"].astype(str)
    day = ft.groupby(["date", "stock_id"], as_index=False).agg(gap_open=("gap_open", "first"), day_atr=("day_atr", "first"), d1_prev_chg=("d1_prev_chg", "first"),
                                                               d1_vs_ma20=("d1_vs_ma20", "first"), day_hi=("from_day_high", "max"))
    day = day.merge(d1, on=["date", "stock_id"]).merge(ch, on=["date", "stock_id"])
    day = day[day["liq_amt20"].fillna(0) >= 1]
    if args.adx:
        ax = pd.read_parquet(args.adx)
        ax["stock_id"] = ax["stock_id"].astype(str)
        ax["date"] = pd.to_datetime(ax["date"]).dt.strftime("%Y-%m-%d")
        day = day.merge(ax, on=["date", "stock_id"], how="left")

    sig = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.signals))], ignore_index=True)
    sig["stock_id"] = sig["stock_id"].astype(str)
    sig = sig[sig["date"] >= SPLIT].merge(day.drop(columns=["z"]) if "z" in sig else day, on=["date", "stock_id"])
    sig["net"] -= tick_cost(sig["entry_price"].to_numpy(float)) - 0.435
    sig["hold"] -= tick_cost(sig["entry_price"].to_numpy(float)) - 0.435

    # random control: one random minute in the window per stock-day
    ctl = ft[(ft["minute"] >= TEST_MIN) & (ft["minute"] <= TEST_MAX)].sample(frac=1, random_state=0)
    ctl = ctl.groupby(["date", "stock_id"]).head(1).merge(day.drop(columns=["gap_open", "d1_prev_chg", "d1_vs_ma20", "day_hi", "day_atr"]), on=["date", "stock_id"])
    ctl["entry_pct"] = ctl["chg_from_prev_close"]
    px = ctl["prev_close"].to_numpy(float) * (1 + ctl["entry_pct"].to_numpy(float) / 100)
    ctl["day_hi"] = ctl.groupby(["date", "stock_id"])["from_day_high"].transform("max")
    for side in ("short", "long"):
        ctl[f"{side}_net"] -= tick_cost(px) - 0.435

    def bins(s: pd.DataFrame) -> list[tuple[str, pd.Series]]:
        vs_open = s["entry_pct"] - s["gap_open"]
        return [
            ("當天漲跌 < −3%", s["entry_pct"] < -3), ("當天漲跌 −3～0%", (s["entry_pct"] >= -3) & (s["entry_pct"] < 0)),
            ("當天漲跌 0～3%", (s["entry_pct"] >= 0) & (s["entry_pct"] < 3)), ("當天漲跌 3～5%", (s["entry_pct"] >= 3) & (s["entry_pct"] <= 5)),
            ("當天漲跌 > 5%", s["entry_pct"] > 5),
            ("比開盤低 2% 以上", vs_open <= -2), ("比開盤 ±2% 內", vs_open.abs() < 2), ("比開盤高 2% 以上", vs_open >= 2),
            ("開高 ≥ 3%", s["gap_open"] >= 3), ("開平 ±1%", s["gap_open"].abs() < 1), ("開低 ≤ −3%", s["gap_open"] <= -3),
            ("昨天漲 ≥ 5%", s["d1_prev_chg"] >= 5), ("昨天跌 ≤ −5%", s["d1_prev_chg"] <= -5),
            ("昨收在 MA20 上 10% 以上", s["d1_vs_ma20"] >= 10), ("昨收在 MA20 下方", s["d1_vs_ma20"] < 0),
        ] + ([] if "adx" not in s else [
            ("ATR 5～7%", (s["day_atr"] >= 5) & (s["day_atr"] < 7)), ("ATR 7～10%", (s["day_atr"] >= 7) & (s["day_atr"] < 10)), ("ATR ≥ 10%", s["day_atr"] >= 10),
            ("ADX < 20（沒趨勢）", s["adx"] < 20), ("ADX 20～30", (s["adx"] >= 20) & (s["adx"] < 30)), ("ADX ≥ 30（強趨勢）", s["adx"] >= 30),
            ("ADX ≥ 25 且 −DI > +DI（空頭）", (s["adx"] >= 25) & (s["mdi"] > s["pdi"])), ("ADX ≥ 25 且 +DI > −DI（多頭）", (s["adx"] >= 25) & (s["pdi"] > s["mdi"])),
            ("ADX ≥ 25 空頭 且 當天 ≤ 0%", (s["adx"] >= 25) & (s["mdi"] > s["pdi"]) & (s["entry_pct"] <= 0)),
            ("昨收突破前 20 日高", s["brk_up"] == True), ("昨收跌破前 20 日低", s["brk_dn"] == True),
            ("跌破前 20 日低 且 ADX ≥ 25", (s["brk_dn"] == True) & (s["adx"] >= 25)),
        ])

    for side in ("short", "long"):
        s = sig[sig["side"] == side]
        s = s[(s["entry_pct"] <= 5) & (s["hi_pct"] < 9.5)] if side == "short" else s[(s["entry_pct"] >= -5) & (s["lo_pct"] > -9.5)]
        s = s.sort_values(["date", "stock_id", "time"]).groupby(["date", "stock_id"]).head(1)
        c = ctl[(ctl["entry_pct"] <= 5) & (ctl["day_hi"] < 9.5)] if side == "short" else ctl[(ctl["entry_pct"] >= -5) & (ctl["from_day_low"] > -9.5)]
        c = c.rename(columns={f"{side}_net": "net", f"{side}_hold": "hold"})
        print(f"# {'做空' if side == 'short' else '做多'}：碰 ±2σ 後穿越 VWAP，依進場時的方向分組（2023–26，成本 2 跳）\n")
        print("| 進場時 | 訊號 筆數 | 勝率 | 平均 | 正半年 | 隨便進 筆數 | 勝率 | 平均 | 正半年 |\n|---|---|---|---|---|---|---|---|---|")
        print(f"| 全部 | {stat(s, 'net')} | {stat(c, 'net')} |")
        for (n, m), (_, mc) in zip(bins(s), bins(c)):
            print(f"| {n} | {stat(s[m], 'net')} | {stat(c[mc], 'net')} |")
        print()


if __name__ == "__main__":
    main()
