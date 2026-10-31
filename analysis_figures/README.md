# To reproduce analysis

```bash
pip install -r requirements.txt
python3 build_data.py                # inputs/ -> data/*.csv
python3 resolution_rates.py          # issues resolved per benchmark / scaffold / model     -> outputs/resolution_rates.{csv,md}
python3 exclusive_fixes_venn.py      # resolved only by iCAT / by both / only by the baseline -> outputs/exclusive_fixes_venn.{pdf,png}
python3 significance_tests.py        # exact McNemar test + 95% CI of the paired difference  -> outputs/significance_tests.csv
python3 resolution_by_language.py    # SWE-bench Pro resolution rate per language           -> outputs/resolution_by_language.{pdf,png,csv}
python3 resolution_by_difficulty.py  # resolution rate per difficulty level                  -> outputs/resolution_by_difficulty.{pdf,png,csv}
python3 ablation_results.py          # component ablations on 100 SWE-bench Pro instances    -> outputs/ablation_results.{csv,md}
```

`case_studies/` (trajectories of the case-study instances) is included as is.

## Inputs (`inputs/`)

| file | content |
|---|---|
| `resolution/icat_pro_sonnet45.csv`, `icat_pro_gpt54.csv` | iCAT-Agent, SWE-bench Pro |
| `resolution/icat_verified_minimax.csv`, `icat_verified_gpt5mini.csv` | iCAT-Agent, SWE-bench Verified |
| `resolution/sweagent_pro_sonnet45.csv` | SWE-agent + Claude Sonnet 4.5, Pro |
| `resolution/claudecode_pro_gpt54.csv` | Claude Code + GPT-5.4, Pro |
| `resolution/claudecode_pro_sonnet45.csv` | Claude Code + Claude Sonnet 4.5, Pro (single run) |
| `resolution/minisweagent_verified_{minimax,gpt5mini}.csv` | mini-SWE-agent, Verified |
| `labels/swebench_verified_difficulty.csv` | Verified difficulty: Easy (<15 min), Medium (15 min–1 h), Hard (1–4 h and >4 h) |
| `labels/swebench_pro_language.csv` | language of the code files changed by the gold patch (`corrected_language`) |
| `labels/swebench_pro_file_count.csv` | number of code files changed by the gold patch (1, 2–3, 4+) |
| `ablation/<variant>.jsonl`, `ablation/instances.txt` | per-instance outcome of each ablation variant; the 100-instance subset |
| `ablation/recorded_counts.json` | resolved / regression counts for variant runs without per-instance logs |

## Ablation variants (`ablation_results.py`)

| ID | Setting | input |
|---|---|---|
| A0 | Default | `resolution/icat_pro_sonnet45.csv` |
| A1 | Always Explore | `ablation/always_explore.jsonl` |
| A2 | Never Explore | `ablation/never_explore.jsonl` |
| A3 | Merged Editor + Validator | `ablation/merged.jsonl` |
| A4 | Test Source Exposed | `ablation/test_exposed.jsonl` |
| A5 | Patch Reasoning Exposed | `ablation/patch_exposed.jsonl` |
| A6 | Test + Patch Mutually Exposed | `ablation/mutual.jsonl` |

Regressions = instances resolved by the default but not by the variant. The `n` column gives how many of the
100 instances each variant's result covers.

## Case studies (`case_studies/`)

Each case study is in `case_studies/<case>/` with the iCAT run and the baseline trajectories.
