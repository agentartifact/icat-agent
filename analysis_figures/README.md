# Evaluation analysis (iCAT-Agent)

Self-contained: everything the analysis needs is in `inputs/`, so this directory alone reproduces the results.

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

| file | content | source in the icat-agent repo |
|---|---|---|
| `resolution/icat_pro_sonnet45.csv`, `icat_pro_gpt54.csv` | iCAT-Agent, SWE-bench Pro | `traj_results_merged/pro/summary/results_{sonnet45,gpt54}.csv` |
| `resolution/icat_verified_minimax.csv`, `icat_verified_gpt5mini.csv` | iCAT-Agent, SWE-bench Verified | `traj_results_merged/verified/{minimax,gpt-5-mini}/summary.csv` |
| `resolution/sweagent_pro_sonnet45.csv` | SWE-agent + Claude Sonnet 4.5, Pro | `scripts/data/sweagent_claudesonnet45.csv` |
| `resolution/claudecode_pro_gpt54.csv` | Claude Code + GPT-5.4, Pro | `scripts/data/cc_gpt54.csv` |
| `resolution/claudecode_pro_sonnet45.csv` | Claude Code + Claude Sonnet 4.5, Pro (single run) | `logs_cc_sonnet/results.csv` |
| `resolution/minisweagent_verified_{minimax,gpt5mini}.csv` | mini-SWE-agent, Verified | `resolved=` header of the Docent exports in `analysis/baselines/minisweagent_*_verified/*.md` |
| `labels/swebench_verified_difficulty.csv` | Verified difficulty: Easy (<15 min), Medium (15 min–1 h), Hard (1–4 h and >4 h) | HF `princeton-nlp/SWE-bench_Verified` |
| `labels/swebench_pro_language.csv` | language of the code files changed by the gold patch (`corrected_language`) | `scripts/language_consistency.csv` |
| `labels/swebench_pro_file_count.csv` | number of code files changed by the gold patch (1, 2–3, 4+) | `analysis/file_count_corrected.csv` |
| `ablation/<variant>.jsonl`, `ablation/instances.txt` | per-instance outcome of each ablation variant; the 100-instance subset | `logs_ablation/<variant>/results.jsonl` |
| `ablation/recorded_counts.json` | resolved / regression counts for variant runs without per-instance logs | `logs_ablation/summary_100.json` |

Every `resolution/*.csv` is `instance_id, resolved` (1/0).
`collect_inputs.py` re-creates `inputs/` from the full icat-agent repo; it is not needed to reproduce the results.

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

Per case, `case_studies/<case>/` holds the iCAT run as archived (`icat_<model>/raw`) and the baseline trajectories.
