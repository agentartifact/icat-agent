import csv, math
from scipy.stats import binomtest
from common import OUT, SYSTEMS, N_INSTANCES, PAIRS, resolved

res = resolved()
rows = []
for a, b in PAIRS:
    bench = SYSTEMS[a][0]; n = N_INSTANCES[bench]
    ids = set(res[a]) | set(res[b])
    only_a = sum(res[a].get(i, False) and not res[b].get(i, False) for i in ids)
    only_b = sum(res[b].get(i, False) and not res[a].get(i, False) for i in ids)
    p = binomtest(only_a, only_a + only_b, 0.5).pvalue
    diff = (only_a - only_b) / n
    se = math.sqrt(only_a + only_b - (only_a - only_b) ** 2 / n) / n
    rows.append(dict(benchmark=bench, model=SYSTEMS[a][2], baseline=SYSTEMS[b][1], icat_only=only_a,
                     baseline_only=only_b, mcnemar_exact_p=f"{p:.2e}", diff_pp=round(100 * diff, 2),
                     ci95_low_pp=round(100 * (diff - 1.96 * se), 2), ci95_high_pp=round(100 * (diff + 1.96 * se), 2)))

with open(OUT / "significance_tests.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

for r in rows:
    print(r)
