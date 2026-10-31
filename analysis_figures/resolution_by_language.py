import csv
from collections import defaultdict
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from common import OUT, labels, rate, resolved

LANGS = ["Python", "TypeScript", "JavaScript", "Go"]
PANELS = [("(a) Claude Sonnet 4.5", "pro_sweagent_sonnet", "SWE-agent", "#7f7f7f"),
          ("(b) GPT-5.4 xhigh", "pro_cc_gpt54", "Claude Code", "#c8a27a")]
ICAT = "#5b4fcf"

res = resolved()
groups = defaultdict(list)
for i, l in labels("pro_language.csv", "language").items():
    groups[l].append(i)

rows = []
fig, axes = plt.subplots(1, 2, figsize=(9, 2.6), sharey=True)
for ax, (title, base, bname, bcol) in zip(axes, PANELS):
    icat = base.replace("sweagent", "icat").replace("cc", "icat")
    for y, lang in enumerate(reversed(LANGS)):
        ids = groups[lang]
        rb, ri = rate(res[base], ids), rate(res[icat], ids)
        rows.append(dict(panel=title, language=lang, n=len(ids), baseline=bname, baseline_pct=round(rb, 1),
                         icat_pct=round(ri, 1), delta=round(ri - rb, 1)))
        ax.plot([rb, ri], [y, y], color="#bbbbbb", lw=1.5, zorder=1)
        ax.scatter([rb], [y], color=bcol, zorder=2, s=30); ax.scatter([ri], [y], color=ICAT, zorder=2, s=30)
        ax.text(rb - 1.5, y, f"{rb:.1f}", ha="right", va="center", fontsize=7, color=bcol)
        ax.text(ri + 1.5, y, f"{ri:.1f}", ha="left", va="center", fontsize=7, color=ICAT, weight="bold")
    ax.set_yticks(range(len(LANGS)))
    ax.set_yticklabels([f"{l}\n(n={len(groups[l])})" for l in reversed(LANGS)], fontsize=7)
    ax.set_xlim(15, 95); ax.set_xlabel(f"{title}   [{bname} vs iCAT-Agent]", fontsize=8)
    ax.grid(axis="x", ls=":", alpha=.5)

axes[1].set_xlabel(axes[1].get_xlabel() + "\nResolution rate (%)", fontsize=8)
plt.tight_layout()
for ext in ("pdf", "png"):
    fig.savefig(OUT / f"resolution_by_language.{ext}", dpi=300, bbox_inches="tight")

with open(OUT / "resolution_by_language.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

for r in rows:
    print(r)
