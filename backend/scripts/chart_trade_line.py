"""Ray 2026-10-16: 「圖的表現，改畫進場跟出場畫一條線就好」 — M1 candles + VWAP + volume, and one line from the entry
to the exit (green when the trade made money, red otherwise). Works for any pick file with columns date, stock_id,
time (signal bar; entry = next open), side, and a stop: --stop col (percent distance, e.g. sl_near) or --stop 3.

    python -m scripts.chart_trade_line --paths X_paths.parquet --pick pick.parquet --out charts/ --stop sl_near --tp 3.5
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def exit_point(o, h, l, c, i, sign, tp, sl):
    entry = o[i]
    tp_p, sl_p = entry * (1 + sign * tp / 100), entry * (1 - sign * sl / 100)
    hit_sl = (l[i:] <= sl_p) if sign > 0 else (h[i:] >= sl_p)
    hit_tp = (h[i:] >= tp_p) if sign > 0 else (l[i:] <= tp_p)
    j_sl = int(np.argmax(hit_sl)) if hit_sl.any() else 10**9
    j_tp = int(np.argmax(hit_tp)) if hit_tp.any() else 10**9
    if j_sl == j_tp == 10**9:
        return len(c) - 1, c[-1], "close"
    if j_sl <= j_tp:
        return i + j_sl, sl_p, "SL"
    return i + j_tp, tp_p, "TP"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", required=True)
    ap.add_argument("--pick", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--stop", default="sl_near", help="column with the stop distance in %, or a number")
    ap.add_argument("--tp", type=float, default=3.5)
    ap.add_argument("--macd", action="store_true", help="add an M1 MACD(12,26,9) histogram panel")
    ap.add_argument("--tp-mult", type=float, default=0, help="take-profit = this x the stop distance (overrides --tp)")
    args = ap.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    matplotlib.rcParams["font.family"] = ["WenQuanYi Zen Hei", "DejaVu Sans"]

    paths = pd.read_parquet(args.paths)
    pk = pd.read_parquet(args.pick)
    for d in (paths, pk):
        d["stock_id"] = d["stock_id"].astype(str)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for n, (_, r) in enumerate(pk.iterrows(), 1):
        d, sid = r["date"], r["stock_id"]
        b = paths[(paths["date"] == d) & (paths["stock_id"] == sid)].sort_values("time").reset_index(drop=True)
        if b.empty or r["time"] not in b["time"].tolist():
            print("no path", sid, d)
            continue
        x = np.arange(len(b))
        o, h, l, c, v = (b[k].to_numpy(float) for k in "ohlcv")
        t = b["time"].tolist().index(r["time"])
        i = t + 1
        sign = 1 if r["side"] == "long" else -1
        sl = float(r[args.stop]) if args.stop in r else float(args.stop)
        sl = max(sl, 0.3)
        tp = args.tp_mult * sl if args.tp_mult else args.tp
        j, px, how = exit_point(o, h, l, c, i, sign, tp, sl)
        ret = sign * (px - o[i]) / o[i] * 100
        if args.macd:
            fig, (ax, av, am) = plt.subplots(3, 1, figsize=(13, 8.5), sharex=True, gridspec_kw={"height_ratios": [3, 1, 1]})
        else:
            fig, (ax, av) = plt.subplots(2, 1, figsize=(13, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
        up = c >= o
        ax.vlines(x, l, h, color="black", linewidth=0.5)
        ax.bar(x, np.abs(c - o).clip(min=1e-6), bottom=np.minimum(o, c), color=np.where(up, "tab:red", "tab:green"), width=0.8)
        ax.plot(x, b["vwap"], color="tab:blue", linewidth=1.0, label="VWAP")
        ax.axhline(float(b["prev_close"].iloc[0]), color="gray", linewidth=0.8, linestyle=":", label="prev close")
        col = "green" if ret > 0 else "red"
        ax.plot([i, j], [o[i], px], color=col, linewidth=2.2, marker="o", markersize=6, zorder=5,
                label=f"{'short' if sign < 0 else 'long'} {b['time'][i]} {o[i]:g} → {b['time'][j]} {px:g} ({how} {ret:+.2f}%, TP {tp:.2f}% SL {sl:.2f}%)")
        av.bar(x, v, color=np.where(up, "tab:red", "tab:green"), width=0.8, alpha=0.7)
        av.set_ylabel("volume")
        if args.macd:
            from scripts.macd_div_study import macd_hist
            hist = macd_hist(c)
            am.bar(x, hist, color=np.where(hist >= 0, "tab:red", "tab:green"), width=0.8)
            am.axhline(0, color="gray", linewidth=0.6)
            am.axvline(t, color="gray", linewidth=0.8, linestyle="--")
            am.set_ylabel("MACD hist")
            if "prior_hi" in r and pd.notna(r["prior_hi"]):
                ax.axhline(float(r["prior_hi"]), color="orange", linewidth=0.8, linestyle="--", label=f"前高 {r['prior_hi']:g}")
                am.set_title(f"前高柱峰 {r['prior_hist']:.3f} → 這波柱峰 {r['cur_hist']:.3f}（{r['div']}）", fontsize=9, loc="left")
        step = max(1, len(b) // 12)
        (am if args.macd else av).set_xticks(x[::step], b["time"].to_numpy()[::step])
        ax.legend(loc="best", fontsize=9)
        ax.set_title(f"{sid} {d}")
        fig.tight_layout()
        tag = r["tag"] if "tag" in r else f"{n:02d}"
        fig.savefig(out / f"{tag}_{n:02d}_{sid}_{d}.png", dpi=80)
        plt.close(fig)
        print("saved", sid, d, how, round(ret, 2))


if __name__ == "__main__":
    main()
