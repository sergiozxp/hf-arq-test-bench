import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
INK, MUTED, BLUE, ORANGE = "#0b0b0b", "#52514e", "#2a78d6", "#eb6834"
fig, ax = plt.subplots(figsize=(7.0, 3.6)); ax.set_xlim(0, 100); ax.set_ylim(-4, 50); ax.axis("off")
def box(x, y, w, h, title, sub="", fill="#f0efec", top=True):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.2", fc=fill, ec=MUTED, lw=0.9))
    if top:
        ax.text(x + 1.5, y + h - 1.5, title, ha="left", va="top", fontsize=9, color=INK, weight="bold")
    else:
        ax.text(x + w/2, y + h/2 + (1.6 if sub else 0), title, ha="center", va="center", fontsize=8.5, color=INK, weight="bold")
        if sub: ax.text(x + w/2, y + h/2 - 2.2, sub, ha="center", va="center", fontsize=7, color=MUTED)
box(2, 10, 30, 36, "PC A")
box(40, 10, 58, 36, "PC B")
box(5, 26, 24, 12, "Sending modem", "Mercury or VARA + client", "#ffffff", top=False)
box(44, 26, 24, 12, "Receiving modem", "Mercury or VARA + client", "#ffffff", top=False)
box(71, 26, 24, 12, "Channel simulator", "Watterson + noise, seeded", "#ffffff", top=False)
box(9, 11.5, 16, 8, "UCA-A", "", "#ffffff", top=False)
box(48, 11.5, 16, 8, "UCA-B", "", "#ffffff", top=False)
box(75, 11.5, 16, 8, "UCA-S", "L: A→B  R: B→A", "#ffffff", top=False)
for x in (17, 56, 83):   # software to interface
    ax.plot([x, x], [19.5, 26], color=MUTED, lw=0.8, ls=(0, (2, 2)))
def path(pts, color, label, lx, ly, ha="center"):
    xs, ys = zip(*pts)
    ax.plot(xs[:-1] + (xs[-1],), ys, color=color, lw=1.5, solid_capstyle="round")
    ax.annotate("", xy=pts[-1], xytext=pts[-2], arrowprops=dict(arrowstyle="-|>", color=color, lw=1.5, shrinkA=0, shrinkB=0))
    ax.text(lx, ly, label, fontsize=7, color=INK, ha=ha, va="center")
# 1: UCA-A out L -> UCA-S in L, lane y=6
path([(13, 11.5), (13, 6), (79, 6), (79, 11.5)], BLUE, "1  UCA-A out L → UCA-S in L", 46, 7.6)
# 4: UCA-S out R -> UCA-A in L, lane y=1
path([(87, 11.5), (87, 1), (21, 1), (21, 11.5)], ORANGE, "4  UCA-S out R → UCA-A in L", 54, 2.6)
# 2: UCA-S out L -> UCA-B in L
path([(75, 17.5), (64, 17.5)], BLUE, "2", 69.5, 19.0)
# 3: UCA-B out L -> UCA-S in R
path([(64, 13.5), (75, 13.5)], ORANGE, "3", 69.5, 15.0)
ax.plot([4, 9], [-2.8, -2.8], color=BLUE, lw=1.5); ax.text(10, -2.8, "A→B audio", fontsize=7.5, color=INK, va="center")
ax.plot([28, 33], [-2.8, -2.8], color=ORANGE, lw=1.5); ax.text(34, -2.8, "B→A audio", fontsize=7.5, color=INK, va="center")
ax.text(98, -2.8, "Four single-conductor RCA cables; no RF stage", fontsize=7.5, color=MUTED, ha="right", va="center")
for ext in ("png", "pdf"): fig.savefig(f"fig_bench.{ext}", dpi=300, bbox_inches="tight")
