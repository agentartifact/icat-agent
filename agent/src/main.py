from __future__ import annotations
import argparse
import logging
import logging.config
import os
from pathlib import Path
from typing import Any, Dict


BASE_LOGGING_CONFIG = {
    "version": 1,
    "formatters": {
        "default": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "default",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

logging.config.dictConfig(BASE_LOGGING_CONFIG)


def setup_instance_logging(instance_id: str, instance_dir: str):
    """
    Create a file handler that logs to {instance_dir}/{instance_id}.log
    Attach it to the root logger.
    """
    os.makedirs(instance_dir, exist_ok=True)
    logfile = os.path.join(instance_dir, f"{instance_id}.log")

    file_handler = logging.FileHandler(logfile)
    file_handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.addHandler(file_handler)

    return file_handler


def run_instance(
    instance_id: str,
    instance: dict,
    model: str,
    log_dir: str = "logs",
    max_cost: float = 3.0,
    no_plan: bool = False,
    shared_plan: bool = False,
    triage: bool = False,
    triage_sequential: bool = False,
    triage_sequential_always: bool = False,
    swe_bench_subset: str = "lite",
    last_n_observations: int = 0,
    truncate_view: bool = False,
):
    """
    Run each single SWE-bench instance.
    All run in docker containers (swebench images) for isolated execution.
    Uses self-planning mode where each agent generates their own plan.
    """
    # Create per-instance directory: log_dir/{instance_id}/
    instance_dir = os.path.join(log_dir, instance_id)
    os.makedirs(instance_dir, exist_ok=True)

    # per-instance file logging
    handler = setup_instance_logging(instance_id, instance_dir)
    logging.info(f"--- Starting instance {instance_id} with model {model} ---")
    logging.info(f"Instance directory: {instance_dir}")
    logging.info(f"Problem: {instance.get('problem_statement', '')}")

    from graph.graph_builder import compile_graph

    init_state = {
        "instance_id": instance["instance_id"],
        "repo": instance["repo"],
        "base_commit": instance["base_commit"],
        "problem_statement": instance.get("problem_statement", "") or "",
        "test_cmds": instance.get("test_cmds", []),
        "patch": instance.get("patch"),
        "image_key": instance.get("image_key"),
        "instance_data": instance,  # full instance dict for per-instance setup
        "swe_bench_subset": swe_bench_subset,  # "lite", "verified", "pro", etc.

        # Model (provider:model format)
        "model_name": model,

        # Log directory for saving outputs (instance-specific)
        "log_dir": instance_dir,

        # Cost limit per instance
        "max_cost": max_cost,

        # Plans (will be populated by planner node)
        "localizer_plan": "",
        "reproducer_plan": "",
        "patch_editor_plan": "",

        # Feasibility check results
        "feasibility_status": None,
        "feasibility_score": None,
        "feasibility_details": None,

        # Plan refinement tracking
        "plan_refinement_count": 0,

        # Triage results
        "triage_buggy_files": None,
        "triage_buggy_functions": None,

        # Results (will be accumulated by parallel agents)
        "localizer_file": [],
        "localizer_rationale": [],
        "reproducer_status": [],
        "reproducer_output": [],
        "reproducer_script": None,
        "reproducer_expected": None,
        "reproducer_actual": None,
        "patch_editor_modified_file": [],
        "patch_editor_patch": [],
        "patch_editor_unified_diff": [],
        "done_count": 0,

        # Reproducer baseline (populated by reproducer in Phase 1)
        "reproducer_baseline_regression": None,

        # Patch validation loop
        "patch_attempt_count": 0,
        "patch_validation_status": None,
        "patch_validation_details": None,

        # Per-agent Docker container IDs (populated by repo_node)
        "localizer_docker_id": None,
        "reproducer_docker_id": None,
        "patch_editor_docker_id": None,

        # Observation elision
        "last_n_observations": last_n_observations,

        # View file truncation
        "truncate_view": truncate_view,
    }

    logging.info(f"Compiling graph... (no_plan={no_plan}, shared_plan={shared_plan}, triage={triage}, triage_sequential={triage_sequential}, triage_sequential_always={triage_sequential_always})")
    app = compile_graph(no_plan=no_plan, shared_plan=shared_plan, triage=triage, triage_sequential=triage_sequential, triage_sequential_always=triage_sequential_always)

    # Save graph visualization
    try:
        png_image = app.get_graph().draw_mermaid_png()
        png_path = os.path.join(instance_dir, "graph.png")
        with open(png_path, "wb") as f:
            f.write(png_image)
        logging.info(f"Graph visualization saved to {png_path}")
    except Exception as e:
        logging.warning(f"Could not save graph PNG: {e}")

    # Run the graph
    logging.info("Invoking graph...")
    try:
        result = app.invoke(init_state)
    except Exception as e:
        error_str = str(e)
        logging.error(f"Graph execution failed: {e}")

        # Tag fatal errors for rerun
        if "invalid_prompt" in error_str or "usage policy" in error_str:
            tag = "CONTENT_POLICY_ERROR"
        else:
            tag = "RUNTIME_ERROR"

        # Write error tag file for batch scripts to detect
        error_file = os.path.join(instance_dir, f"{instance_id}.error")
        with open(error_file, "w") as f:
            f.write(f"{tag}: {error_str}\n")
        logging.error(f"Error tagged in {error_file}: {tag}")

        # Clean up any running Docker containers
        import subprocess
        for key in ["shared_docker_id", "localizer_docker_id", "reproducer_docker_id", "patch_editor_docker_id"]:
            cid = init_state.get(key)
            if cid:
                try:
                    subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=15)
                    logging.info(f"Cleaned up container {cid[:12]} ({key})")
                except Exception:
                    pass

        raise

    # Log final results
    logging.info("=" * 80)
    logging.info("FINAL RESULTS:")
    logging.info(f"  Localizer files: {result.get('localizer_file', [])}")
    logging.info(f"  Reproducer status: {result.get('reproducer_status', [])}")
    logging.info(f"  Patch editor modified: {result.get('patch_editor_modified_file', [])}")
    logging.info("=" * 80)

    # Save predictions, trajectory, and instance summary
    from run.common import save_predictions as save_pred, save_trajectory, save_instance_summary
    save_pred(Path(log_dir), instance_id, result, model_name=model)
    save_trajectory(Path(log_dir), instance_id, result, info={"model": model})
    save_instance_summary(Path(log_dir), instance_id, result)
    logging.info(f"Predictions saved to: {instance_dir}/{instance_id}.pred")
    logging.info(f"Trajectory saved to: {instance_dir}/{instance_id}.traj")
    logging.info(f"Summary saved to: {instance_dir}/{instance_id}_summary.json")

    # Generate detailed trajectory PDF
    try:
        from run.generate_trajectory_pdf import generate_pdf, find_run_log
        log_path = find_run_log(instance_dir, instance_id)
        if log_path:
            output_path = os.path.join(instance_dir, "detailed_trajectory")
            pdf_path = generate_pdf(instance_id, log_path, output_path)
            logging.info(f"Detailed trajectory PDF saved to: {pdf_path}")
        else:
            logging.warning(f"No run log found for trajectory PDF generation")
    except Exception as e:
        logging.warning(f"Failed to generate trajectory PDF: {e}")

    logging.info(f"--- Finished instance {instance_id} ---")

    root = logging.getLogger()  # clean up handler to avoid duplicates
    root.removeHandler(handler)
    handler.close()

    return result


def load_instance_from_swebench(subset: str, split: str, instance_id: str) -> Dict[str, Any]:
    from run.batch_instances import load_instances
    return load_instances(subset=subset, split=split, instance_ids=[instance_id])[0].to_dict()


def parse_args():
    parser = argparse.ArgumentParser(
        description="PLAgent - SWE-bench instance runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        "--swe-bench",
        type=str,
        choices=["lite", "verified", "full", "pro", "multimodal"],
        default="lite",
        help="SWE-bench subset to load from HuggingFace (default: lite)",
    )

    parser.add_argument(
        "--instance",
        type=str,
        required=True,
        help="Instance ID to run (e.g., astropy__astropy-13033)",
    )

    parser.add_argument(
        "--split",
        type=str,
        default="dev",
        choices=["dev", "test"],
        help="Dataset split (for --swe-bench)",
    )

    parser.add_argument(
        "--logdir",
        type=str,
        default="logs",
        help="Directory to store per-instance logs and predictions",
    )

    parser.add_argument(
        "--model",
        type=str,
        default="openai:gpt-5-mini",
        help="Model to use (provider:model format, e.g. openai:gpt-5-mini, anthropic:claude-sonnet-4-20250514)",
    )

    parser.add_argument(
        "--max-cost",
        type=float,
        default=2.0,
        help="Maximum cost per instance in USD (default: $2.00)",
    )

    parser.add_argument(
        "--no-plan",
        action="store_true",
        default=False,
        help="Disable planners; agents explore on their own without plans",
    )

    parser.add_argument(
        "--shared-plan",
        action="store_true",
        default=False,
        help="Use shared exploration planner: one exploration pass, three plan generation calls (more efficient)",
    )

    parser.add_argument(
        "--triage",
        action="store_true",
        default=False,
        help="Use triage mode: one fast LLM call analyzes the issue, no tool-based planning (fastest)",
    )
    parser.add_argument(
        "--triage-sequential",
        action="store_true",
        default=False,
        help="Triage + sequential: if localization unknown, run localizer first then editor+reproducer",
    )
    parser.add_argument(
        "--triage-sequential-always",
        action="store_true",
        default=False,
        help="Triage + sequential: ALWAYS run localizer first regardless of feasibility, then editor+reproducer",
    )
    parser.add_argument(
        "--last-n-observations",
        type=int,
        default=5,
        help="Elide old tool observations, keeping only the last N verbatim (0=disabled)",
    )
    parser.add_argument(
        "--truncate-view",
        action="store_true",
        default=False,
        help="Truncate view_file output at 10K chars (first 5K + last 5K)",
    )

    return parser.parse_args()


def _verify_environment():
    """Check that required language bindings are importable.

    Warns (doesn't exit) if any are missing so Python-only runs still proceed,
    but the user knows JS/TS/Go/Java syntax checks will be degraded.
    """
    required = {
        'tree_sitter': 'core',
        'tree_sitter_java': 'Java (.java)',
        'tree_sitter_javascript': 'JavaScript (.js/.jsx/.mjs/.cjs)',
        'tree_sitter_typescript': 'TypeScript (.ts/.tsx)',
        'tree_sitter_go': 'Go (.go)',
    }
    missing = []
    for mod, desc in required.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append((mod, desc))
    if missing:
        pkgs = ' '.join(m.replace('_', '-') for m, _ in missing)
        logging.warning(
            "Missing tree-sitter bindings — syntax checks will be skipped for: "
            + ', '.join(desc for _, desc in missing)
            + f"\n    Install with: pip install {pkgs}"
        )


if __name__ == "__main__":
    args = parse_args()

    _verify_environment()

    logging.info(f"Loading instance {args.instance} from SWE-bench {args.swe_bench} ({args.split})")
    instance = load_instance_from_swebench(args.swe_bench, args.split, args.instance)

    if args.no_plan:
        logging.info("Using NO-PLAN mode (agents explore on their own)")
    elif args.triage_sequential_always:
        logging.info("Using TRIAGE SEQUENTIAL ALWAYS mode (localizer ALWAYS runs first, then editor+reproducer)")
    elif args.triage_sequential:
        logging.info("Using TRIAGE SEQUENTIAL mode (localizer runs first if localization unknown)")
    elif args.triage:
        logging.info("Using TRIAGE mode (one fast LLM call, no tool-based planning)")
    elif args.shared_plan:
        logging.info("Using SHARED-PLAN mode (one exploration, three plan generation calls)")
    else:
        logging.info("Using SELF-PLANNING mode (each agent generates their own plan)")
    logging.info("Tests will run in Docker containers (swebench images)")
    logging.info(f"Max cost per instance: ${args.max_cost:.2f}")
    instance_dir = os.path.join(args.logdir, args.instance)
    logging.info(f"All logs will be saved to: {instance_dir}")

    result = run_instance(
        args.instance,
        instance,
        args.model,
        log_dir=args.logdir,
        max_cost=args.max_cost,
        no_plan=args.no_plan,
        shared_plan=args.shared_plan,
        triage=args.triage,
        triage_sequential=args.triage_sequential,
        triage_sequential_always=args.triage_sequential_always,
        last_n_observations=args.last_n_observations,
        swe_bench_subset=args.swe_bench,
        truncate_view=args.truncate_view,
    )

    print("\n" + "=" * 80)
    print("EXECUTION COMPLETE")
    print("=" * 80)
    print(f"Instance: {args.instance}")
    print(f"Instance directory: {instance_dir}")
    print(f"Localizer files: {result.get('localizer_file', [])}")
    print(f"Reproducer status: {result.get('reproducer_status', [])}")
    print(f"Patch editor modified: {result.get('patch_editor_modified_file', [])}")
    print(f"Predictions: {instance_dir}/{args.instance}.pred")
    print(f"Trajectory PDF: {instance_dir}/detailed_trajectory.pdf")
    print("=" * 80)
