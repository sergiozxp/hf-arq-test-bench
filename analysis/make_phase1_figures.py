import json, statistics as st, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
r = json.load(open(sys.argv[1])); out = sys.argv[2]
ARMS = [("VARA HF licensed", "VARA HF licensed", "#2a78d6", "o"),
        ("Mercury 1.9.19", "Mercury 1.9.19", "#eb6834", "s"),
        ("VARA HF unlicensed", "VARA HF unlicensed", "#1baf7a", "^")]
PROF = [("good", "Good\n(+10 dB)", 10), ("moderate", "Moderate\n(+3 dB)", 3), ("poor", "Poor\n(−3 dB)", -3)]
data = {}
for p in r["pairs"]:
    if p.get("series") != "P1" or p["outcome"] not in ("passed", "failed"):
        continue
    k = (p["arm"], p["condition"], p["load"])
    data.setdefault(k, []).append(p)
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": "#52514e",
                     "axes.labelcolor": "#0b0b0b", "xtick.color": "#52514e", "ytick.color": "#52514e",
                     "axes.spines.top": False, "axes.spines.right": False})
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e6e5e1"

def jitter(n, w=0.18):
    return [(-w + 2 * w * i / max(n - 1, 1)) for i in range(n)]

# Figure 1: delivery time per attempt, one panel per load
fig, axes = plt.subplots(1, 2, figsize=(7.0, 3.4), constrained_layout=True)
for ax, (load, title, unit) in zip(axes, [("200B", "200 B", 1), ("10KiB", "10 KiB", 60)]):
    for gi, (cond, label, _snr) in enumerate(PROF):
        for ai, (arm, name, color, mk) in enumerate(ARMS):
            ps = data[(arm, cond, load)]
            ok = sorted(p["delivery_time_s"] / unit for p in ps if p["outcome"] == "passed")
            nfail = sum(p["outcome"] == "failed" for p in ps)
            x0 = gi + (ai - 1) * 0.28
            xs = [x0 + j * 0.25 for j in jitter(len(ok), 0.25)]
            ax.scatter(xs, ok, s=9, marker=mk, facecolor=color, edgecolor="#fcfcfb", linewidth=0.4, zorder=3,
                       label=name if gi == 0 else None)
            m = st.median(ok)
            ax.plot([x0 - 0.11, x0 + 0.11], [m, m], color=INK, linewidth=1.6, zorder=4, solid_capstyle="round")
            if nfail:
                ax.annotate(f"{nfail} failed", (x0, max(ok)), xytext=(0, 6), textcoords="offset points",
                            ha="center", fontsize=7, color=MUTED)
    ax.set_xticks(range(3)); ax.set_xticklabels([l for _c, l, _s in PROF])
    ax.set_ylim(bottom=0)
    ax.set_ylabel("Time to verified ACK (s)" if unit == 1 else "Time to verified ACK (min)")
    ax.set_title(title, fontsize=10, color=INK, loc="left")
    ax.grid(axis="y", color=GRID, linewidth=0.6); ax.set_axisbelow(True)
handles, labels = axes[0].get_legend_handles_labels()
fig.legend(handles, labels, loc="outside upper center", ncol=3, frameon=False, markerscale=1.8)
for ext in ("png", "pdf"):
    fig.savefig(f"{out}/fig_delivery_times.{ext}", dpi=300)
plt.close(fig)

# Figure 2: effective SNR per attempt against the nominal profile SNR
fig, axes = plt.subplots(1, 3, figsize=(7.0, 2.9), constrained_layout=True, sharey=False)
for ax, (cond, label, nominal) in zip(axes, PROF):
    for li, load in enumerate(("200B", "10KiB")):
        for ai, (arm, name, color, mk) in enumerate(ARMS):
            ps = data[(arm, cond, load)]
            v = [((p.get("simulator") or {}).get("effective_snr_db") or {}).get("A->B") for p in ps]
            v = sorted(x for x in v if x is not None)
            x0 = li + (ai - 1) * 0.28
            xs = [x0 + j for j in jitter(len(v), 0.06)]
            ax.scatter(xs, v, s=8, marker=mk, facecolor=color, edgecolor="#fcfcfb", linewidth=0.4, zorder=3,
                       label=name if (cond == "good" and li == 0) else None)
            m = st.median(v)
            ax.plot([x0 - 0.11, x0 + 0.11], [m, m], color=INK, linewidth=1.6, zorder=4)
    ax.axhline(nominal, color=MUTED, linewidth=1, linestyle=(0, (4, 3)), zorder=2)
    ax.annotate("nominal", (1.45, nominal), xytext=(0, 3), textcoords="offset points", ha="right",
                fontsize=7, color=MUTED)
    ax.set_xticks([0, 1]); ax.set_xticklabels(["200 B", "10 KiB"])
    ax.set_xlim(-0.55, 1.55)
    ax.set_title(label.replace("\n", " "), fontsize=10, color=INK, loc="left")
    ax.grid(axis="y", color=GRID, linewidth=0.6); ax.set_axisbelow(True)
axes[0].set_ylabel("Effective SNR, A→B (dB, 3 kHz)")
handles, labels = axes[0].get_legend_handles_labels()
fig.legend(handles, labels, loc="outside upper center", ncol=3, frameon=False, markerscale=1.8)
for ext in ("png", "pdf"):
    fig.savefig(f"{out}/fig_effective_snr.{ext}", dpi=300)
plt.close(fig)

# Per-attempt data behind the figures
with open(f"{out}/phase1_attempts.csv", "w") as f:
    f.write("arm,profile,load,attempt,outcome,delivery_time_s,effective_snr_ab_db,effective_snr_ba_db,buffer0_missing\n")
    for (arm, cond, load), ps in sorted(data.items()):
        for p in sorted(ps, key=lambda p: p.get("attempt") or 0):
            e = (p.get("simulator") or {}).get("effective_snr_db") or {}
            f.write(f"{arm},{cond},{load},{p.get('attempt')},{'success' if p['outcome']=='passed' else 'failure'},"
                    f"{p.get('delivery_time_s') if p['outcome']=='passed' else ''},{e.get('A->B','')},{e.get('B->A','')},"
                    f"{bool(p.get('buffer0_missing'))}\n")
print("done")
