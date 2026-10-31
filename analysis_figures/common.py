import csv
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
IN = HERE / "inputs"
DATA = HERE / "data"
OUT = HERE / "outputs"
OUT.mkdir(exist_ok=True)

SYSTEMS = {
    "pro_icat_sonnet":   ("SWE-bench Pro", "iCAT-Agent", "Claude Sonnet 4.5"),
    "pro_sweagent_sonnet": ("SWE-bench Pro", "SWE-agent", "Claude Sonnet 4.5"),
    "pro_cc_sonnet":     ("SWE-bench Pro", "Claude Code", "Claude Sonnet 4.5"),
    "pro_icat_gpt54":    ("SWE-bench Pro", "iCAT-Agent", "GPT-5.4-xhigh"),
    "pro_cc_gpt54":      ("SWE-bench Pro", "Claude Code", "GPT-5.4-xhigh"),
    "ver_icat_minimax":  ("SWE-bench Verified", "iCAT-Agent", "MiniMax M2.5"),
    "ver_mini_minimax":  ("SWE-bench Verified", "mini-SWE-agent", "MiniMax M2.5"),
    "ver_icat_gpt5mini": ("SWE-bench Verified", "iCAT-Agent", "GPT-5-mini"),
    "ver_mini_gpt5mini": ("SWE-bench Verified", "mini-SWE-agent", "GPT-5-mini"),
}
N_INSTANCES = {"SWE-bench Pro": 731, "SWE-bench Verified": 500}

PAIRS = [
    ("ver_icat_minimax", "ver_mini_minimax"),
    ("ver_icat_gpt5mini", "ver_mini_gpt5mini"),
    ("pro_icat_sonnet", "pro_sweagent_sonnet"),
    ("pro_icat_gpt54", "pro_cc_gpt54"),
]


def _read(name):
    with open(DATA / name) as f:
        return list(csv.DictReader(f))


def resolved():
    out = defaultdict(dict)
    for r in _read("resolved.csv"):
        out[r["system"]][r["instance_id"]] = r["resolved"] == "1"
    return out


def labels(name, key):
    return {r["instance_id"]: r[key] for r in _read(name)}


def rate(res, ids):
    ids = list(ids)
    return 100 * sum(res.get(i, False) for i in ids) / len(ids) if ids else float("nan")
