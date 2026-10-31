import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from common import OUT, SYSTEMS, PAIRS, resolved

ICAT, BASE = "#5b6bbf", "#d9926a"
res = resolved()
fig, axes = plt.subplots(1, 4, figsize=(12, 3.0))
for ax, (a, b), tag in zip(axes, PAIRS, "abcd"):
    ids = set(res[a]) | set(res[b])
    only_a = sum(res[a].get(i, False) and not res[b].get(i, False) for i in ids)
    only_b = sum(res[b].get(i, False) and not res[a].get(i, False) for i in ids)
    both = sum(res[a].get(i, False) and res[b].get(i, False) for i in ids)
    na, nb = only_a + both, only_b + both
    ra, rb = (na / 500) ** 0.5, (nb / 500) ** 0.5
    ax.add_patch(Circle((-0.18, 0), ra, color=ICAT, alpha=0.45, lw=0))
    ax.add_patch(Circle((0.18, 0), rb, color=BASE, alpha=0.45, lw=0))
    ax.text(-0.18 - ra + 0.12, 0, str(only_a), ha="center", va="center", color=ICAT, fontsize=12)
    ax.text(0, 0, str(both), ha="center", va="center", fontsize=13)
    ax.text(0.18 + rb - 0.12, 0, str(only_b), ha="center", va="center", color=BASE, fontsize=12)
    ax.text(-0.05, -1.3, SYSTEMS[a][1], color=ICAT, ha="right", fontsize=9, weight="bold")
    ax.text(0.05, -1.3, SYSTEMS[b][1], color=BASE, ha="left", fontsize=9, weight="bold")
    ax.set_title(f"({tag}) {SYSTEMS[a][2]}", y=-0.12, fontsize=10)
    ax.set_xlim(-1.6, 1.6); ax.set_ylim(-1.6, 1.6); ax.set_aspect("equal"); ax.axis("off")
    print(f"({tag}) {SYSTEMS[a][0]} {SYSTEMS[a][2]}: iCAT-only {only_a}, both {both}, {SYSTEMS[b][1]}-only {only_b}")

plt.tight_layout()
for ext in ("pdf", "png"):
    fig.savefig(OUT / f"exclusive_fixes_venn.{ext}", dpi=300, bbox_inches="tight")
