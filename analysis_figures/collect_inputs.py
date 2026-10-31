import csv, glob, json, os, re, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = HERE.parent
IN = HERE / "inputs"


def write(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    print(f"{path.relative_to(HERE)}: {len(rows)} rows")


def from_csv(path, id_key="instance_id", res_key="resolved"):
    with open(path) as f:
        return [(r[id_key], int(r[res_key].strip().lower() in ("1", "true", "resolved"))) for r in csv.DictReader(f)]


def from_docent(d):
    rows = []
    for f in sorted(glob.glob(f"{d}/*.md")):
        m = re.search(r"^resolved=(\S+)", open(f).read(400), re.M)
        rows.append((os.path.basename(f)[:-3], int(bool(m) and m.group(1).lower() in ("1", "true"))))
    return rows


pro = R / "traj_results_merged/pro/summary"
SOURCES = {
    "icat_pro_sonnet45": from_csv(pro / "results_sonnet45.csv"),
    "icat_pro_gpt54": from_csv(pro / "results_gpt54.csv"),
    "icat_verified_minimax": from_csv(R / "traj_results_merged/verified/minimax/summary.csv"),
    "icat_verified_gpt5mini": from_csv(R / "traj_results_merged/verified/gpt-5-mini/summary.csv"),
    "sweagent_pro_sonnet45": from_csv(R / "scripts/data/sweagent_claudesonnet45.csv", "metadata.instance_id", "metadata.resolved"),
    "claudecode_pro_gpt54": from_csv(R / "scripts/data/cc_gpt54.csv"),
    "claudecode_pro_sonnet45": from_csv(R / "logs_cc_sonnet/results.csv"),
    "minisweagent_verified_minimax": from_docent(R / "analysis/baselines/minisweagent_minimax_verified"),
    "minisweagent_verified_gpt5mini": from_docent(R / "analysis/baselines/minisweagent_gpt5mini_verified"),
}
for name, rows in SOURCES.items():
    write(IN / "resolution" / f"{name}.csv", ["instance_id", "resolved"], sorted(rows))

from datasets import load_dataset

ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
write(IN / "labels/swebench_verified_difficulty.csv", ["instance_id", "difficulty"],
      sorted((r["instance_id"], r["difficulty"]) for r in ds))
(IN / "labels").mkdir(parents=True, exist_ok=True)
shutil.copy2(R / "scripts/language_consistency.csv", IN / "labels/swebench_pro_language.csv")
shutil.copy2(R / "analysis/file_count_corrected.csv", IN / "labels/swebench_pro_file_count.csv")
(IN / "ablation").mkdir(parents=True, exist_ok=True)
for f in sorted(glob.glob(str(R / "logs_ablation/*/results.jsonl"))):
    v = Path(f).parent.name
    if v.startswith("_"):
        continue
    with open(f) as src, open(IN / "ablation" / f"{v}.jsonl", "w") as dst:
        for l in src:
            d = json.loads(l)
            dst.write(json.dumps({"instance_id": d["instance_id"], "variant": v, "resolved": bool(d.get("resolved"))}) + "\n")

ids = sorted({l.strip() for f in sorted(glob.glob(str(R / "logs_ablation/batch*.txt"))) for l in open(f) if l.strip()})
(IN / "ablation/instances.txt").write_text("\n".join(ids) + "\n")
summary = json.loads((R / "logs_ablation/summary_100.json").read_text())
recorded = {}
for v, d in summary.items():
    for half in d.values():
        if isinstance(half, list) and half[-1] == 0:
            recorded[v] = {"n": 50, "resolved": half[0], "regressions": half[2]}

(IN / "ablation/recorded_counts.json").write_text(json.dumps(recorded, indent=1) + "\n")
print("inputs/ablation:", sorted(os.listdir(IN / "ablation")))
