"""Double top (M 頭) on M1, following the textbook definition (Wikipedia, "Double top and double bottom"):
an uptrend, two peaks of about the same height, the valley between them is the neckline, confirmed when price
closes below the neckline; volume should be lower on the second peak.

find_mtops(o, h, l, c, v, hh, ...) -> list of dicts with p1, tr, p2, brk (bar indexes) and features.
  - prior rise: h[p1] at least `rise` % above the lowest low before p1 (and p1 not in the first `skip` bars)
  - p1 is the day high so far; p2 = the highest bar of the second hump, within `tol` % of h[p1], not above it by more than tol
  - at least `gap` bars between peaks; trough depth between `depth_min` and `depth_max` % below the lower peak
  - confirmation = first close below the neckline after p2 (before price exceeds the M high); `brk` is that bar
"""

import numpy as np


def find_mtops(o, h, l, c, v, hh, tol=1.0, depth_min=0.8, depth_max=5.0, gap=6, rise=1.5, skip=5, last_bar="12:00"):
    nb = len(c)
    W = 3
    hi_so_far = np.maximum.accumulate(h)
    sw = [p for p in range(max(W, skip), nb - W) if h[p] >= h[p - W:p + W + 1].max()]
    out, used = [], set()
    for a, p1 in enumerate(sw):
        if h[p1] < hi_so_far[p1] or (h[p1] / l[:p1 + 1].min() - 1) * 100 < rise:
            continue
        for p2 in sw[a + 1:]:
            if p2 - p1 < gap:
                continue
            if h[p2] > h[p1] * (1 + tol / 100):
                break  # a clearly higher high: p1 is not a top
            if (h[p2] / h[p1] - 1) * 100 < -tol:
                continue
            tr = p1 + int(np.argmin(l[p1:p2 + 1]))
            depth = (1 - l[tr] / min(h[p1], h[p2])) * 100
            if depth < depth_min or depth > depth_max or tr == p1 or tr == p2:
                continue
            m_hi = max(h[p1], h[p2])
            brk = None
            for u in range(p2 + 1, nb - 1):
                if hh[u] > last_bar or h[u] > m_hi * (1 + tol / 100):
                    break
                if h[u] > h[p2] and h[u] <= m_hi * (1 + tol / 100):
                    p2, m_hi = u, max(m_hi, h[u])  # the real top of the second hump
                    continue
                if c[u] < l[tr]:
                    brk = u
                    break
            if brk is None or p2 in used:
                continue
            used.add(p2)
            v1 = v[max(0, p1 - 2):p1 + 3].sum()
            v2 = v[max(0, p2 - 2):p2 + 3].sum()
            out.append({"p1": p1, "tr": tr, "p2": p2, "brk": brk, "m_hi": m_hi, "neck": l[tr], "depth_pct": depth,
                        "diff_pct": (h[p2] / h[p1] - 1) * 100, "gap_bars": p2 - p1, "bars_after_p2": brk - p2,
                        "rise_pct": (h[p1] / l[:p1 + 1].min() - 1) * 100, "vol2_vs_1": v2 / max(v1, 1e-9)})
            break
    return out
