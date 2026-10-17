"""Ray 2026-10-17: M 頭 5 個點的對稱檢查（方法 B）。
Unit = mean high-low of the 3 bars around each point. Conditions, all <= n units:
  tops: |H1 - H2|; lows: |L0 - L|, |L - D|, |L0 - D| (D = breakdown close point);
  width: |(L - L0) - (D - L)| in bars (left half vs right half).
Reads scripts.m1_mtop_d1_scan outputs + paths, prints how many pass for each n, and writes pick files for charts.

    python -m scripts.mtop_shape --dir .../mtop/wide --years 2024-2026 --ns 2,3,4,5 --pick-dir .../mtop/wide/shape
"""

import argparse
import glob
import os

import numpy as np
import pandas as pd


def unit(h, l, i):
    a, b = max(0, i - 1), min(len(h), i + 2)
    return float((h[a:b] - l[a:b]).mean())


def shape_metrics(b: pd.DataFrame, r: pd.Series) -> dict:
    times = b["time"].tolist()
    h, l = b["h"].to_numpy(float), b["l"].to_numpy(float)
    i0, i1, iL, i2, iD = (times.index(r[k]) for k in ("l0_time", "p1_time", "tr_time", "p2_time", "d_time"))
    L0, H1, L, H2, D = l[i0], h[i1], l[iL], h[i2], float(r["d_price"])
    u_top = (unit(h, l, i1) + unit(h, l, i2)) / 2
    u_low = (unit(h, l, i0) + unit(h, l, iL) + unit(h, l, iD)) / 3
    if u_top <= 0 or u_low <= 0:
        return {}
    return {"top_u": abs(H1 - H2) / u_top, "low01_u": abs(L0 - L) / u_low, "low12_u": abs(L - D) / u_low,
            "low02_u": abs(L0 - D) / u_low, "width_bars": abs((iL - i0) - (iD - iL)), "left_w": iL - i0, "right_w": iD - iL,
            "unit_pct": u_low / L * 100}


def passes(m: dict, n: float) -> bool:
    return bool(m) and m["top_u"] <= n and m["low01_u"] <= n and m["low12_u"] <= n and m["low02_u"] <= n and m["width_bars"] <= n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--glob", default="mtopw_{y}_p[0-9].parquet")
    ap.add_argument("--years", default="2024-2026")
    ap.add_argument("--ns", default="2,3,4,5")
    ap.add_argument("--pick-dir", default="")
    ap.add_argument("--per", type=int, default=12)
    ap.add_argument("--orig-pool", action="store_true", help="only ATR>=5 PR>=50 stock-days")
    args = ap.parse_args()
    y0, y1 = (int(x) for x in args.years.split("-"))
    ns = [float(x) for x in args.ns.split(",")]
    rows, paths_all = [], []
    for y in range(y0, y1 + 1):
        for f in sorted(glob.glob(f"{args.dir}/" + args.glob.format(y=y))):
            e = pd.read_parquet(f)
            p = pd.read_parquet(f.replace(".parquet", "_paths.parquet"))
            e["stock_id"] = e["stock_id"].astype(str)
            p["stock_id"] = p["stock_id"].astype(str)
            if args.orig_pool and "day_atr" in e.columns:
                e = e[(e["day_atr"] >= 5) & (e["vol5_pr"] >= 50)]
            e = e.sort_values(["date", "stock_id", "time"]).groupby(["date", "stock_id"]).head(1)
            g = p.groupby(["date", "stock_id"])
            for _, r in e.iterrows():
                key = (r["date"], r["stock_id"])
                if key not in g.groups:
                    continue
                b = g.get_group(key).sort_values("time").reset_index(drop=True)
                m = shape_metrics(b, r)
                rec = {**r.to_dict(), **m}
                for n in ns:
                    rec[f"pass_{n:g}"] = passes(m, n)
                rows.append(rec)
            paths_all.append(p)
    df = pd.DataFrame(rows)
    print(f"{len(df)} M 頭（有 M1 路徑），{y0}–{y1}" + ("，原本池子" if args.orig_pool else ""))
    print(df[["top_u", "low01_u", "low12_u", "low02_u", "width_bars", "unit_pct"]].describe().round(2).to_string())
    print("\n| n | 通過 | 比例 |\n|---|---|---|")
    for n in ns:
        print(f"| {n:g} | {df[f'pass_{n:g}'].sum()} | {df[f'pass_{n:g}'].mean() * 100:.0f}% |")
    if args.pick_dir:
        os.makedirs(args.pick_dir, exist_ok=True)
        df.to_parquet(os.path.join(args.pick_dir, "shape_all.parquet"), index=False)
        paths = pd.concat(paths_all, ignore_index=True)
        prev = None
        for n in ns:
            d = df[df[f"pass_{n:g}"]]
            if prev is not None:
                d = d[~d[f"pass_{prev:g}"]]  # only the extra ones this looser n lets in
            d = d.sort_values("date", ascending=False).head(args.per).iloc[::-1].copy()
            d["tag"] = f"n{n:g}"
            d["div"] = "M 頭"
            d["brk_label"] = "跌破頸線"
            d["q1_time"] = ""
            d["q2_time"] = ""
            d.to_parquet(os.path.join(args.pick_dir, f"pick_n{n:g}.parquet"), index=False)
            keys = set(zip(d["date"], d["stock_id"]))
            paths[paths.set_index(["date", "stock_id"]).index.isin(keys)].to_parquet(os.path.join(args.pick_dir, f"pick_n{n:g}_paths.parquet"), index=False)
            prev = n
        print("picks written")


if __name__ == "__main__":
    main()
