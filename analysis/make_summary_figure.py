import json, sys, math, statistics as st
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from math import comb
r = json.load(open(sys.argv[1]))
ARMS = [("VARA HF licensed", "VARA HF (licensed)", "#2a78d6"),
        ("Mercury 1.9.19", "Mercury", "#eb6834"),
        ("VARA HF unlicensed", "VARA HF (free)", "#1baf7a")]
PROF = [("good", "Good\nconditions"), ("moderate", "Moderate\nconditions"), ("poor", "Poor\nconditions")]
data = {}
for p in r["pairs"]:
    if p.get("series") == "P1" and p["outcome"] in ("passed", "failed"):
        data.setdefault((p["arm"], p["condition"], p["load"]), []).append(p)

def median_ci(v, conf=0.95):
    """Distribution-free CI for the median from order statistics (binomial)."""
    v = sorted(v); n = len(v)
    cdf = lambda k: sum(comb(n, i) for i in range(k + 1)) / 2 ** n
    lo = max(k for k in range(n) if cdf(k) <= (1 - conf) / 2)       # P(X <= lo) <= 2.5 %
    return v[lo], v[n - 1 - lo]

INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e6e5e1"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.edgecolor": MUTED, "xtick.color": INK, "ytick.color": MUTED})
fig, axes = plt.subplots(1, 2, figsize=(7.2, 4.4))
fig.subplots_adjust(left=0.08, right=0.99, top=0.76, bottom=0.14, wspace=0.22)
panels = [("200B", "A short message (200 bytes)", 1, "seconds"), ("10KiB", "A small file (10 KiB)", 60, "minutes")]
for ax, (load, title, div, unit) in zip(axes, panels):
    w = 0.26
    top = 0
    for gi, (cond, glabel) in enumerate(PROF):
        for ai, (arm, name, color) in enumerate(ARMS):
            ps = data[(arm, cond, load)]
            ok = [p["delivery_time_s"] / div for p in ps if p["outcome"] == "passed"]
            m = st.median(ok); lo, hi = median_ci(ok)
            x = gi + (ai - 1) * w
            ax.bar(x, m, width=w * 0.86, color=color, label=name if gi == 0 else None, zorder=2)
            ax.errorbar(x, m, yerr=[[m - lo], [hi - m]], fmt="none", ecolor=INK, elinewidth=1, capsize=2.5, zorder=3)
            nfail = sum(p["outcome"] == "failed" for p in ps)
            label = f"{m:.0f}" + (f"\n{30 - nfail}/30\narrived" if nfail else "")
            ax.text(x, hi + (0.25 if div == 60 else 1.8), label, ha="center", va="bottom", fontsize=7.2,
                    color=INK, linespacing=1.05)
            top = max(top, hi)
    ax.set_xticks(range(3)); ax.set_xticklabels([g for _c, g in PROF])
    ax.tick_params(axis="x", length=0, pad=4)
    ax.set_ylim(0, top * 1.2)
    ax.set_ylabel(f"Time to deliver and confirm ({unit})")
    ax.set_title(title, loc="left", fontsize=10, color=INK)
    ax.grid(axis="y", color=GRID, linewidth=0.6); ax.set_axisbelow(True)
handles, labels = axes[0].get_legend_handles_labels()
fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(0.06, 0.885), ncol=3, frameon=False)
fig.text(0.01, 0.985, "Lower is better. In poor conditions both VARA versions deliver in about half Mercury's time;\n"
             "for files on a good channel, the VARA licence makes the biggest difference.", fontsize=9.5,
             color=INK, ha="left", va="top")
fig.text(0.01, 0.045, "Bars: median time of the attempts that arrived (30 attempts per modem), lower is better. Lines: 95 % confidence interval of the median.\n"
         "Every attempt arrived, except where a label shows how many of 30 did. Simulated HF channel, 500 Hz modem bandwidth.",
         fontsize=7, color=MUTED, ha="left", va="top")
for ext in ("png", "pdf"):
    fig.savefig(f"fig_summary.{ext}", dpi=300, bbox_inches="tight")
