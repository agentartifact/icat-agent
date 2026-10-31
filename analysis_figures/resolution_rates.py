import csv
from common import OUT, SYSTEMS, N_INSTANCES, resolved

LEADERBOARD = [("SWE-bench Pro", "mini-SWE-agent", "GPT-5.4-xhigh", 431, 59.10)]
ORDER = ["pro_icat_sonnet", "pro_sweagent_sonnet", "pro_cc_sonnet", "pro_icat_gpt54", "LB", "pro_cc_gpt54",
         "ver_icat_minimax", "ver_mini_minimax", "ver_icat_gpt5mini", "ver_mini_gpt5mini"]
res = resolved()
rows = []
for k in ORDER:
    if k == "LB":
        bench, scaffold, model, n_res, pct = LEADERBOARD[0]
        rows.append(dict(benchmark=bench, scaffold=scaffold, model=model, resolved=n_res,
                         pct=pct))
        continue
    bench, scaffold, model = SYSTEMS[k]
    n_res = sum(res[k].values())
    rows.append(dict(benchmark=bench, scaffold=scaffold, model=model, resolved=n_res,
                     pct=100 * n_res / N_INSTANCES[bench]))

for r in rows:
    if r["scaffold"] == "iCAT-Agent":
        same = [b for b in rows if b["benchmark"] == r["benchmark"] and b["model"] == r["model"]]
        for col, scaffolds in (("delta", ("SWE-agent", "mini-SWE-agent")), ("delta_cc", ("Claude Code",))):
            base = [b["pct"] for b in same if b["scaffold"] in scaffolds]
            r[col] = r["pct"] - base[0] if base else None

with open(OUT / "resolution_rates.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["benchmark", "scaffold", "model", "resolved", "pct", "delta", "delta_cc"])
    w.writeheader(); w.writerows(rows)

lines = ["| Benchmark | Scaffold | Model | # Resolved | % Resolved | vs Claude Code |", "|---|---|---|---|---|---|"]
for r in rows:
    d = f" (+{r['delta']:.1f})" if r.get("delta") is not None else ""
    dc = f"{r['delta_cc']:+.1f}" if r.get("delta_cc") is not None else ""
    lines.append(f"| {r['benchmark']} | {r['scaffold']} | {r['model']} | {r['resolved']} | {r['pct']:.1f}{d} | {dc} |")

open(OUT / "resolution_rates.md", "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
