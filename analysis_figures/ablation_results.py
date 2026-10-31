import csv, json
from collections import defaultdict
from common import DATA, IN, OUT, resolved

VARIANTS = [("A1", "Always Explore", "always_explore"), ("A2", "Never Explore", "never_explore"),
            ("A3", "Merged Editor + Validator", "merged"), ("A4", "Test Source Exposed", "test_exposed"),
            ("A5", "Patch Reasoning Exposed", "patch_exposed"), ("A6", "Test + Patch Mutually Exposed", "mutual")]
default = resolved()["pro_icat_sonnet"]
ids = sorted(r["instance_id"] for r in csv.DictReader(open(DATA / "ablation_instances.csv")))
abl = defaultdict(dict)
for r in csv.DictReader(open(DATA / "ablation.csv")):
    abl[r["variant"]][r["instance_id"]] = r["resolved"] == "1"

recorded = json.load(open(IN / "ablation" / "recorded_counts.json"))

rows = [dict(id="A0", setting="Default", n=len(ids), resolved=sum(default.get(i, False) for i in ids),
             delta=0, regressions=0, note="archive run")]
for aid, name, v in VARIANTS:
    run = abl.get(v, {})
    got = [i for i in ids if i in run]
    n_res = sum(run[i] for i in got)
    regr = sum(default.get(i, False) and not run[i] for i in got)
    note = f"{len(got)} instances run here"
    n = len(got)
    rec = recorded.get(v)
    if n < len(ids) and rec:
        n_res += rec["resolved"]; regr += rec["regressions"]; n += rec["n"]
        note += f"; other {rec['n']} from recorded counts (no per-instance logs)"
    d_res = sum(default.get(i, False) for i in (ids if n == len(ids) else got))
    rows.append(dict(id=aid, setting=name, n=n, resolved=n_res, delta=n_res - d_res, regressions=regr, note=note))

with open(OUT / "ablation_results.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

lines = ["| ID | Setting | n | Res. | Δ vs default (same n) | Regr. | Note |", "|---|---|---|---|---|---|---|"]
for r in rows:
    lines.append(f"| {r['id']} | {r['setting']} | {r['n']} | {r['resolved']} | {r['delta']} | {r['regressions']} | {r['note']} |")

open(OUT / "ablation_results.md", "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
