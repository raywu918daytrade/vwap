"""One position at a time for the short model: each day take only the earliest signal across all
stocks (ties: highest score), hold to the 13:24 close or the 3% stop (most trades end at the close,
so one trade per day). Streaks and 1-2-4-8 loss / win doubling as in the project's standard stats.

    python -m scripts.one_at_a_time --tables 'ft_*.parquet' --chips chips_daily.parquet
"""

import argparse
import glob
from collections import Counter

import numpy as np
import pandas as pd

from scripts.chips_model import NOT_FEATURES, half, stat
from scripts.wide_study import VOL_COLS, gbm_scores


def streaks(wins: list[bool]) -> tuple[int, int, Counter]:
    best_w = best_l = cur = 0
    last = None
    dist: Counter = Counter()
    for w in wins + [None]:
        if w == last:
            cur += 1
        else:
            if last is False:
                dist[cur] += 1
            cur = 1
        if w is True:
            best_w = max(best_w, cur)
        elif w is False:
            best_l = max(best_l, cur)
        last = w
    return best_w, best_l, dist


def doubling(rets: np.ndarray, on_loss: bool) -> tuple[float, float]:
    """Units 1-2-4-8: double after a loss (or a win), back to 1 after the 4th step or the other outcome."""
    unit, step, eq, peak, dd = 1, 0, 0.0, 0.0, 0.0
    for r in rets:
        eq += unit * r
        peak, dd = max(peak, eq), max(dd, peak - eq)
        hit = (r <= 0) if on_loss else (r > 0)
        if hit and step < 3:
            unit, step = unit * 2, step + 1
        else:
            unit, step = 1, 0
    return eq, dd


def flat_dd(rets: np.ndarray) -> float:
    eq = np.cumsum(rets)
    return float(np.max(np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--chips", required=True)
    ap.add_argument("--split", default="2023-01-01")
    args = ap.parse_args()
    df = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.tables))], ignore_index=True)
    df["stock_id"] = df["stock_id"].astype(str)
    ch = pd.read_parquet(args.chips, columns=["stock_id", "date", "liq_amt20"])
    df = df.merge(ch.assign(stock_id=ch["stock_id"].astype(str)), on=["stock_id", "date"], how="left")
    base = [c for c in df.columns if c not in NOT_FEATURES and c not in VOL_COLS and c != "liq_amt20"]
    tr = df[(df["date"] < args.split) & (df["day_atr"] >= 5) & (df["vol5_pr"] >= 50)]
    test = df[df["date"] >= args.split].copy()
    p_tr, test["score"] = gbm_scores(tr, test, base)
    n_days = test["date"].nunique()

    filters = [("ATR≥5 PR≥50", 5, 50, 0), ("ATR≥3 PR≥50", 3, 50, 0), ("ATR≥3 PR≥70", 3, 70, 0),
               ("ATR≥3 PR≥70 ≥1億", 3, 70, 1), ("ATR≥3 PR≥90", 3, 90, 0)]
    lines = ["| 門檻 | 切點 | 交易天數 | 勝率 | 平均 | 合計 | 最大回落 | 最多連贏 | 最多連輸 | 輸加碼1-2-4-8 合計 / 回落 | 贏加碼1-2-4-8 合計 / 回落 | 正的半年 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    detail = []
    for name, atr, pr, liq in filters:
        for q in (0.05, 0.02):
            thr = np.quantile(p_tr, 1 - q)
            s = test[(test["score"] >= thr) & (test["day_atr"] >= atr) & (test["vol5_pr"] >= pr)
                     & (test["liq_amt20"].fillna(0) >= liq)]
            s = s.sort_values(["date", "minute", "score"], ascending=[True, True, False]).groupby("date").head(1)
            r = s["short_hold"].to_numpy(float)
            bw, bl, dist = streaks([bool(x) for x in r > 0])
            ld, ldd = doubling(r, True)
            wd, wdd = doubling(r, False)
            hh = s.groupby(half(s["date"]))["short_hold"].sum()
            lines.append(f"| {name} | 前{q * 100:g}% | {len(s)}/{n_days} | {(r > 0).mean() * 100:.1f}% | {r.mean():+.3f}% | "
                         f"{r.sum():+.1f}% | {flat_dd(r):.1f}% | {bw} | {bl} | {ld:+.1f}% / {ldd:.1f}% | {wd:+.1f}% / {wdd:.1f}% | {(hh > 0).sum()}/{len(hh)} |")
            detail.append((f"{name} 前{q * 100:g}%", s, dist, hh))

    print("# 一次一筆（做空，2023-26 測試，每天只做全市場最早的一個訊號，收盤平倉、停損 3%）\n")
    print("合計 = 每筆報酬相加（每筆同金額）；最大回落 = 累計報酬從高點往下最多掉多少。\n")
    print("\n".join(lines))
    for title, s, dist, hh in detail:
        print(f"\n## {title}\n")
        print("- 連輸分布：" + "、".join(f"{k} 連輸 {v} 次" for k, v in sorted(dist.items())))
        print("- 每半年合計：" + "、".join(f"{h} {v:+.1f}%" for h, v in hh.items()))
        print(f"- 進場時間：09:20 前 {(s['minute'] < 20).mean() * 100:.0f}%，10:00 前 {(s['minute'] < 60).mean() * 100:.0f}%")


if __name__ == "__main__":
    main()
