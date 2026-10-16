"""Ray 2026-10-16: 「柱體背離，要有紅綠交換，做空是紅綠紅」 — MACD histogram top divergence defined on histogram
colour blocks: the last two positive (red) blocks before bar t, separated by at least one negative (green) bar.
頂背離 = price high during the later red block >= high during the earlier one, AND the later block's histogram peak
< the earlier block's peak. Returns (label, i1, i2, q1, q2) with i = bar of the block's price high, q = bar of its
histogram peak; label in div / nodiv (two blocks, no divergence) / lowerhigh / one (fewer than two red blocks).
"""

import numpy as np
import pandas as pd


def macd_hist(c, fast=12, slow=26, sig=9):
    s = pd.Series(c)
    macd = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    return (macd - macd.ewm(span=sig, adjust=False).mean()).to_numpy()


def red_blocks(hist, t):
    """[(start, end)] of contiguous hist>0 runs within bars 0..t (end inclusive)."""
    out, s = [], None
    for k in range(t + 1):
        if hist[k] > 0:
            if s is None:
                s = k
        elif s is not None:
            out.append((s, k - 1))
            s = None
    if s is not None:
        out.append((s, t))
    return out


def rgr_divergence(h, hist, t, min_len=2):
    blocks = [b for b in red_blocks(hist, t) if b[1] - b[0] + 1 >= min_len]
    if len(blocks) < 2:
        if len(blocks) == 1:
            s, e = blocks[0]
            return "one", -1, s + int(np.argmax(h[s:e + 1])), -1, s + int(np.argmax(hist[s:e + 1]))
        return "none", -1, -1, -1, -1
    (s1, e1), (s2, e2) = blocks[-2], blocks[-1]
    i1, i2 = s1 + int(np.argmax(h[s1:e1 + 1])), s2 + int(np.argmax(h[s2:e2 + 1]))
    q1, q2 = s1 + int(np.argmax(hist[s1:e1 + 1])), s2 + int(np.argmax(hist[s2:e2 + 1]))
    if h[i2] < h[i1]:
        lab = "lowerhigh"
    elif hist[q2] < hist[q1]:
        lab = "div"
    else:
        lab = "nodiv"
    return lab, i1, i2, q1, q2
