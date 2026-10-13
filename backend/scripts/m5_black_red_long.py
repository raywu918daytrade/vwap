"""Ray 2026-10-13: D1 picks a high-volatility pool, intraday M5 black bar then red bar while the stock is already
up >= 5% from prev close -> long at the next M1 open. Standard rules (long entry >= -5%, no limit-down touched,
09:10-12:00, first signal per stock-day, SL 3%, TP 3.5% / hold to close), cost = 2 ticks, random-long control.

    python -m scripts.m5_black_red_long --ft 'ft_20??.parquet' --d1 d1_raw.parquet --adx d1_adx.parquet \
        --band d1_vwap_band.parquet --chips chips_daily.parquet
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import half
from scripts.d1_rebound_short import SPLIT, st
from scripts.d1_volatility import feats
from scripts.m1vx_direction import tick_cost
from scripts.one_at_a_time import flat_dd, streaks


def one(s: pd.DataFrame) -> str:
    s = s.sort_values(["date", "time", "stock_id"]).groupby("date").head(1)
    r = s["net"].to_numpy(float)
    if len(r) == 0:
        return "0 | - | - | -"
    _, bl, _ = streaks([bool(x) for x in r > 0])
    return f"{len(r)} | {r.mean():+.3f}% | {flat_dd(r):.0f}% | {bl}"


def row(name: str, s: pd.DataFrame) -> str:
    a, b = s[s["date"] < SPLIT], s[s["date"] >= SPLIT]
    g = b.groupby(half(b["date"]))["net"].mean()
    return (f"| {name} | {st(a['net'])} | {st(b['net'])} | {b['hold'].mean() if len(b) else float('nan'):+.3f}% | "
            f"{(g > 0).sum()}/{len(g)} | {one(b)} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    for a in ("--ft", "--d1", "--adx", "--band", "--chips"):
        ap.add_argument(a, required=True)
    ap.add_argument("--gain", type=float, nargs="+", default=[3, 5, 7], help="漲過 X%% thresholds")
    args = ap.parse_args()
    cols = ["date", "stock_id", "time", "minute", "day_atr", "vol5_pr", "chg_from_prev_close", "from_day_low", "from_day_high",
            "vs_vwap", "m5_ret", "prev_m5_ret", "prev_m5_black", "break_prev_m5_high", "m5_vol_ratio", "long_net", "long_hold"]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in sorted(glob.glob(args.ft))], ignore_index=True)
    df = df[(df["day_atr"] >= 5) & (df["vol5_pr"] >= 50) & (df["minute"] >= 10) & (df["minute"] <= 180)]
    df["stock_id"] = df["stock_id"].astype(str)
    d1 = pd.read_parquet(args.d1)
    d1["stock_id"] = d1["stock_id"].astype(str)
    d1["date"] = pd.to_datetime(d1["date"]).dt.strftime("%Y-%m-%d")
    d1 = pd.concat([feats(g) for _, g in d1.groupby("stock_id")], ignore_index=True)
    d1 = d1[["date", "stock_id", "y_rng", "chg20", "y_limit", "vol_ratio"]]
    ax = pd.read_parquet(args.adx, columns=["date", "stock_id", "adx", "brk_up"])
    ax["stock_id"] = ax["stock_id"].astype(str)
    ax["date"] = pd.to_datetime(ax["date"]).dt.strftime("%Y-%m-%d")
    bd = pd.read_parquet(args.band, columns=["date", "stock_id", "z", "prev_close"])
    bd["stock_id"] = bd["stock_id"].astype(str)
    ch = pd.read_parquet(args.chips, columns=["stock_id", "date", "liq_amt20"])
    ch["stock_id"] = ch["stock_id"].astype(str)
    df = df.merge(d1, on=["date", "stock_id"], how="left").merge(ax, on=["date", "stock_id"], how="left")
    df = df.merge(bd, on=["date", "stock_id"], how="inner").merge(ch, on=["date", "stock_id"], how="left")
    df = df[df["liq_amt20"].fillna(0) >= 1].sort_values(["date", "stock_id", "minute"]).reset_index(drop=True)
    px = df["prev_close"].to_numpy(float) * (1 + df["chg_from_prev_close"].to_numpy(float) / 100)
    extra = tick_cost(px) - 0.435
    df["net"], df["hold"] = df["long_net"] - extra, df["long_hold"] - extra
    # limit-down touched at or before this bar (low so far)
    lo = ((1 + df["chg_from_prev_close"] / 100) / (1 + df["from_day_low"] / 100) - 1) * 100
    touched = (lo <= -9.5).astype(int).groupby([df["date"], df["stock_id"]]).cummax().astype(bool)
    tradable = (df["chg_from_prev_close"] >= -5) & ~touched
    pair = (df["prev_m5_black"] == 1) & (df["m5_ret"] > 0)
    pools = {
        "不看 D1（ATR≥5 PR≥50）": pd.Series(True, index=df.index),
        "ATR ≥ 7%": df["day_atr"] >= 7,
        "昨天振幅 ≥ 7%": df["y_rng"] >= 7,
        "昨天漲停": df["y_limit"] == True,
        "20 日漲 ≥ 30%": df["chg20"] >= 30,
        "昨收突破前 20 日高": df["brk_up"] == True,
        "昨收 > D1 VWAP20 +2σ": df["z"] > 2,
        "ATR ≥ 7% 或 昨天振幅 ≥ 10%": (df["day_atr"] >= 7) | (df["y_rng"] >= 10),
    }
    head = ("| D1 池 | 日內條件 | 訓練 筆數 | 勝率 | 停利3.5 平均 | 測試 筆數 | 勝率 | 停利3.5 平均 | 測試 收盤 平均 | 測試 正的半年 | "
            "一次一筆 筆數 | 平均 | 回落 | 最多連輸 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    print("# D1 高波動池 ＋ 日內 M5 先黑後紅、已漲過 X% → 做多（09:10-12:00，每檔每天第一筆，成本 2 跳）\n")
    print(f"成本 2 跳中位數 {np.median(extra + 0.435):.3f}%\n")
    print(head)
    key = [df["date"], df["stock_id"]]
    for pn, pm in pools.items():
        for g in args.gain:
            up = df["chg_from_prev_close"] >= g
            ctl = df[pm & up & tradable].sample(frac=1, random_state=0).groupby(["date", "stock_id"]).head(1)
            print(f"| {pn} | 隨便多，已漲 ≥ {g:g}%（對照） " + row("", ctl)[3:])
            sig = df[pm & up & tradable & pair].groupby(["date", "stock_id"]).head(1)
            print(f"| {pn} | 黑接紅，已漲 ≥ {g:g}% " + row("", sig)[3:])
            if g == 5:
                m2 = pair & (df["break_prev_m5_high"] == 1)
                print(f"| {pn} | 黑接紅且紅收過黑的高點，已漲 ≥ 5% " + row("", df[pm & up & tradable & m2].groupby(["date", "stock_id"]).head(1))[3:])
                m3 = pair & (df["m5_ret"] >= -df["prev_m5_ret"])
                print(f"| {pn} | 黑接紅且紅 ≥ 黑（吞噬），已漲 ≥ 5% " + row("", df[pm & up & tradable & m3].groupby(["date", "stock_id"]).head(1))[3:])
                m4 = pair & (df["vs_vwap"] > 0)
                print(f"| {pn} | 黑接紅且在 VWAP 上方，已漲 ≥ 5% " + row("", df[pm & up & tradable & m4].groupby(["date", "stock_id"]).head(1))[3:])


if __name__ == "__main__":
    main()
