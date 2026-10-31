import csv
from collections import defaultdict
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from common import OUT, labels, rate, resolved

VER = {"<15 min fix": "Easy", "15 min - 1 hour": "Medium", "1-4 hours": "Hard", ">4 hours": "Hard"}
PANELS = [
    ("(a) MiniMax M2.5 (Verified)", "ver_mini_minimax", "ver_icat_minimax", "verified_difficulty.csv", "difficulty", VER, ["Easy", "Medium", "Hard"]),
    ("(b) GPT-5 mini (Verified)", "ver_mini_gpt5mini", "ver_icat_gpt5mini", "verified_difficulty.csv", "difficulty", VER, ["Easy", "Medium", "Hard"]),
    ("(c) Claude Sonnet 4.5 (Pro)", "pro_sweagent_sonnet", "pro_icat_sonnet", "pro_filecount.csv", "bucket", None, ["1 file", "2-3 files", "4+ files"]),
    ("(d) GPT-5.4 xhigh (Pro)", "pro_cc_gpt54", "pro_icat_gpt54", "pro_filecount.csv", "bucket", None, ["1 file", "2-3 files", "4+ files"]),
]
COL = {"ver_mini": "#f6c79a", "pro_swea": "#a9c8e8", "pro_cc_g": "#eaa5b8", "icat": "#a8d89a"}
NAME = {"ver_mini": "mini-SWE-agent", "pro_swea": "SWE-agent", "pro_cc_g": "Claude Code"}
res = resolved()
rows = []
fig, axes = plt.subplots(1, 4, figsize=(14, 3.2), sharey=True)
for ax, (title, base, icat, lf, key, mapping, order) in zip(axes, PANELS):
    groups = defaultdict(list)
    for i, v in labels(lf, key).items():
        groups[mapping.get(v, v) if mapping else v].append(i)
    for x, bucket in enumerate(order):
        ids = groups[bucket]
        rb, ri = rate(res[base], ids), rate(res[icat], ids)
        rows.append(dict(panel=title, bucket=bucket, n=len(ids), baseline=NAME[base[:8]],
                         baseline_pct=round(rb, 1), icat_pct=round(ri, 1), delta=round(ri - rb, 1)))
        ax.bar(x - .2, rb, .4, color=COL[base[:8]]); ax.bar(x + .2, ri, .4, color=COL["icat"])
        ax.text(x - .2, rb + 1, f"{rb:.1f}", ha="center", fontsize=7)
        ax.text(x + .2, ri + 1, f"{ri:.1f}", ha="center", fontsize=7)
        ax.text(x, max(rb, ri) + 8, f"Δ {ri - rb:+.1f}", ha="center", fontsize=8, color="green", weight="bold")
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels([f"{b}\n(n={len(groups[b])})" for b in order], fontsize=7)
    ax.set_title(title, y=-0.3, fontsize=9); ax.set_ylim(0, 105)
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=COL[base[:8]]), plt.Rectangle((0, 0), 1, 1, color=COL["icat"])],
              labels=[NAME[base[:8]], "iCAT-Agent"], fontsize=7, loc="upper right")

axes[0].set_ylabel("Resolution rate (%)")
plt.tight_layout()
for ext in ("pdf", "png"):
    fig.savefig(OUT / f"resolution_by_difficulty.{ext}", dpi=300, bbox_inches="tight")

with open(OUT / "resolution_by_difficulty.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

for r in rows:
    print(r)
