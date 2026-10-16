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
            # divergence line: price peaks p1 -> p2 and histogram peaks around them
            p1 = p2 = None
            times = b["time"].tolist()
            if "p1_time" in r and isinstance(r["p1_time"], str) and r["p1_time"] in times and r["p2_time"] in times:
                p1, p2 = times.index(r["p1_time"]), times.index(r["p2_time"])
            elif "run_start" in r and r["run_start"] in times:
                rs = times.index(r["run_start"])
                if rs >= 6:
                    p1 = int(np.argmax(h[:rs]))
                    p2 = rs + int(np.argmax(h[rs:t + 1]))
            if p1 is not None and "depth_pct" in r:
                # M top: peak -> trough -> peak, neckline at the trough, break bar marked
                tr = times.index(r["tr_time"]) if "tr_time" in r and r["tr_time"] in times else p1 + int(np.argmin(l[p1:p2 + 1]))
                xs, ys = [p1, tr, p2], [h[p1], l[tr], h[p2]]
                if "l0_time" in r and isinstance(r["l0_time"], str) and r["l0_time"] in times:
                    l0 = times.index(r["l0_time"])
                    xs, ys = [l0] + xs, [l[l0]] + ys
                lab = f"M 頭 {times[p1]} {h[p1]:g} → {times[tr]} {l[tr]:g} → {times[p2]} {h[p2]:g}"
                if "d_time" in r and isinstance(r["d_time"], str) and r["d_time"] in times:
                    # 4th leg, as the D1 detector draws it: H2 -> D (close at the breakdown point)
                    di = times.index(r["d_time"])
                    xs, ys = xs + [di], ys + [float(r["d_price"])]
                    lab += f" → {times[di]} {float(r['d_price']):g}"
                ax.plot(xs, ys, color="orange", linewidth=2.2, marker="o", markersize=6, zorder=6, label=lab)
                ax.axhline(l[tr], color="orange", linewidth=0.9, linestyle=":", label=f"頸線 {l[tr]:g}")
                ax.axvline(t, color="gray", linewidth=0.9, linestyle="--", label=f"{r['brk_label'] if 'brk_label' in r else '跌破 VWAP'} {times[t]}")
                am.axvline(t, color="gray", linewidth=0.8, linestyle="--")
                am.set_title("", loc="left")
                if "q1_time" in r and isinstance(r["q1_time"], str) and r["q1_time"] in times and r["q2_time"] in times:
                    # red-green-red histogram divergence at the neckline break: join the two red-block peaks
                    q1, q2 = times.index(r["q1_time"]), times.index(r["q2_time"])
                    am.plot([q1, q2], [hist[q1], hist[q2]], color="orange", linewidth=1.6, marker="o", markersize=5)
                    am.set_title(f"柱峰 {hist[q1]:.3f} → {hist[q2]:.3f}（紅綠紅頂背離）", fontsize=9, loc="left")
                p1 = None
            if p1 is not None:
                if "q1_time" in r and isinstance(r["q1_time"], str) and r["q1_time"] in times and r["q2_time"] in times:
                    q1, q2 = times.index(r["q1_time"]), times.index(r["q2_time"])
                else:
                    q1 = max(0, p1 - 3) + int(np.argmax(hist[max(0, p1 - 3):p1 + 4]))
                    q2 = max(0, p2 - 3) + int(np.argmax(hist[max(0, p2 - 3):p2 + 4]))
                lab = r["div"] if "div" in r else ""
                ax.plot([p1, p2], [h[p1], h[p2]], color="orange", linewidth=1.6, marker="v", markersize=6,
                        label=f"高點 {times[p1]} {h[p1]:g} → {times[p2]} {h[p2]:g}")
                am.plot([q1, q2], [hist[q1], hist[q2]], color="orange", linewidth=1.6, marker="o", markersize=5)
                am.set_title(f"柱峰 {hist[q1]:.3f} → {hist[q2]:.3f}（{lab}）", fontsize=9, loc="left")
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
