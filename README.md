# iCAT-Agent

iCAT-Agent is a multi-agent system for resolving GitHub issues. 

## Roles

- **Issue quality checker**: LLM call to analyze the issue, identify buggy files/functions, and determine if the localizer should run.
- **Explorer Agent**: Explores the codebase to find buggy files, classes, and functions. Skipped when the issue quality confidence is HIGH.
- **Patch Editor Agent**: Reads source code, traces call chains, and generates code fixes. Communicates with the reproducer via MessageBus.
- **Reproducer Agent**: Writes reproduction/regression tests and runs validation, and sends `validation_passed`/`validation_failed` feedback to drive iterative refinement.

## Setup

- Python 3.10
- Set up:
```
export OPENROUTER_API_KEY=[KEY]
pip install -r requirements.txt 
```

## Directory Structure

```
agent/
  src/                              # Source code
    main.py                         
    agent/                          # Agent implementations
      base_agent.py                 # Base agent class
      patch_editor_agent.py         # Patch editing agent
      reproducer_agent.py           # Validation agent
      localizer_agent.py            # Explorer agent
      docker_env.py                 
      tools.py                      
      utils.py                      
    config/
      prompts.yaml                  # Agent system prompts (some embedding prompts are in graph nodes and added during execution dynamically)
    graph/
      graph_builder.py              
```

## Trajectory Files

Each instance directory in `trajs/` contains:

- `*_summary.json`: Instance metadata and final patch
- `*_patch_editor.json`: Full trajectory of the patch editor agent (tool calls, responses)
- `*_reproducer.json`: Full trajectory of the reproducer agent
- `*_localizer.json`: Full trajectory of the localizer agent (if run)
- `*.pred`: Prediction file with `instance_id`, `model_name_or_path`, `model_patch`

## Usage

```bash
# Run on a single instance
python main.py --swe-bench pro --split test --instance <instance_id> \
  --triage-sequential --model "openrouter/openai/gpt-5.4" --max-cost 3.0

# Key arguments
--swe-bench {lite,verified,pro}   # Dataset
--triage-sequential               # Localizer runs first if needed
--model MODEL                     # LLM model identifier
--max-cost FLOAT                  # Cost budget per instance (USD)
--logdir DIR                      # Output directory
```


| Flag | Default | Meaning |
|---|---|---|
| `--swe-bench` | `pro` | Benchmark subset |
| `--instance ID` | *required* | Instance to run |
| `--split {dev,test}` | `test` | Dataset split |
| `--model` | `openai:gpt-5-mini` | LLM for all agents: `openrouter/...`, `openai:...` or `anthropic:...` |
| `--max-cost USD` | `3.0` | Budget per instance |
| `--logdir DIR` | `logs` | Output directory |
| `--triage-sequential` / `--no-triage-sequential` | on | Issue Quality Checker first; run the Explorer only if the bug location is unclear |
| `--triage-sequential-always` | off | Always run the Explorer before the Editor and Validator |
| `--triage` | off | A single triage LLM call, no Explorer |
| `--no-plan` | off | No planning step; agents explore on their own |
| `--shared-plan` | off | One shared exploration pass that produces all three plans |
| `--truncate-view` / `--no-truncate-view` | on | Truncate `view_file` output to the first and last 5K characters |


## Additional Analysis

Sub-agent Hk(t): 

<img width="1124" height="353" alt="image" src="https://github.com/user-attachments/assets/877ec805-3b0f-4d4a-b29d-58eb0bae286b" />

