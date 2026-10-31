import csv, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DATA, IN

DATA.mkdir(exist_ok=True)

SYSTEM_FILES = {
    "pro_icat_sonnet": "icat_pro_sonnet45", "pro_icat_gpt54": "icat_pro_gpt54",
    "pro_sweagent_sonnet": "sweagent_pro_sonnet45", "pro_cc_gpt54": "claudecode_pro_gpt54",
    "pro_cc_sonnet": "claudecode_pro_sonnet45",
    "ver_icat_minimax": "icat_verified_minimax", "ver_icat_gpt5mini": "icat_verified_gpt5mini",
    "ver_mini_minimax": "minisweagent_verified_minimax", "ver_mini_gpt5mini": "minisweagent_verified_gpt5mini",
}
LANG = {"python": "Python", "ts": "TypeScript", "js": "JavaScript", "go": "Go"}


def read(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def write(name, header, rows):
    with open(DATA / name, "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    print(f"data/{name}: {len(rows)} rows")


write("resolved.csv", ["system", "instance_id", "resolved"],
      [(s, r["instance_id"], r["resolved"]) for s, f in SYSTEM_FILES.items() for r in read(IN / "resolution" / f"{f}.csv")])
write("verified_difficulty.csv", ["instance_id", "difficulty"],
      [(r["instance_id"], r["difficulty"]) for r in read(IN / "labels/swebench_verified_difficulty.csv")])
write("pro_language.csv", ["instance_id", "language"],
      [(r["instance_id"], LANG[r["corrected_language"]]) for r in read(IN / "labels/swebench_pro_language.csv")])
write("pro_filecount.csv", ["instance_id", "bucket", "num_code_files"],
      [(r["instance_id"], r["difficulty_bucket_codeonly"], r["num_code_files"]) for r in read(IN / "labels/swebench_pro_file_count.csv")])

ids = [l.strip() for l in open(IN / "ablation/instances.txt") if l.strip()]
write("ablation_instances.csv", ["instance_id"], [(i,) for i in ids])
rows = []
for f in sorted((IN / "ablation").glob("*.jsonl")):
    for l in open(f):
        d = json.loads(l)
        rows.append((d["variant"], d["instance_id"], int(d["resolved"])))

write("ablation.csv", ["variant", "instance_id", "resolved"], rows)
