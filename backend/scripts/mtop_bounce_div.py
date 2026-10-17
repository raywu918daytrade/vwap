"""Ray 2026-10-17: 「我指的 MACD 背離是 M 頭之後的反彈的柱狀體」 — compare the red histogram block of the bounce after the
neckline break (the block we enter on) with the red block at the second top (H2). Bounce peak lower = 背離 (weaker bounce).
Reads the saved entry tables from scripts.mtop_wide_stats (--save) and the raw mtopw_* files for the M points.

    python -m scripts.mtop_bounce_div --entries .../mtop_wide_shape3_2024-26.parquet --dir .../mtop/wide --years 2024-2026
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import half
from scripts.macd_div_study import macd_hist
from scripts.mtop_wide_stats import POOLS

LABELS = [("反彈柱峰 < 50% 頭部柱峰", lambda r: r < 0.5), ("反彈柱峰 50%–100%", lambda r: (r >= 0.5) & (r < 1)),
          ("反彈柱峰 ≥ 頭部柱峰（無背離）", lambda r: r >= 1)]


def blocks(hist):
    """[(start, end_inclusive, peak_idx)] for each run of hist > 0."""
    out, s = [], None
    for k, v in enumerate(hist):
        if v > 0 and s is None:
            s = k
        elif v <= 0 and s is not None:
            out.append((s, k - 1, s + int(np.argmax(hist[s:k]))))
            s = None
    if s is not None:
        out.append((s, len(hist) - 1, s + int(np.argmax(hist[s:]))))
    return out


def top_block(bl, p2):
    """The red block at the second top: contains p2, else the last one starting before p2."""
    for b in bl:
        if b[0] <= p2 <= b[1]:
            return b
    before = [b for b in bl if b[0] <= p2]
    return before[-1] if before else None


def load_raw(dir_, y0, y1):
    raws, paths = [], []
    for y in range(y0, y1 + 1):
        for f in sorted(glob.glob(f"{dir_}/mtopw_{y}_p[0-9].parquet")):
            e = pd.read_parquet(f)
            e["stock_id"] = e["stock_id"].astype(str)
            e = e[(e["time"] >= "09:10") & (e["time"] <= "12:00") & (e["hi_pct"] < 9.5)]
            raws.append(e.sort_values(["date", "stock_id", "time"]).groupby(["date", "stock_id"]).head(1))
            p = pd.read_parquet(f.replace(".parquet", "_paths.parquet"))
            p["stock_id"] = p["stock_id"].astype(str)
            paths.append(p)
    return pd.concat(raws), pd.concat(paths)


def annotate(ent: pd.DataFrame, raw: pd.DataFrame, paths: pd.DataFrame) -> pd.DataFrame:
    ent = ent[ent["rr1_m"].notna()].copy()
    ent["stock_id"] = ent["stock_id"].astype(str)
    cols = ["date", "stock_id", "l0_time", "p1_time", "tr_time", "p2_time", "d_time", "d_price", "time"]
    ent = ent.merge(raw[cols].rename(columns={"time": "brk_time"}), on=["date", "stock_id"], how="left")
    g = paths.groupby(["date", "stock_id"])
    q1, q2, ratio = [], [], []
    for _, r in ent.iterrows():
        b = g.get_group((r["date"], r["stock_id"])).sort_values("time").reset_index(drop=True)
        times = b["time"].tolist()
        hist = macd_hist(b["c"].to_numpy(float))
        bl = blocks(hist)
        p2, u = times.index(r["p2_time"]), times.index(r["time"])
        tb = top_block(bl, p2)
        bb = next((x for x in bl if x[0] <= u <= x[1]), None)
        if tb is None or bb is None or tb[2] >= bb[0]:
            q1.append(None), q2.append(None), ratio.append(np.nan)
            continue
        q1.append(times[tb[2]]), q2.append(times[bb[2]]), ratio.append(hist[bb[2]] / hist[tb[2]])
    ent["q1_time"], ent["q2_time"], ent["peak_ratio"] = q1, q2, ratio
    return ent


def line(lab, d, col):
    d = d.dropna(subset=[col])
    if len(d) == 0:
        return f"| {lab} | 0 | | | | | |"
    w, lo = d[d[col] > 0], d[d[col] <= 0]
    hv = d.groupby(half(d["date"]))[col].mean()
    return (f"| {lab} | {len(d)} | {(d[col] > 0).mean() * 100:.0f}% | {w[col].mean():+.2f}% | {lo[col].mean():+.2f}% | "
            f"{d[col].mean():+.3f}% | {(hv > 0).sum()}/{len(hv)} |")


def report(ent: pd.DataFrame, title: str) -> None:
    print(f"## {title}\n")
    print(f"進場 {len(ent)} 筆，{ent['peak_ratio'].isna().sum()} 筆找不到頭部紅柱群（略過）。背離 = 反彈紅柱群的柱峰低於第二個頭的紅柱群柱峰。\n")
    ok = ent[ent["peak_ratio"].notna()]
    for pl, a, p in POOLS:
        d = ok[(ok["day_atr"] >= a) & (ok["vol5_pr"] >= p)]
        print(f"### 池子 {pl}：{len(d)} 筆\n")
        print("| 分組 / 出場 | 筆數 | 勝率 | 平均賺 | 平均賠 | 期望值/筆 | 正半年 |\n|---|---|---|---|---|---|---|")
        groups = [("有背離（反彈柱峰較低）", d[d["peak_ratio"] < 1]), ("無背離（反彈柱峰較高）", d[d["peak_ratio"] >= 1])]
        groups += [(lab, d[fn(d["peak_ratio"])]) for lab, fn in LABELS]
        for gl, gd in groups:
            print(line(f"{gl}：停損 M 頭高點 1 比 1", gd, "rr1_m"))
            print(line(f"{gl}：停損 M 頭高點，抱到收盤", gd, "hold_m"))
        print()
    print("### 有背離依年（原本池子，停損 M 頭高點 1 比 1）\n\n| 年 | 筆數 | 勝率 | 期望值/筆 |\n|---|---|---|---|")
    d = ok[(ok["day_atr"] >= 5) & (ok["vol5_pr"] >= 50) & (ok["peak_ratio"] < 1)]
    for yy, gg in d.groupby(d["date"].str[:4]):
        print(f"| {yy} | {len(gg)} | {(gg['rr1_m'] > 0).mean() * 100:.0f}% | {gg['rr1_m'].mean():+.3f}% |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--years", default="2024-2026")
    ap.add_argument("--title", default="")
    ap.add_argument("--save", default="")
    args = ap.parse_args()
    y0, y1 = (int(x) for x in args.years.split("-"))
    raw, paths = load_raw(args.dir, y0, y1)
    ent = annotate(pd.read_parquet(args.entries), raw, paths)
    if args.save:
        ent.to_parquet(args.save, index=False)
    report(ent, args.title or f"M 頭 → MACD 紅柱轉弱做空，反彈柱狀體背離，{y0}–{y1}，成本 2 跳")


if __name__ == "__main__":
    main()
