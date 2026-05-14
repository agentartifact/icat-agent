# graph/graph_builder.py
from __future__ import annotations
import json
import logging
import operator
from typing import Annotated, TypedDict, List, Dict, Any, Optional
from langgraph.graph import StateGraph, START, END

# Import test runner utilities for framework detection
import sys
from pathlib import Path
_tool_lib = Path(__file__).parent.parent / "regression_tests_tool" / "lib"
if str(_tool_lib) not in sys.path:
    sys.path.insert(0, str(_tool_lib))

try:
    from test_runner import detect_framework, TEST_COMMANDS
    HAS_TEST_RUNNER = True
except ImportError:
    HAS_TEST_RUNNER = False
    TEST_COMMANDS = {
        "pytest": "python -m pytest -xvs",
        "django": "./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1",
        "sympy": "./bin/test -C --verbose",
        "sphinx": "tox --current-env -epy39 -v --",
    }

    def detect_framework(instance_id: str) -> str:
        """Fallback framework detection based on instance ID."""
        instance_lower = instance_id.lower()
        if instance_lower.startswith("django"):
            return "django"
        elif instance_lower.startswith("sympy"):
            return "sympy"
        elif instance_lower.startswith("sphinx"):
            return "sphinx"
        else:
            return "pytest"

from agent.localizer_agent import LocalizerAgent
from agent.patch_editor_agent import PatchEditorAgent
from agent.reproducer_agent import ReproducerAgent
from agent.context_sharing import AgentContext, AgentCommunicationLogger, AgentMessageBus
from agent.utils import DetailedLogger
from agent.context_window import create_window_manager
from agent.prompt_loader import (
    get_prompt,
    get_plan_evaluation_prompt,
)

# Additional imports for agentic self-planning
import fnmatch
from agent.utils import create_chat_model
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage
from langchain_core.tools import tool
from agent.docker_env import DockerEnvironment
from agent.tools import (
    make_find_files, make_grep_content, make_view_file, make_list_dir,
    make_view_symbol, make_view_outline,
    make_trace_call_chain,
    make_run_tests, make_validate_entities, make_check_plan_quality,
    make_submit_plan,
)


class GraphState(TypedDict):
    """
    State schema for the parallel multi-agent graph.
    Uses Annotated types for fields that can be updated concurrently.
    """
    # Input fields (single value)
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    test_cmds: List[str]
    patch: Optional[str]
    image_key: Optional[str]
    instance_data: Optional[Dict[str, Any]]  # full instance dict for per-instance setup
    swe_bench_subset: str  # "lite", "verified", "pro", etc.

    # Log directory for saving outputs
    log_dir: str

    # Docker configuration - always uses Docker containers for test execution

    # Repo context (single value)
    repo_path: str

    # Plans (single value)
    localizer_plan: str
    reproducer_plan: str
    patch_editor_plan: str

    # Feasibility check results (single value)
    feasibility_status: Optional[str]  # HIGH, MEDIUM, LOW
    feasibility_score: Optional[float]
    feasibility_details: Optional[Dict[str, Any]]

    # Agent coordination (NOTE: These are references to shared objects, not serialized)
    file_coordinator: Optional[Any]  # FileCoordinator instance for real-time file coordination
    message_bus: Optional[Any]  # AgentMessageBus instance for real-time inter-agent communication

    # Plan refinement tracking
    plan_refinement_count: int  # Track refinement iterations (max 1)

    # Triage results (populated by triage node)
    triage_buggy_files: Optional[List[str]]
    triage_buggy_functions: Optional[List[str]]

    # Force localizer to run even when feasibility is HIGH
    force_localizer: Optional[bool]

    # Results (can be updated concurrently by parallel agents)
    localizer_file: Annotated[List[str], operator.add]
    localizer_rationale: Annotated[List[str], operator.add]
    reproducer_status: Annotated[List[str], operator.add]
    reproducer_output: Annotated[List[str], operator.add]  # Test output from reproducer
    reproducer_script: Optional[str]  # LLM-generated reproduction script
    reproducer_expected: Optional[str]  # Expected behavior
    reproducer_actual: Optional[str]  # Actual behavior (bug)
    patch_editor_modified_file: Annotated[List[str], operator.add]
    patch_editor_patch: Annotated[List[str], operator.add]
    patch_editor_unified_diff: Annotated[List[str], operator.add]  # Unified diff format of the patch (list for concurrent update)
    done_count: Annotated[int, operator.add]

    # Model configuration
    model_name: str  # Model to use for all agents (e.g., "anthropic:claude-sonnet-4-20250514")

    # Cost budget (total across all sub-agents in this process/instance)
    max_cost: float

    # Last N observations to retain per agent (0 = no sliding window)
    last_n_observations: int

    # Truncate view_file output at 10K chars
    truncate_view: bool

    # Docker container IDs
    shared_docker_id: Optional[str]  # Container ID for planners (read-only)
    localizer_docker_id: Optional[str]  # Localizer's dedicated container
    reproducer_docker_id: Optional[str]  # Reproducer's dedicated container
    patch_editor_docker_id: Optional[str]  # Patch editor's dedicated container


def _get_instance_setup_commands(instance: dict, repo_path: str = "/testbed") -> str:
    """Extract per-instance environment setup commands.

    Handles two dataset formats:
    - SWE-bench Verified/Lite: Uses swebench TestSpec to generate setup script
    - SWE-bench Pro: Uses before_repo_set_cmd field directly

    Returns a shell script that sets up the environment before the agent starts.
    """
    log = logging.getLogger("RepoNode")

    # SWE-bench Pro: before_repo_set_cmd is for EVALUATION only (it cherry-picks
    # golden test files from the PR commit). Agents should work on the bare base
    # commit without access to golden tests. Skip it during agent setup.
    before_cmd = instance.get("before_repo_set_cmd", "")
    if before_cmd:
        log.info(f"[RepoUtility] Skipping before_repo_set_cmd (evaluation-only, not for agent setup)")
        return ""

    # SWE-bench Verified/Lite: extract from TestSpec
    try:
        from swebench.harness.test_spec.test_spec import make_test_spec
        spec = make_test_spec(instance)
        setup_lines = []
        for line in spec.eval_script_list:
            if 'Start Test Output' in line:
                break
            # Skip diagnostic/test-specific commands
            if any(skip in line for skip in [
                'git status', 'git show', 'git -c core.fileMode',
                'git checkout', 'git apply', 'EOF_',
            ]):
                continue
            # Skip patch content lines
            if line.startswith(('diff --git', '---', '+++', '@@', '+', '-', ' ')):
                continue
            setup_lines.append(line)
        return '\n'.join(setup_lines)
    except Exception as e:
        log.warning(f"[RepoUtility] Could not generate setup commands: {e}")
        return ""


def repo_node(state: dict) -> dict:
    """
    First node: Start shared Docker container.
    Uses swebench Docker images which have repo pre-cloned at /testbed.
    """
    from agent.docker_env import DockerEnvironment

    log = logging.getLogger("RepoNode")
    log.info(f"[RepoUtility] Starting for {state['instance_id']} (Docker-based)")

    instance_id = state["instance_id"]
    image_name = state.get("image_key")

    # Determine repo path based on dataset subset
    swe_bench_subset = state.get("swe_bench_subset", "lite")
    repo_path = "/app" if swe_bench_subset == "pro" else "/testbed"
    log.info(f"[RepoUtility] Dataset subset={swe_bench_subset}, repo_path={repo_path}")

    # Generate per-instance setup commands
    instance_data = state.get("instance_data", {})
    setup_script = _get_instance_setup_commands(instance_data, repo_path=repo_path) if instance_data else ""

    # Start shared Docker container for planners (read-only)
    log.info(f"[RepoUtility] Starting shared Docker container for planners...")
    shared_docker = DockerEnvironment(instance_id, timeout=1800, image_name=image_name, repo_path=repo_path)  # 30 min timeout
    shared_docker.start()

    if not shared_docker.container_id:
        log.error("[RepoUtility] Failed to start shared Docker container")
        return {
            "repo_path": repo_path,
            "file_coordinator": None,
            "message_bus": AgentMessageBus(),
            "shared_docker_id": None,
            "localizer_docker_id": None,
            "reproducer_docker_id": None,
            "patch_editor_docker_id": None,
        }

    log.info(f"[RepoUtility] Shared Docker container started: {shared_docker.container_id[:12]}")

    # Run per-instance setup in shared container
    if setup_script and shared_docker.container_id:
        log.info(f"[RepoUtility] Running per-instance setup in shared container...")
        rc, out, err = shared_docker.run_command(setup_script, timeout=300)
        if rc != 0:
            log.warning(f"[RepoUtility] Setup script returned rc={rc}: {(err or out or '')[:200]}")
        else:
            log.info(f"[RepoUtility] Setup completed in shared container")

    # Start dedicated containers for each execution agent
    agent_containers = {}
    for agent_name in ["localizer", "reproducer", "patch_editor"]:
        key = f"{agent_name}_docker_id"
        log.info(f"[RepoUtility] Starting dedicated container for {agent_name}...")
        try:
            docker = DockerEnvironment(instance_id, timeout=1800, image_name=image_name, repo_path=repo_path)
            docker.start()
            if docker.container_id:
                agent_containers[key] = docker.container_id
                log.info(f"[RepoUtility] {agent_name} container started: {docker.container_id[:12]}")
                # Run per-instance setup in each agent container
                if setup_script:
                    rc, out, err = docker.run_command(setup_script, timeout=300)
                    if rc != 0:
                        log.warning(f"[RepoUtility] Setup in {agent_name} container returned rc={rc}: {(err or out or '')[:200]}")
                    else:
                        log.info(f"[RepoUtility] Setup completed in {agent_name} container")
            else:
                log.error(f"[RepoUtility] Failed to start {agent_name} container")
                agent_containers[key] = None
        except Exception as e:
            log.error(f"[RepoUtility] Error starting {agent_name} container: {e}")
            agent_containers[key] = None

    return {
        "repo_path": repo_path,
        "file_coordinator": None,
        "message_bus": AgentMessageBus(),
        "shared_docker_id": shared_docker.container_id,
        "localizer_docker_id": agent_containers.get("localizer_docker_id"),
        "reproducer_docker_id": agent_containers.get("reproducer_docker_id"),
        "patch_editor_docker_id": agent_containers.get("patch_editor_docker_id"),
    }


class RolePlanner:
    """
    Role-specific planner that generates a single agent plan for one role.
    Each role (localizer, patch_editor, reproducer) gets its own planner
    with role-specific tools and prompts.
    """

    # Define which tools each role gets
    ROLE_TOOLS = {
        "localizer": [
            "find_files", "grep_content", "view_file", "list_dir",
            "view_symbol", "view_outline",
            "trace_call_chain",
            "submit_plan",
        ],
        "patch_editor": [
            "list_dir", "view_file", "view_symbol", "view_outline", "grep_content",
            "trace_call_chain",
            "submit_plan",
        ],
        "reproducer": [
            "view_file", "trace_call_chain",
            "submit_plan",
        ],
    }

    def __init__(
        self,
        role: str,
        problem_statement: str,
        instance_id: str,
        docker_env: Optional[DockerEnvironment],
        model_name: str = "anthropic:claude-sonnet-4-20250514",
        max_steps: int = 10,
        logger: Optional[logging.Logger] = None,
        log_dir: str = "logs",
        detailed_log: Optional['DetailedLogger'] = None,
    ):
        if role not in self.ROLE_TOOLS:
            raise ValueError(f"Invalid role: {role}. Must be one of {list(self.ROLE_TOOLS.keys())}")

        self.role = role
        self.problem_statement = problem_statement
        self.instance_id = instance_id
        self.docker_env = docker_env
        self.model_name = model_name
        self.max_steps = max_steps
        self.logger = logger or logging.getLogger(f"RolePlanner-{role}")
        self.log_dir = log_dir
        self.detailed_log = detailed_log

        self.explored_files: List[str] = []
        self.plan_submitted = False
        self.final_plan: Optional[str] = None

        # Initialize tools and model
        self._init_tools()

        # Bind only the tools for this role
        role_tool_names = self.ROLE_TOOLS[role]
        bound_tools = [self.all_tools[name] for name in role_tool_names if name in self.all_tools]
        self.model = create_chat_model(model_name)
        self.model_with_tools = self.model.bind_tools(bound_tools)

    def _init_tools(self):
        """Initialize exploration + planning tools from shared factories. Each role gets a subset."""

        def _on_view_file(path):
            if path not in self.explored_files:
                self.explored_files.append(path)

        def _on_submit(plan_str):
            self.final_plan = plan_str
            self.plan_submitted = True

        # All tools from agent/tools.py factories
        # Note: validate_entities and check_plan_quality are intentionally excluded —
        # plan_evaluation_node handles quality/entity checks post-submission to avoid
        # redundant LLM calls during planning.
        self.all_tools = {
            "find_files": make_find_files(self.docker_env),
            "grep_content": make_grep_content(self.docker_env),
            "view_file": make_view_file(self.docker_env, max_lines_cap=200, on_view_callback=_on_view_file, truncate_output=self.truncate_view if hasattr(self, "truncate_view") else False),
            "list_dir": make_list_dir(self.docker_env),
            "view_symbol": make_view_symbol(self.docker_env),
            "view_outline": make_view_outline(self.docker_env),
            "run_tests": make_run_tests(
                self.docker_env,
                instance_id=self.instance_id,
                detect_framework_fn=detect_framework if HAS_TEST_RUNNER else None,
                test_commands=TEST_COMMANDS,
            ),
            "submit_plan": make_submit_plan(role=self.role, on_submit=_on_submit),
            "trace_call_chain": make_trace_call_chain(self.docker_env),
        }

    def run(self) -> Optional[str]:
        """Run the role-specific planning loop and return the plan as a JSON string."""
        self.logger.info(f"[RolePlanner-{self.role}] Starting planning")

        system_prompt = get_prompt(f"{self.role}_planner", "system_prompt", problem_statement=self.problem_statement)
        if not system_prompt:
            raise ValueError(f"Missing {self.role}_planner.system_prompt in prompts.yaml")

        initial_context = (
            f"Create your {self.role} plan.\n\n"
            "1. Read the problem statement carefully to identify key files and functions.\n"
            "2. Use view_file()/trace_call_chain() calls to confirm key files exist.\n"
            "3. Call submit_plan() with your JSON plan.\n\n"
            "submit_plan() is the ONLY way to complete your task."
        )

        # Use sliding window manager for context management
        window_manager = create_window_manager(
            model_name=self.model_name,
            summarization_threshold=0.80,
            min_recent_turns=4,
        )
        window_manager.set_system_message(system_prompt)
        window_manager.set_plan_message(initial_context)

        # Build tool dispatch dict from role-specific tools
        role_tool_names = self.ROLE_TOOLS[self.role]
        tool_dispatch = {}
        for name in role_tool_names:
            if name in self.all_tools:
                t = self.all_tools[name]
                tool_dispatch[t.name] = t

        # Format messages into a readable prompt string for logging
        def _format_messages_for_log(msgs):
            parts = []
            for m in msgs:
                role_label = type(m).__name__.replace("Message", "").upper()
                content = m.content if isinstance(m.content, str) else str(m.content)
                # Include tool calls in AI messages
                tool_calls_str = ""
                if hasattr(m, 'tool_calls') and m.tool_calls:
                    tc_parts = []
                    for tc in m.tool_calls:
                        tc_parts.append(f"  tool_call: {tc['name']}({json.dumps(tc['args'], default=str)})")
                    tool_calls_str = "\n" + "\n".join(tc_parts)
                parts.append(f"[{role_label}]\n{content}{tool_calls_str}")
            return "\n---\n".join(parts)

        for step in range(1, self.max_steps + 1):
            if self.plan_submitted:
                self.logger.info(f"[RolePlanner-{self.role}] Plan submitted at step {step-1}")
                break

            self.logger.info(f"[RolePlanner-{self.role}] Step {step}/{self.max_steps}")
            if self.detailed_log:
                self.detailed_log.log_step(step, f"Planning step {step}/{self.max_steps}")

            # Gentle reminder of remaining steps
            remaining = self.max_steps - step
            if remaining == 5 and not self.plan_submitted:
                window_manager.add_message(HumanMessage(
                    content=f"Reminder: You have {remaining} steps remaining to submit your plan via submit_plan()."
                ))
                self.logger.info(f"[RolePlanner-{self.role}] Sent step reminder ({remaining} remaining)")

            messages = window_manager.get_messages()

            try:
                resp = self.model_with_tools.invoke(messages)
            except Exception as e:
                self.logger.error(f"[RolePlanner-{self.role}] LLM call failed: {e}")
                if self.detailed_log:
                    self.detailed_log.log_error(e, context=f"LLM call failed at step {step}")
                break

            # Log the full LLM call (prompt + response)
            if self.detailed_log:
                prompt_str = _format_messages_for_log(messages)
                self.detailed_log.log_llm_call_from_response(
                    prompt=prompt_str,
                    response=resp,
                    metadata={"model": self.model_name, "step": step}
                )

            if getattr(resp, "tool_calls", None):
                tool_msgs = []
                for call in resp.tool_calls:
                    tool_fn = tool_dispatch.get(call["name"])
                    if tool_fn:
                        try:
                            out = tool_fn.invoke(call["args"])
                            self.logger.info(f"[RolePlanner-{self.role}] Tool {call['name']} called with {call['args']}")
                            if self.detailed_log:
                                self.detailed_log.log_tool_call(call["name"], call["args"], str(out))
                            tool_msgs.append(ToolMessage(content=out, tool_call_id=call["id"]))
                        except Exception as e:
                            error_msg = f"Error: {e}"
                            if self.detailed_log:
                                self.detailed_log.log_tool_call(call["name"], call["args"], error_msg)
                            tool_msgs.append(ToolMessage(content=error_msg, tool_call_id=call["id"]))
                    else:
                        error_msg = f"Unknown tool: {call['name']}"
                        if self.detailed_log:
                            self.detailed_log.log_tool_call(call["name"], call["args"], error_msg)
                        tool_msgs.append(ToolMessage(content=error_msg, tool_call_id=call["id"]))

                window_manager.add_message(resp, increment_turn=True)
                for tm in tool_msgs:
                    window_manager.add_message(tm)

                if self.plan_submitted:
                    break
            else:
                # LLM responded with text instead of a tool call
                window_manager.add_message(resp, increment_turn=True)
                window_manager.add_message(HumanMessage(
                    content=(
                        "Plans written as text are IGNORED. You MUST call the submit_plan() tool "
                        "with your JSON plan string. Use the tool — do not write plans in text."
                    )
                ))

        # Log window manager stats
        stats = window_manager.get_stats()
        self.logger.info(f"[RolePlanner-{self.role}] Window stats: {stats}")

        # Fallback: if no plan submitted, force submission with only submit_plan available
        if not self.plan_submitted:
            self.logger.warning(f"[RolePlanner-{self.role}] Max steps reached without plan submission, forcing")
            try:
                import json as _json
                import re as _re

                # Build a compact prompt with just the essentials — no full conversation replay
                explored_summary = ", ".join(self.explored_files[:20]) if self.explored_files else "none"
                # Gather key findings from recent tool results
                findings = []
                for msg in window_manager.get_messages()[-10:]:
                    if hasattr(msg, 'content') and isinstance(msg.content, str) and len(msg.content) > 20:
                        content = msg.content[:300]
                        if any(kw in content.lower() for kw in ['found', 'match', 'def ', 'class ', 'error', 'bug']):
                            findings.append(content)
                findings_text = "\n".join(findings[:5]) if findings else "See explored files."

                schemas = {
                    "localizer": '{"role":"localizer","potential_buggy_files":[...],"potential_buggy_classes":[...],"potential_buggy_functions":[...]}',
                    "patch_editor": '{"role":"patch_editor","steps":[{"action":"...","target_file":"...","target_function":"...","rationale":"..."}],"files_to_modify":[...],"functions_to_modify":[...]}',
                    "reproducer": '{"role":"reproducer","steps":[{"action":"...","target_file":"...","target_function":"...","rationale":"..."}],"test_files":[...],"existing_tests":[...],"focal_functions":[...],"failure_description":"..."}',
                }
                force_prompt = (
                    f"Generate a {self.role} plan as JSON and call submit_plan().\n\n"
                    f"Problem:\n{self.problem_statement[:2000]}\n\n"
                    f"Files explored: {explored_summary}\n\n"
                    f"Findings:\n{findings_text}\n\n"
                    f"Your plan MUST use this exact schema:\n{schemas.get(self.role, schemas['localizer'])}"
                )

                model_submit_only = self.model.bind_tools([self.all_tools["submit_plan"]])
                resp = model_submit_only.invoke([
                    SystemMessage(content=f"You are a {self.role} planner. Call submit_plan() with your JSON plan."),
                    HumanMessage(content=force_prompt),
                ])

                tool_calls = getattr(resp, "tool_calls", None)
                text = resp.content if isinstance(resp.content, str) else str(resp.content)
                self.logger.info(f"[RolePlanner-{self.role}] Force response: tool_calls={bool(tool_calls)}, text_len={len(text.strip())}")

                if tool_calls:
                    for call in tool_calls:
                        if call["name"] == "submit_plan":
                            try:
                                result = self.all_tools["submit_plan"].invoke(call["args"])
                                self.logger.info(f"[RolePlanner-{self.role}] submit_plan result: {result}")
                            except Exception as e:
                                self.logger.error(f"[RolePlanner-{self.role}] Final submit failed: {e}")

                # If LLM responded with text containing JSON instead of a tool call, extract it
                if not self.plan_submitted and text.strip():
                    self.logger.info(f"[RolePlanner-{self.role}] Extracting JSON from text response")
                    if "```" in text:
                        match = _re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
                        if match:
                            text = match.group(1).strip()
                    json_match = _re.search(r'\{.*\}', text, _re.DOTALL)
                    if json_match:
                        try:
                            plan_obj = _json.loads(json_match.group(0))
                            if isinstance(plan_obj, dict):
                                self.final_plan = _json.dumps(plan_obj)
                                self.plan_submitted = True
                                self.logger.info(f"[RolePlanner-{self.role}] Extracted plan from text response")
                        except _json.JSONDecodeError:
                            self.logger.error(f"[RolePlanner-{self.role}] Failed to parse JSON from text response")
            except Exception as e:
                self.logger.error(f"[RolePlanner-{self.role}] Final attempt failed: {e}")

        return self.final_plan


def _setup_planner_docker(state: dict, role: str, log):
    """Helper to setup Docker environment for a planner node."""
    instance_id = state["instance_id"]
    shared_docker_id = state.get("shared_docker_id")
    image_name = state.get("image_key")
    docker_env = None
    owns_docker = False

    repo_path = state.get("repo_path", "/testbed")
    if shared_docker_id:
        docker_env = DockerEnvironment(instance_id, timeout=600, image_name=image_name, repo_path=repo_path)
        docker_env.container_id = shared_docker_id
        log.info(f"[{role}Planner] Using shared Docker: {shared_docker_id[:12]}")
    else:
        docker_env = DockerEnvironment(instance_id, timeout=600, image_name=image_name, repo_path=repo_path)
        docker_env.__enter__()
        owns_docker = True
        if docker_env.container_id:
            log.info(f"[{role}Planner] Docker started: {docker_env.container_id[:12]}")
        else:
            log.warning(f"[{role}Planner] Failed to start Docker")
            docker_env = None

    return docker_env, owns_docker


def _run_role_planner(state: dict, role: str) -> dict:
    """Run a role-specific planner and return the plan in the appropriate state key."""
    log = logging.getLogger(f"{role.title()}PlannerNode")
    model_name = state.get("model_name", "anthropic:claude-sonnet-4-20250514")
    detailed_log = DetailedLogger(f"{role}_planner", state["instance_id"], state.get("log_dir", "logs"), model_name=model_name, max_cost=state.get("max_cost", 3.0))

    log.info(f"[{role}Planner] Starting for {state['instance_id']}")
    detailed_log.log_start({"instance_id": state["instance_id"], "mode": f"{role}-planning"})

    problem_statement = state["problem_statement"]
    instance_id = state["instance_id"]
    log_dir = state.get("log_dir", "logs")

    docker_env, owns_docker = _setup_planner_docker(state, role, log)

    try:
        planner = RolePlanner(
            role=role,
            problem_statement=problem_statement,
            instance_id=instance_id,
            docker_env=docker_env,
            model_name=model_name,
            max_steps=5,
            logger=log,
            log_dir=log_dir,
            detailed_log=detailed_log,
        )
        plan = planner.run()
    finally:
        if owns_docker and docker_env:
            docker_env.__exit__(None, None, None)
            log.info(f"[{role}Planner] Docker cleaned up")

    log.info(f"[{role}Planner] Plan generated: {plan or ''}")
    detailed_log.log_plan(f"{role.title()} Plan:\n{plan or ''}")

    return {f"{role}_plan": plan or ""}


def localizer_planner_node(state: dict) -> dict:
    """Planner node: Generates localizer plan using role-specific RolePlanner."""
    return _run_role_planner(state, "localizer")


def patch_editor_planner_node(state: dict) -> dict:
    """Planner node: Generates patch editor plan using role-specific RolePlanner."""
    return _run_role_planner(state, "patch_editor")


def reproducer_planner_node(state: dict) -> dict:
    """Planner node: Generates reproducer plan using role-specific RolePlanner."""
    return _run_role_planner(state, "reproducer")


# =============================================================================
# TRIAGE PLANNER (--triage mode)
# =============================================================================

def triage_node(state: dict) -> dict:
    """
    Lightweight triage: one LLM call (no tools) analyzes the issue description
    and produces context for execution agents. Decides whether to skip localizer.
    """
    import json as _json
    import re as _re

    log = logging.getLogger("TriageNode")
    model_name = state.get("model_name", "anthropic:claude-sonnet-4-20250514")
    problem_statement = state["problem_statement"]
    instance_id = state["instance_id"]

    log.info(f"[Triage] Starting for {instance_id}")

    triage_prompt = get_prompt("triage", "system_prompt", problem_statement=problem_statement)
    if not triage_prompt:
        log.warning("[Triage] Missing triage.system_prompt, skipping triage")
        return {}

    model = create_chat_model(model_name)

    try:
        resp = model.invoke([
            SystemMessage(content="You analyze issue descriptions for a bug-fixing system. Return ONLY a valid JSON object (not a tuple or list). Use double quotes for all keys and string values. Use true/false (lowercase) for booleans. Do NOT wrap the JSON in markdown code blocks."),
            HumanMessage(content=triage_prompt),
        ])
        text = resp.content if isinstance(resp.content, str) else str(resp.content)

        # Extract JSON
        if "```" in text:
            match = _re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
            if match:
                text = match.group(1).strip()
        json_match = _re.search(r'\{.*\}', text, _re.DOTALL)
        if json_match:
            text = json_match.group(0)

        try:
            triage = _json.loads(text)
        except _json.JSONDecodeError:
            # Fallback: fix common non-standard JSON from reasoning models
            # (single quotes, trailing commas, unquoted keys)
            import ast
            try:
                parsed = ast.literal_eval(text)
                if not isinstance(parsed, dict):
                    raise ValueError(f"Expected dict, got {type(parsed).__name__}")
                triage = parsed
            except Exception:
                # Last resort: regex extraction of key fields
                triage = {}
                for key in ["localization_known", "fix_known", "skip_localizer"]:
                    m = _re.search(rf'"{key}"\s*:\s*(true|false)', text, _re.IGNORECASE)
                    if m:
                        triage[key] = m.group(1).lower() == "true"
                for key in ["buggy_files", "buggy_classes", "buggy_functions"]:
                    m = _re.search(rf'"{key}"\s*:\s*\[(.*?)\]', text, _re.DOTALL)
                    if m:
                        items = _re.findall(r'"([^"]+)"', m.group(1))
                        triage[key] = items
                for key in ["fix_strategy", "context_for_patch_editor", "context_for_reproducer"]:
                    m = _re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', text, _re.DOTALL)
                    if m:
                        triage[key] = m.group(1).replace('\\"', '"').replace('\\n', '\n')
                if not triage:
                    raise
        log.info(f"[Triage] Result: localization_known={triage.get('localization_known')}, "
                 f"fix_known={triage.get('fix_known')}, skip_localizer={triage.get('skip_localizer')}")
        log.info(f"[Triage] Buggy files: {triage.get('buggy_files', [])}")
        log.info(f"[Triage] Buggy classes: {triage.get('buggy_classes', [])}")
        log.info(f"[Triage] Buggy functions: {triage.get('buggy_functions', [])}")
        log.info(f"[Triage] Fix strategy: {triage.get('fix_strategy', 'N/A')}")

    except Exception as e:
        log.error(f"[Triage] Failed: {e}")
        triage = {}

    # Build context strings for each agent
    buggy_files = triage.get("buggy_files", [])
    buggy_classes = triage.get("buggy_classes", [])
    buggy_functions = triage.get("buggy_functions", [])
    fix_strategy = triage.get("fix_strategy", "")
    skip_localizer = triage.get("skip_localizer", False)

    # Localizer context
    if buggy_files or buggy_classes or buggy_functions:
        localizer_ctx = (
            f"From issue analysis:\n"
            f"Buggy files: {', '.join(buggy_files)}\n"
            f"Buggy classes: {', '.join(buggy_classes)}\n"
            f"Buggy functions: {', '.join(buggy_functions)}\n"
            f"Verify these are correct and identify the exact buggy lines."
        )
    else:
        localizer_ctx = ""

    # Patch editor context
    editor_ctx = triage.get("context_for_patch_editor", "")
    if fix_strategy:
        editor_ctx = f"Fix strategy: {fix_strategy}\n\n{editor_ctx}"
    if buggy_files:
        editor_ctx = f"Files to modify: {', '.join(buggy_files)}\n{editor_ctx}"
    if buggy_classes:
        editor_ctx = f"Classes to modify: {', '.join(buggy_classes)}\n{editor_ctx}"
    if buggy_functions:
        editor_ctx = f"Functions to modify: {', '.join(buggy_functions)}\n{editor_ctx}"

    # Reproducer context
    reproducer_ctx = triage.get("context_for_reproducer", "")

    result = {
        "localizer_plan": localizer_ctx,
        "patch_editor_plan": editor_ctx,
        "reproducer_plan": reproducer_ctx,
        "feasibility_status": "HIGH" if (triage.get("localization_known") and buggy_files and (buggy_classes or buggy_functions) and fix_strategy and reproducer_ctx) else "MEDIUM",
        # "feasibility_score": 0.9 if (triage.get("localization_known") and buggy_files and (buggy_classes or buggy_functions) and fix_strategy) else 0.5,
        "triage_buggy_files": buggy_files,
        "triage_buggy_functions": buggy_functions,
    }

    log.info(f"[Triage] skip_localizer={skip_localizer}, feasibility={result['feasibility_status']}")
    return result


# =============================================================================
# SHARED EXPLORATION PLANNER (--shared-plan mode)
# =============================================================================

def _explorer_node(state: dict) -> dict:
    """
    Shared exploration node: one agent explores the codebase and produces a context summary.
    Three single-shot LLM calls then generate role-specific plans from that context.
    """
    import json as _json
    import re as _re

    log = logging.getLogger("ExplorerNode")
    model_name = state.get("model_name", "anthropic:claude-sonnet-4-20250514")
    instance_id = state["instance_id"]
    problem_statement = state["problem_statement"]
    log_dir = state.get("log_dir", "logs")
    detailed_log = DetailedLogger("explorer", instance_id, log_dir, model_name=model_name, max_cost=state.get("max_cost", 3.0))

    log.info(f"[Explorer] Starting shared exploration for {instance_id}")
    detailed_log.log_start({"instance_id": instance_id, "mode": "shared-exploration"})

    # Set up Docker
    docker_env, owns_docker = _setup_planner_docker(state, "explorer", log)

    try:
        # --- Phase 1: Explore with tools ---
        explored_files = []

        def _on_view_file(path):
            if path not in explored_files:
                explored_files.append(path)

        def _on_submit(ctx_str):
            pass  # handled below

        all_tools = {
            "find_files": make_find_files(docker_env),
            "grep_content": make_grep_content(docker_env),
            "view_file": make_view_file(docker_env, max_lines_cap=200, on_view_callback=_on_view_file, truncate_output=state.get("truncate_view", False)),
            "list_dir": make_list_dir(docker_env),
            "view_symbol": make_view_symbol(docker_env),
            "view_outline": make_view_outline(docker_env),
            "trace_call_chain": make_trace_call_chain(docker_env),
        }

        # Build submit_context tool
        context_submitted = [False]
        context_result = [None]

        @tool
        def submit_context(context: str) -> str:
            """Submit your exploration context as a JSON string.

            Args:
                context: JSON string with your findings
            """
            try:
                ctx_obj = _json.loads(context)
                if not isinstance(ctx_obj, dict):
                    return "[ERROR] Context must be a JSON object"
            except _json.JSONDecodeError:
                # Try to fix common JSON issues
                fixed = context
                fixed = _re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r'\\\\', fixed)
                fixed = _re.sub(r',\s*([}\]])', r'\1', fixed)
                try:
                    ctx_obj = _json.loads(fixed)
                    context = fixed
                except _json.JSONDecodeError as e:
                    return f"[ERROR] Context is not valid JSON: {e}"

            context_result[0] = context
            context_submitted[0] = True
            return "CONTEXT_SUBMITTED: Your exploration context has been recorded."

        all_tools["submit_context"] = submit_context

        explorer_prompt = get_prompt("explorer", "system_prompt", problem_statement=problem_statement)
        if not explorer_prompt:
            raise ValueError("Missing explorer.system_prompt in prompts.yaml")

        context_schema = get_prompt("explorer", "context_schema") or ""

        bound_tools = list(all_tools.values())
        model = create_chat_model(model_name)
        model_with_tools = model.bind_tools(bound_tools)

        window_manager = create_window_manager(
            model_name=model_name,
            summarization_threshold=0.80,
            min_recent_turns=4,
        )
        window_manager.set_system_message(explorer_prompt)
        window_manager.set_plan_message(
            f"Explore the codebase to understand the bug. When done, call submit_context() with your findings.\n\n"
            f"{context_schema}"
        )

        max_steps = state.get("explorer_max_steps", 8)
        tool_dispatch = {t.name: t for t in bound_tools}

        for step_num in range(1, max_steps + 1):
            if context_submitted[0]:
                log.info(f"[Explorer] Context submitted at step {step_num - 1}")
                break

            log.info(f"[Explorer] Step {step_num}/{max_steps}")
            detailed_log.log_step(step_num, f"Exploration step {step_num}/{max_steps}")

            try:
                resp = model_with_tools.invoke(window_manager.get_messages())
            except Exception as e:
                log.error(f"[Explorer] LLM call failed: {e}")
                break

            if getattr(resp, "tool_calls", None):
                tool_msgs = []
                for c in resp.tool_calls:
                    fn = tool_dispatch.get(c["name"])
                    if fn:
                        try:
                            out = fn.invoke(c["args"])
                            log.info(f"[Explorer] Tool {c['name']} called")
                            detailed_log.log_tool_call(c["name"], c["args"], str(out))
                            tool_msgs.append(ToolMessage(content=out, tool_call_id=c["id"]))
                        except Exception as e:
                            tool_msgs.append(ToolMessage(content=f"Error: {e}", tool_call_id=c["id"]))
                    else:
                        tool_msgs.append(ToolMessage(content=f"Unknown tool: {c['name']}", tool_call_id=c["id"]))

                window_manager.add_message(resp, increment_turn=True)
                for tm in tool_msgs:
                    window_manager.add_message(tm)
            else:
                window_manager.add_message(resp, increment_turn=True)

        # Force context submission if not done
        if not context_submitted[0]:
            log.warning("[Explorer] Max steps reached, forcing context submission")
            findings_text = ", ".join(explored_files[:20]) if explored_files else "none"
            # Gather findings from conversation
            findings = []
            for msg in window_manager.get_messages()[-10:]:
                if hasattr(msg, 'content') and isinstance(msg.content, str) and len(msg.content) > 20:
                    content = msg.content[:300]
                    if any(kw in content.lower() for kw in ['found', 'match', 'def ', 'class ', 'error', 'bug']):
                        findings.append(content)
            findings_summary = "\n".join(findings[:5]) if findings else "See explored files."

            model_submit_only = model.bind_tools([submit_context])
            force_resp = model_submit_only.invoke([
                SystemMessage(content="You are a code explorer. Call submit_context() with your JSON findings."),
                HumanMessage(content=(
                    f"Problem:\n{problem_statement[:2000]}\n\n"
                    f"Files explored: {findings_text}\n\n"
                    f"Findings:\n{findings_summary}\n\n"
                    f"{context_schema}\n\n"
                    f"Call submit_context() with your JSON now."
                )),
            ])

            if getattr(force_resp, "tool_calls", None):
                for c in force_resp.tool_calls:
                    if c["name"] == "submit_context":
                        result = submit_context.invoke(c["args"])
                        log.info(f"[Explorer] Force submit result: {result}")

            # Extract from text if still not submitted
            if not context_submitted[0]:
                text = force_resp.content if isinstance(force_resp.content, str) else str(force_resp.content)
                if text.strip():
                    if "```" in text:
                        match = _re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
                        if match:
                            text = match.group(1).strip()
                    json_match = _re.search(r'\{.*\}', text, _re.DOTALL)
                    if json_match:
                        try:
                            obj = _json.loads(json_match.group(0))
                            if isinstance(obj, dict):
                                context_result[0] = _json.dumps(obj)
                                context_submitted[0] = True
                                log.info("[Explorer] Extracted context from text response")
                        except _json.JSONDecodeError:
                            pass

        exploration_context = context_result[0] or _json.dumps({
            "root_cause_hypothesis": "Could not determine — exploration incomplete",
            "relevant_files": [{"path": f, "relevance": "explored", "key_lines": ""} for f in explored_files[:5]],
            "call_chain": "",
            "existing_tests": [],
            "fix_hint": "",
        })

        log.info(f"[Explorer] Exploration complete. Context: {exploration_context[:200]}...")
        detailed_log.log_plan(f"Exploration Context:\n{exploration_context}")

        # --- Phase 2: Generate three plans in parallel (single-shot, no tools) ---
        log.info("[Explorer] Generating role-specific plans in parallel from shared context...")

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _generate_plan(role):
            plan_prompt = get_prompt("plan_generator", f"{role}_prompt",
                                     problem_statement=problem_statement,
                                     exploration_context=exploration_context)
            if not plan_prompt:
                log.warning(f"[Explorer] Missing plan_generator.{role}_prompt, skipping")
                return role, ""

            try:
                resp = model.invoke([
                    SystemMessage(content=f"You are a {role} planner. Return ONLY a valid JSON plan."),
                    HumanMessage(content=plan_prompt),
                ])
                text = resp.content if isinstance(resp.content, str) else str(resp.content)

                if "```" in text:
                    match = _re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
                    if match:
                        text = match.group(1).strip()
                json_match = _re.search(r'\{.*\}', text, _re.DOTALL)
                if json_match:
                    plan_obj = _json.loads(json_match.group(0))
                    log.info(f"[Explorer] {role} plan generated successfully")
                    return role, _json.dumps(plan_obj)
                else:
                    log.warning(f"[Explorer] No JSON found in {role} plan response")
                    return role, ""
            except Exception as e:
                log.error(f"[Explorer] Failed to generate {role} plan: {e}")
                return role, ""

        plans = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(_generate_plan, role): role for role in ["localizer", "patch_editor", "reproducer"]}
            for future in as_completed(futures):
                role, plan = future.result()
                plans[role] = plan
                if detailed_log:
                    detailed_log.log_plan(f"{role.title()} Plan:\n{plan}")

    finally:
        if owns_docker and docker_env:
            docker_env.__exit__(None, None, None)
            log.info("[Explorer] Docker cleaned up")

    return {
        "localizer_plan": plans.get("localizer", ""),
        "patch_editor_plan": plans.get("patch_editor", ""),
        "reproducer_plan": plans.get("reproducer", ""),
    }


def shared_planner_node(state: dict) -> dict:
    """Shared exploration planner: one exploration pass, three plan generation calls."""
    return _explorer_node(state)


def plan_evaluation_node(state: dict) -> dict:
    """
    Evaluate plan quality and cross-plan alignment.

    Single LLM call for combined quality + alignment scoring (no entity checks).
    """
    import json
    import re
    import time as _time

    log = logging.getLogger("PlanEvaluationNode")
    log.info(f"[PlanEvaluation] Starting for {state['instance_id']}")

    localizer_plan = state.get("localizer_plan", "")
    patch_editor_plan = state.get("patch_editor_plan", "")
    reproducer_plan = state.get("reproducer_plan", "")
    model_name = state.get("model_name", "anthropic:claude-sonnet-4-20250514")

    # Single combined LLM call for quality + alignment
    combined_prompt = get_plan_evaluation_prompt(
        "combined_prompt",
        localizer_plan=localizer_plan,
        patch_editor_plan=patch_editor_plan,
        reproducer_plan=reproducer_plan,
    )

    quality_scores = {"localizer": {}, "patch_editor": {}, "reproducer": {}}
    alignment_scores = {}

    eval_start = _time.time()

    if combined_prompt:
        try:
            llm = create_chat_model(model_name)
            resp = llm.invoke([
                SystemMessage(content="You evaluate bug-fixing plan quality and alignment. Return ONLY valid JSON."),
                HumanMessage(content=combined_prompt),
            ])
            txt = resp.content if isinstance(resp.content, str) else str(resp.content)
            if "```" in txt:
                match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', txt)
                if match:
                    txt = match.group(1).strip()
            json_match = re.search(r'\{.*\}', txt, re.DOTALL)
            if json_match:
                txt = json_match.group(0)
            result = json.loads(txt)
            quality_scores = result.get("quality", quality_scores)
            alignment_scores = result.get("alignment", alignment_scores)
            log.info(f"[PlanEvaluation] Combined scores: {json.dumps(result)}")
        except Exception as e:
            log.warning(f"[PlanEvaluation] Combined evaluation failed: {e}")

    eval_elapsed = _time.time() - eval_start
    log.info(f"[PlanEvaluation] Evaluation completed in {eval_elapsed:.1f}s")

    # Compute overall quality score (average of per-plan overalls)
    plan_overalls = []
    for role_key in ["localizer", "patch_editor", "reproducer"]:
        role_scores = quality_scores.get(role_key, {})
        overall = role_scores.get("overall")
        if overall is not None:
            plan_overalls.append(float(overall))
        else:
            metrics = [v for k, v in role_scores.items() if isinstance(v, (int, float))]
            if metrics:
                plan_overalls.append(sum(metrics) / len(metrics))

    # Compute overall alignment score
    alignment_metrics = [
        alignment_scores.get("file_overlap", 0),
        alignment_scores.get("function_overlap", 0),
        alignment_scores.get("directional_check", 0),
        alignment_scores.get("reproducer_coverage", 0),
        alignment_scores.get("call_chain_coherency", 0),
    ]
    alignment_avg = sum(alignment_metrics) / len(alignment_metrics) if alignment_metrics else 0.0

    # Combined score: 60% quality, 40% alignment
    quality_avg = sum(plan_overalls) / len(plan_overalls) if plan_overalls else 0.0
    overall_score = 0.6 * quality_avg + 0.4 * alignment_avg

    if overall_score >= 0.7:
        status = "HIGH"
    elif overall_score >= 0.4:
        status = "MEDIUM"
    else:
        status = "LOW"

    issues = alignment_scores.get("issues", [])
    suggestions = alignment_scores.get("suggestions", [])

    log.info(f"[PlanEvaluation] Overall: {status} ({overall_score:.3f}) — quality={quality_avg:.3f}, alignment={alignment_avg:.3f}")

    return {
        "feasibility_status": status,
        "feasibility_score": overall_score,
        "feasibility_details": {
            "plan_quality": quality_scores,
            "plan_alignment": alignment_scores,
            "quality_avg": quality_avg,
            "alignment_avg": alignment_avg,
            "issues": issues,
            "suggestions": suggestions,
        },
    }


def plan_refinement_node(state: dict) -> dict:
    """
    Refine plans that scored LOW on evaluation.
    Makes a single LLM call with all 3 plans + evaluation feedback.
    Max 1 refinement iteration to avoid loops.
    """
    import json
    import re

    log = logging.getLogger("PlanRefinementNode")
    log.info(f"[PlanRefinement] Starting for {state['instance_id']}")

    localizer_plan = state.get("localizer_plan", "")
    patch_editor_plan = state.get("patch_editor_plan", "")
    reproducer_plan = state.get("reproducer_plan", "")
    model_name = state.get("model_name", "anthropic:claude-sonnet-4-20250514")
    feasibility_details = state.get("feasibility_details", {})

    quality_scores = json.dumps(feasibility_details.get("plan_quality", {}), indent=2)
    alignment_scores = json.dumps(feasibility_details.get("plan_alignment", {}), indent=2)
    issues = json.dumps(feasibility_details.get("issues", []), indent=2)
    suggestions = json.dumps(feasibility_details.get("suggestions", []), indent=2)

    refinement_prompt = get_plan_evaluation_prompt(
        "refinement_prompt",
        localizer_plan=localizer_plan,
        patch_editor_plan=patch_editor_plan,
        reproducer_plan=reproducer_plan,
        quality_scores=quality_scores,
        alignment_scores=alignment_scores,
        issues=issues,
        suggestions=suggestions,
    )

    if not refinement_prompt:
        log.warning("[PlanRefinement] Missing refinement_prompt in prompts.yaml, skipping")
        return {"plan_refinement_count": state.get("plan_refinement_count", 0) + 1}

    try:
        llm = create_chat_model(model_name)
        resp = llm.invoke([
            SystemMessage(content="You refine bug-fixing plans based on evaluation feedback. Return ONLY valid JSON with keys: localizer_plan, patch_editor_plan, reproducer_plan."),
            HumanMessage(content=refinement_prompt),
        ])

        txt = resp.content if isinstance(resp.content, str) else str(resp.content)
        if "```" in txt:
            match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', txt)
            if match:
                txt = match.group(1).strip()
        json_match = re.search(r'\{.*\}', txt, re.DOTALL)
        if json_match:
            txt = json_match.group(0)

        refined = json.loads(txt)

        result = {"plan_refinement_count": state.get("plan_refinement_count", 0) + 1}

        for role_key in ["localizer_plan", "patch_editor_plan", "reproducer_plan"]:
            plan = refined.get(role_key, {})
            if isinstance(plan, dict) and "steps" in plan:
                result[role_key] = json.dumps(plan)
                log.info(f"[PlanRefinement] Refined {role_key}: {json.dumps(plan)}")
            else:
                log.warning(f"[PlanRefinement] {role_key} not refined (invalid or missing)")

        return result

    except Exception as e:
        log.error(f"[PlanRefinement] Refinement failed: {e}")
        return {"plan_refinement_count": state.get("plan_refinement_count", 0) + 1}


def route_after_evaluation(state: dict) -> str:
    """Route based on evaluation scores."""
    status = state.get("feasibility_status", "LOW")
    refinement_count = state.get("plan_refinement_count", 0)

    log = logging.getLogger("RouteAfterEvaluation")

    if status == "LOW" and refinement_count < 1:
        log.info(f"[Route] LOW feasibility, refinement_count={refinement_count} → plan_refinement")
        return "plan_refinement"
    elif status == "HIGH":
        log.info(f"[Route] HIGH feasibility → execute (localizer will skip)")
        return "execute"
    else:
        log.info(f"[Route] {status} feasibility → execute")
        return "execute"


def localizer_node(state: dict) -> dict:
    """
    Parallel node 1: Localize the bug in the codebase.
    Can leverage context from reproducer if available.
    Uses dynamic step allocation and filtered file list for efficiency.
    Runs after iterative refinement to localize the bug using the refined plans.

    SKIP LOGIC: When feasibility is HIGH, localizer is skipped because:
    1. Plans have high alignment (localizer and patch target same areas)
    2. Patch plan is already specific with concrete file/function mentions
    3. Running localizer would be redundant - patch editor already knows the target
    """
    log = logging.getLogger("LocalizerNode")
    comm_log = AgentCommunicationLogger("Localizer")

    log.info(f"[Localizer] Starting for {state['instance_id']}")

    # ==========================================================================
    # SKIP LOGIC: When feasibility is HIGH, skip localizer
    # ==========================================================================
    feasibility_status = state.get("feasibility_status", "")
    feasibility_score = state.get("feasibility_score", 0.0)
    feasibility_details = state.get("feasibility_details", {})

    if feasibility_status == "HIGH" and not state.get("force_localizer", False):
        # Extract files from patch_editor plan or triage results
        import json as _json
        patch_files = []
        patch_entities = []
        try:
            pe_plan = _json.loads(state.get("patch_editor_plan", "{}"))
            patch_files = pe_plan.get("files_to_modify", [])
            patch_entities = pe_plan.get("functions_to_modify", [])
            for step in pe_plan.get("steps", []):
                tf = step.get("target_file", "")
                if tf and tf not in patch_files:
                    patch_files.append(tf)
                tfn = step.get("target_function", "")
                if tfn and tfn not in patch_entities:
                    patch_entities.append(tfn)
        except (_json.JSONDecodeError, TypeError):
            pass

        # Fall back to triage results if no files from plan
        if not patch_files:
            patch_files = state.get("triage_buggy_files", []) or []
        if not patch_entities:
            patch_entities = state.get("triage_buggy_functions", []) or []

        # Get score breakdown for logging
        quality_avg = feasibility_details.get("quality_avg", 0) if feasibility_details else 0
        alignment_avg = feasibility_details.get("alignment_avg", 0) if feasibility_details else 0

        log.info(f"[Localizer] SKIPPING - feasibility is HIGH ({feasibility_score:.3f})")
        log.info(f"[Localizer] Reason: Plans already identify specific targets")
        log.info(f"[Localizer] Score breakdown: quality={quality_avg:.3f}, alignment={alignment_avg:.3f}")
        log.info(f"[Localizer] Returning patch plan files: {patch_files}")
        log.info(f"[Localizer] Returning patch plan entities: {patch_entities}")

        # Return files from patch plan as localizer result
        return {
            "localizer_file": patch_files if patch_files else ["(skipped - HIGH feasibility)"],
            "localizer_function": list(patch_entities) if patch_entities else [],
            "localizer_skipped": True,
            "localizer_skip_reason": f"Feasibility HIGH ({feasibility_score:.3f}) - plans already specific"
        }
    # ==========================================================================

    # Check for context from other agents
    problem_statement = state["problem_statement"]
    has_context = False
    if AgentContext.should_use_context(state, "localizer"):
        context = AgentContext.get_reproducer_context(state)
        if context:
            comm_log.log_context_received("Reproducer", context)
            problem_statement += context
            has_context = True
    else:
        comm_log.log_no_context()

    log_dir = state.get('log_dir', 'logs')
    model_name = state.get("model_name", "anthropic:claude-sonnet-4-20250514")
    max_cost = state.get("max_cost", 3.0)
    ag = LocalizerAgent(
        repo_path=state["repo_path"],
        commit=state["base_commit"],
        problem=problem_statement,
        log_path=f"{log_dir}/{state['instance_id']}_localizer.log",
        log_dir=log_dir,  # Pass log_dir for JSONL detailed logs
        plan=state.get("localizer_plan", ""),
        instance_id=state["instance_id"],
        shared_docker_id=state.get("localizer_docker_id") or state.get("shared_docker_id"),  # Dedicated container
        model_name=model_name,
        max_cost=max_cost,
        message_bus=state.get("message_bus"),  # Real-time inter-agent communication
        image_name=state.get("image_key"),
        last_n_observations=state.get("last_n_observations", 0),
        truncate_view=state.get("truncate_view", False),
    )
    result = ag.run()

    log.info(f"[Localizer] Completed. Files: {result.get('localizer_file', [])}")
    comm_log.log_context_sent(str(result.get('localizer_file', [])))

    return result


def reproducer_node(state: dict) -> dict:
    """
    Parallel node 2: Intelligent reproducer agent that explores to reproduce the issue.
    Uses ReproducerAgent with tools to interactively analyze, run tests, and create reproduction scripts.
    """
    log = logging.getLogger("ReproducerNode")
    comm_log = AgentCommunicationLogger("Reproducer")

    log.info(f"[Reproducer] Starting intelligent reproduction for {state['instance_id']}")

    instance_id = state["instance_id"]
    problem_statement = state.get("problem_statement", "")
    # Note: reproducer_plan is generated but NOT passed to agent execution
    # The agent explores freely without being constrained by a plan
    log_dir = state.get('log_dir', 'logs')
    model_name = state.get("model_name", "anthropic:claude-sonnet-4-20250514")

    # Use dedicated Docker container (fall back to shared)
    shared_docker_id = state.get("reproducer_docker_id") or state.get("shared_docker_id")
    if not shared_docker_id:
        log.error("[Reproducer] No Docker container available")
        return {
            "reproducer_status": ["error"],
            "reproducer_output": ["No Docker container available"],
            "done_count": 1,
        }

    # Create and run ReproducerAgent with plan guidance and step limit
    reproducer_plan = state.get("reproducer_plan", "")
    ag = ReproducerAgent(
        repo_path=state["repo_path"],
        problem=problem_statement,
        instance_id=instance_id,
        log_path=f"{log_dir}/{instance_id}_reproducer.log",
        log_dir=log_dir,
        plan=reproducer_plan or None,  # Pass plan for guided execution
        shared_docker_id=shared_docker_id,
        model_name=model_name,
        message_bus=state.get("message_bus"),  # Real-time inter-agent communication
        image_name=state.get("image_key"),
        last_n_observations=state.get("last_n_observations", 0),
        max_cost=state.get("max_cost", 3.0),
    )
    result = ag.run()

    log.info(f"[Reproducer] Completed. Status: {result.get('reproducer_status', [])}")
    comm_log.log_context_sent(str(result.get('reproducer_output', [])[:500] if result.get('reproducer_output') else ""))

    return result


def patch_editor_node(state: dict) -> dict:
    """
    Parallel node 3: Directly attempt to create a patch.
    Leverages context from localizer and reproducer if available.
    This agent benefits most from context sharing as it can use
    localization and reproduction results to create better patches.
    Uses dynamic step allocation based on available context.
    """
    log = logging.getLogger("PatchEditorNode")
    comm_log = AgentCommunicationLogger("PatchEditor")

    log.info(f"[PatchEditor] Starting for {state['instance_id']}")

    # Get all available context from other agents
    problem_statement = state["problem_statement"]
    has_localizer_files = False
    localizer_files = state.get("localizer_file", [])

    # Check if localizer found files
    if localizer_files and localizer_files[0] != "(not found)":
        has_localizer_files = True
        # Inject priority files at the START of problem statement
        priority_section = "\n\n==============================================================================\n"
        priority_section += "PRIORITY FILES (Found by Localizer - START HERE!):\n"
        priority_section += "==============================================================================\n"
        for f in localizer_files:
            if f and f != "(not found)":
                priority_section += f"  - {f}\n"
        priority_section += "\nIMPORTANT: View these files FIRST before exploring elsewhere.\n"
        priority_section += "==============================================================================\n"
        problem_statement = problem_statement + priority_section
        log.info(f"[PatchEditor] Injected {len(localizer_files)} priority files from localizer")

    if AgentContext.should_use_context(state, "patch_editor"):
        all_context = AgentContext.get_all_context(state)
        if all_context:
            comm_log.log_context_received("All agents", all_context)
            problem_statement += "\n\n" + all_context
        else:
            comm_log.log_no_context()
    else:
        comm_log.log_no_context()

    log_dir = state.get('log_dir', 'logs')
    model_name = state.get("model_name", "anthropic:claude-sonnet-4-20250514")
    max_cost = state.get("max_cost", 3.0)
    ag = PatchEditorAgent(
        repo_path=state["repo_path"],
        commit=state["base_commit"],
        problem=problem_statement,
        log_path=f"{log_dir}/{state['instance_id']}_patch_editor.log",
        log_dir=log_dir,  # Pass log_dir for JSONL detailed logs
        plan=state.get("patch_editor_plan", ""),
        instance_id=state["instance_id"],
        shared_docker_id=state.get("patch_editor_docker_id") or state.get("shared_docker_id"),  # Dedicated container
        model_name=model_name,
        max_cost=max_cost,
        message_bus=state.get("message_bus"),  # Real-time inter-agent communication
        image_name=state.get("image_key"),
        last_n_observations=state.get("last_n_observations", 0),
    )
    result = ag.run()

    log.info(f"[PatchEditor] Completed. Modified: {result.get('patch_editor_modified_file', [])}")
    comm_log.log_context_sent(str(result.get('patch_editor_modified_file', [])))

    return result


def collector_node(state: dict) -> dict:
    """
    Collector node: Aggregates results from all parallel agents.
    Logs summary of all agent results and file coordination.
    """
    log = logging.getLogger("CollectorNode")
    log.info(f"[Collector] Gathering results for {state['instance_id']}")

    # Log summary
    log.info(f"  Localizer files: {state.get('localizer_file', [])}")
    log.info(f"  Reproducer status: {state.get('reproducer_status', [])}")
    log.info(f"  Patch editor files: {state.get('patch_editor_modified_file', [])}")
    log.info(f"  Done count: {state.get('done_count', 0)}")

    # Log file coordination summary
    file_coordinator = state.get('file_coordinator')
    if file_coordinator:
        summary = file_coordinator.get_summary()
        log.info(f"\n{summary}")
    else:
        log.info("  No file coordinator available")

    # Clean up all Docker containers in parallel
    import subprocess
    from concurrent.futures import ThreadPoolExecutor

    def _stop_container(key_cid):
        key, cid = key_cid
        try:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=15)
            log.info(f"[Collector] Removed container {cid[:12]} ({key})")
        except Exception as e:
            log.warning(f"[Collector] Failed to clean up container {cid[:12]} ({key}): {e}")

    containers = [(k, state.get(k)) for k in ["shared_docker_id", "localizer_docker_id", "reproducer_docker_id", "patch_editor_docker_id"] if state.get(k)]
    if containers:
        with ThreadPoolExecutor(max_workers=len(containers)) as executor:
            list(executor.map(_stop_container, containers))

    return {}


# NOTE: extract_repo_structure_from_docker, _identify_buggy_functions_with_llm,
# and _trace_call_chains were removed — repo context preprocessing is no longer used.
# Agents discover files directly via Docker tools during execution.


def _execution_entry_node(state: dict) -> dict:
    """Pass-through node to enable fan-out to all execution agents after plan evaluation."""
    log = logging.getLogger("ExecutionEntry")
    score = state.get('feasibility_score')
    score_str = f"{score:.3f}" if score is not None else "N/A"
    log.info(f"[ExecutionEntry] Starting execution phase. Feasibility: {state.get('feasibility_status', 'N/A')} ({score_str})")
    return {}


def _execution_entry_no_localizer(state: dict) -> dict:
    """Pass-through for triage_sequential: skip localizer since it already ran."""
    log = logging.getLogger("ExecutionEntry")
    log.info("[ExecutionEntry] Starting editor+reproducer (localizer already ran)")
    return {}


def _route_after_triage(state: dict) -> str:
    """Route after triage: skip localizer only when feasibility is HIGH (localization confirmed by triage LLM)."""
    if state.get("feasibility_status") == "HIGH":
        return "execute"
    else:
        return "localize_first"


def compile_graph(no_plan: bool = False, shared_plan: bool = False, triage: bool = False,
                   triage_sequential: bool = False, triage_sequential_always: bool = False):
    """
    Compile the parallel multi-agent graph.

    When triage=True:
      START → repo → triage (one LLM call, no tools)
        → execution_entry → [localizer, reproducer, patch_editor] → collector → END
      Triage analyzes the issue and provides context to agents. Skips localizer if files are known.

    When triage_sequential=True:
      START → repo → triage → conditional routing:
        - HIGH feasibility (files known): → execution_entry → [localizer(skip), reproducer, patch_editor] → collector → END
        - MEDIUM/LOW (files unknown): → localizer (runs first) → execution_entry_no_localizer → [reproducer, patch_editor] → collector → END
      This gives the localizer time to find files before the editor starts, avoiding blind exploration.

    When triage_sequential_always=True:
      START → repo → triage → localizer (always runs first) → execution_entry_no_localizer → [reproducer, patch_editor] → collector → END
      Localizer ALWAYS runs first regardless of feasibility, then editor+reproducer run in parallel.

    When no_plan=False, shared_plan=False (default):
      START → repo → [localizer_planner, patch_editor_planner, reproducer_planner]
        → plan_evaluation → (conditional: plan_refinement OR execution_entry)
        → [localizer, reproducer, patch_editor] → collector → END

    When shared_plan=True:
      START → repo → shared_planner (one exploration + three single-shot plan calls)
        → plan_evaluation → (conditional: plan_refinement OR execution_entry)
        → [localizer, reproducer, patch_editor] → collector → END

    When no_plan=True:
      START → repo → execution_entry → [localizer, reproducer, patch_editor] → collector → END
      Planners are skipped entirely; agents explore on their own.
    """
    g = StateGraph(GraphState)

    # Common nodes
    g.add_node("repo", repo_node)
    g.add_node("execution_entry", _execution_entry_node)
    g.add_node("localizer", localizer_node)
    g.add_node("reproducer", reproducer_node)
    g.add_node("patch_editor", patch_editor_node)
    g.add_node("collector", collector_node)

    # Sequential: START -> repo
    g.add_edge(START, "repo")

    log = logging.getLogger("GraphCompiler")

    if no_plan:
        # Skip planners: repo -> execution_entry directly
        g.add_edge("repo", "execution_entry")
        log.info("Graph compiled (NO PLAN): repo -> [localizer, reproducer, patch_editor] -> collector -> END")
    elif triage_sequential:
        # Triage sequential: triage decides whether localizer runs first
        # Uses a separate "localizer_first" node to avoid edge conflicts with the parallel path
        g.add_node("triage", triage_node)
        g.add_node("localizer_first", localizer_node)  # same function, different node name
        g.add_node("execution_entry_no_localizer", _execution_entry_no_localizer)
        g.add_edge("repo", "triage")

        # Conditional: HIGH → all parallel, MEDIUM/LOW → localizer first then editor+reproducer
        g.add_conditional_edges("triage", _route_after_triage, {
            "execute": "execution_entry",              # HIGH: all 3 in parallel (localizer will skip)
            "localize_first": "localizer_first",       # MEDIUM/LOW: localizer runs first
        })

        # After localizer_first finishes, run editor + reproducer
        g.add_edge("localizer_first", "execution_entry_no_localizer")
        g.add_edge("execution_entry_no_localizer", "reproducer")
        g.add_edge("execution_entry_no_localizer", "patch_editor")

        # localizer_first → collector (not through execution_entry)
        # reproducer/patch_editor → collector as usual
        g.add_edge("reproducer", "collector")
        g.add_edge("patch_editor", "collector")

        log.info("Graph compiled (TRIAGE SEQUENTIAL): repo -> triage -> [localizer first if needed] -> [reproducer, patch_editor] -> collector -> END")
    elif triage_sequential_always:
        # Triage sequential always: localizer ALWAYS runs first, then editor+reproducer
        def _force_localizer_node(state: dict) -> dict:
            """Set force_localizer flag so localizer doesn't skip on HIGH feasibility."""
            return {"force_localizer": True}

        g.add_node("triage", triage_node)
        g.add_node("force_localizer_flag", _force_localizer_node)
        g.add_node("localizer_first", localizer_node)  # same function, different node name
        g.add_node("execution_entry_no_localizer", _execution_entry_no_localizer)
        g.add_edge("repo", "triage")

        # Always route to localizer first, regardless of feasibility
        g.add_edge("triage", "force_localizer_flag")
        g.add_edge("force_localizer_flag", "localizer_first")

        # After localizer_first finishes, run editor + reproducer
        g.add_edge("localizer_first", "execution_entry_no_localizer")
        g.add_edge("execution_entry_no_localizer", "reproducer")
        g.add_edge("execution_entry_no_localizer", "patch_editor")

        g.add_edge("reproducer", "collector")
        g.add_edge("patch_editor", "collector")

        log.info("Graph compiled (TRIAGE SEQUENTIAL ALWAYS): repo -> triage -> localizer (always) -> [reproducer, patch_editor] -> collector -> END")
    elif triage:
        # Triage: one LLM call -> straight to execution (all parallel)
        g.add_node("triage", triage_node)
        g.add_edge("repo", "triage")
        g.add_edge("triage", "execution_entry")
        log.info("Graph compiled (TRIAGE): repo -> triage -> [localizer, reproducer, patch_editor] -> collector -> END")
    elif shared_plan:
        # Shared exploration: one explorer -> plan_evaluation
        g.add_node("shared_planner", shared_planner_node)
        g.add_node("plan_evaluation", plan_evaluation_node)
        g.add_node("plan_refinement", plan_refinement_node)

        g.add_edge("repo", "shared_planner")
        g.add_edge("shared_planner", "plan_evaluation")

        # Conditional routing after evaluation
        g.add_conditional_edges("plan_evaluation", route_after_evaluation, {
            "plan_refinement": "plan_refinement",
            "execute": "execution_entry",
        })

        # Refinement -> re-evaluate
        g.add_edge("plan_refinement", "plan_evaluation")
        log.info("Graph compiled (SHARED PLAN): repo -> shared_planner -> plan_evaluation -> [localizer, reproducer, patch_editor] -> collector -> END")
    else:
        # Original: 3 parallel planners
        g.add_node("localizer_planner", localizer_planner_node)
        g.add_node("patch_editor_planner", patch_editor_planner_node)
        g.add_node("reproducer_planner", reproducer_planner_node)
        g.add_node("plan_evaluation", plan_evaluation_node)
        g.add_node("plan_refinement", plan_refinement_node)

        # Parallel planning: repo -> 3 planners
        g.add_edge("repo", "localizer_planner")
        g.add_edge("repo", "patch_editor_planner")
        g.add_edge("repo", "reproducer_planner")

        # All 3 planners -> plan_evaluation (fan-in)
        g.add_edge("localizer_planner", "plan_evaluation")
        g.add_edge("patch_editor_planner", "plan_evaluation")
        g.add_edge("reproducer_planner", "plan_evaluation")

        # Conditional routing after evaluation
        g.add_conditional_edges("plan_evaluation", route_after_evaluation, {
            "plan_refinement": "plan_refinement",
            "execute": "execution_entry",
        })

        # Refinement -> re-evaluate
        g.add_edge("plan_refinement", "plan_evaluation")
        log.info("Graph compiled: repo -> [3 planners] -> plan_evaluation -> [localizer, reproducer, patch_editor] -> collector -> END")

    if not triage_sequential:
        # Standard fan-out: execution_entry -> all 3 agents in parallel
        g.add_edge("execution_entry", "localizer")
        g.add_edge("execution_entry", "reproducer")
        g.add_edge("execution_entry", "patch_editor")

        # All execution agents -> collector -> END
        g.add_edge("localizer", "collector")
        g.add_edge("reproducer", "collector")
        g.add_edge("patch_editor", "collector")
    else:
        # triage_sequential: execution_entry only used for HIGH path (all 3 parallel)
        g.add_edge("execution_entry", "localizer")
        g.add_edge("execution_entry", "reproducer")
        g.add_edge("execution_entry", "patch_editor")

        # HIGH path: localizer → collector (it will skip quickly)
        g.add_edge("localizer", "collector")
        # reproducer/patch_editor → collector already added above in triage_sequential block

    g.add_edge("collector", END)

    return g.compile()
