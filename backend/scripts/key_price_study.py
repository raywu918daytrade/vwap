"""D1 關鍵價 + M5 在關鍵價反轉, under the 2026-10-12 backtest standard.

Key levels (scripts.build_key_levels): >= 3 of the last 20 days' highs/lows within ±band%.
Short: an M5 opens below a level, its high reaches the level zone (level ± band), and it closes black
below the level -> short at the next M1 open. Long mirrors it: opens above, low reaches the zone,
closes red above the level.
Standard: ATR>=5, PR>=50, 20d turnover >= 1億, short entry <= +5% / long >= -5% vs prev close, no
limit-up (short) / limit-down (long) touched so far today, 09:10-12:00, cost 0.435% + slippage 0.1%,
first signal per stock-day, one at a time = the market-wide earliest signal each day.

    python -m scripts.key_price_study --tables 'ft_*.parquet' --m5 'm5_hl_*.parquet' --levels key_levels.parquet --chips chips_daily.parquet
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import first_per_day, half
from scripts.d1_rebound_short import SLIP, SPLIT, one_at_a_time, st


def load(args) -> pd.DataFrame:
    cols = ["date", "stock_id", "time", "minute", "gap_open", "day_atr", "vol5_pr", "chg_from_prev_close", "from_day_high",
            "from_day_low", "short_net", "short_hold", "long_net", "long_hold"]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in sorted(glob.glob(args.tables))], ignore_index=True)
    df["stock_id"] = df["stock_id"].astype(str)
    df = df[(df["day_atr"] >= 5) & (df["vol5_pr"] >= 50) & (df["minute"] <= 180)]
    m5 = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.m5))], ignore_index=True)
    m5["stock_id"] = m5["stock_id"].astype(str)
    df = df.merge(m5, on=["date", "stock_id", "time"], how="inner")
    ch = pd.read_parquet(args.chips, columns=["stock_id", "date", "liq_amt20"])
    ch["stock_id"] = ch["stock_id"].astype(str)
    df = df.merge(ch, on=["stock_id", "date"], how="left")
    df = df[df["liq_amt20"].fillna(0) >= 1]
    gap = (df["m5_c_pct"] - df["chg_from_prev_close"]).abs()
    print(f"M5 收盤與特徵表一致（差 <0.05%）：{(gap < 0.05).mean() * 100:.1f}%")
    df["hi_prev"] = ((1 + df["chg_from_prev_close"] / 100) / (1 + df["from_day_high"] / 100) - 1) * 100
    df["lo_prev"] = ((1 + df["chg_from_prev_close"] / 100) / (1 + df["from_day_low"] / 100) - 1) * 100
    for c in ("short_net", "short_hold", "long_net", "long_hold"):
        df[c] = df[c] - SLIP
    return df.reset_index(drop=True)


def strongest(lv: pd.DataFrame, opens: pd.DataFrame, above: bool) -> pd.DataFrame:
    """One level per stock-day: the most-touched level above (short) / below (long) today's open;
    ties go to the level nearest the open."""
    x = lv.merge(opens, on=["date", "stock_id"])
    x = x[x["level_pct"] > x["gap_open"]] if above else x[x["level_pct"] < x["gap_open"]]
    x = x.assign(dist=(x["level_pct"] - x["gap_open"]).abs())
    return x.sort_values(["n_days", "dist"], ascending=[False, True]).groupby(["date", "stock_id", "band"]).head(1)


def signals(df: pd.DataFrame, lv: pd.DataFrame, band: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    x = df.drop(columns=["gap_open"]).merge(lv[lv["band"] == band][["date", "stock_id", "level_pct", "n_days", "n_highs", "n_lows"]],
                 on=["date", "stock_id"], how="inner")
    w = band * (1 + x["level_pct"] / 100)
    L, o, h, l, c = x["level_pct"], x["m5_o_pct"], x["m5_h_pct"], x["m5_l_pct"], x["m5_c_pct"]
    sh = (o < L) & (h >= L - w) & (h <= L + w) & (c < L) & (c < o)
    lg = (o > L) & (l <= L + w) & (l >= L - w) & (c > L) & (c > o)
    s = x[sh & (x["chg_from_prev_close"] <= 5) & (x["hi_prev"] < 9.5)]
    g = x[lg & (x["chg_from_prev_close"] >= -5) & (x["lo_prev"] > -9.5)]
    # One row per M5 bar even if two levels qualify.
    return s.drop_duplicates(["date", "stock_id", "time"]), g.drop_duplicates(["date", "stock_id", "time"])


def row(name: str, s: pd.DataFrame, net: str, hold: str) -> str:
    c = first_per_day(s)
    a, b = c[c["date"] < SPLIT], c[c["date"] >= SPLIT]
    g = b.groupby(half(b["date"]))[net].mean()
    return (f"| {name} | {st(a[net])} | {st(b[net])} | {b[hold].mean() if len(b) else float('nan'):+.3f}% | "
            f"{(g > 0).sum()}/{len(g)} | {one_at_a_time(b, net)} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--m5", required=True)
    ap.add_argument("--levels", required=True)
    ap.add_argument("--chips", required=True)
    ap.add_argument("--strongest", action="store_true", help="only the most-touched level above (short) / below (long) the open")
    ap.add_argument("--picks", default=None, help="write sample short signals (band 1%%) for charts")
    args = ap.parse_args()
    df = load(args)
    lv = pd.read_parquet(args.levels)
    lv["stock_id"] = lv["stock_id"].astype(str)
    print(f"M5 點 {len(df)}，股票日 {df[['date', 'stock_id']].drop_duplicates().shape[0]}\n")
    print("每個範圍：有關鍵價的股票日比例、平均幾條關鍵價")
    sd = df[["date", "stock_id"]].drop_duplicates()
    for b in sorted(lv["band"].unique()):
        k = lv[lv["band"] == b].merge(sd, on=["date", "stock_id"])
        n = k.groupby(["date", "stock_id"]).size()
        print(f"- ±{b:g}%：{len(n) / len(sd) * 100:.0f}% 的股票日有關鍵價，平均 {n.mean():.1f} 條")
    print()
    head = ("| 條件 | 訓練 筆數 | 勝率 | 停利3.5 平均 | 測試 筆數 | 勝率 | 停利3.5 平均 | 測試 收盤 平均 | 測試 正的半年 | "
            "測試一次一筆 筆數 | 平均(停利3.5) | 回落 | 最多連輸 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    short_ok = df[(df["chg_from_prev_close"] <= 5) & (df["hi_prev"] < 9.5)]
    long_ok = df[(df["chg_from_prev_close"] >= -5) & (df["lo_prev"] > -9.5)]
    opens = df[["date", "stock_id", "gap_open"]].drop_duplicates(["date", "stock_id"])
    lv_by_side = {"做空": strongest(lv, opens, True), "做多": strongest(lv, opens, False)} if args.strongest else {"做空": lv, "做多": lv}
    if args.strongest:
        print("只用一條關鍵價：做空 = 開盤價上方碰最多次的那條，做多 = 開盤價下方碰最多次的那條\n")
        for side, l in lv_by_side.items():
            print(f"- {side}：關鍵價離開盤 中位數 {(l['level_pct'] - l['gap_open']).abs().median():.1f}%，碰的天數 中位數 {l['n_days'].median():.0f}")
        print()
    for side, net, hold, ok in (("做空", "short_net", "short_hold", short_ok), ("做多", "long_net", "long_hold", long_ok)):
        print(f"# {side}：M5 在 D1 關鍵價反轉（09:10-12:00，扣成本＋滑價 0.535%）\n")
        print(head)
        print(row("對照：隨便進場", ok, net, hold))
        for b in sorted(lv["band"].unique()):
            s, g = signals(df, lv_by_side[side], b)
            sig = s if side == "做空" else g
            print(row(f"±{b:g}%", sig, net, hold))
            print(row(f"±{b:g}%，關鍵價 ≥4 天", sig[sig["n_days"] >= 4], net, hold))
            if args.strongest:
                print(row(f"±{b:g}%，關鍵價 ≥5 天", sig[sig["n_days"] >= 5], net, hold))
            if args.picks and side == "做空" and b == 1.0:
                c = first_per_day(sig)
                c[c["date"] >= SPLIT].sample(10, random_state=1).to_csv(args.picks, index=False)
        print()


if __name__ == "__main__":
    main()
