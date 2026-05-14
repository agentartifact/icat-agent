from __future__ import annotations
from pathlib import Path
import time
import logging
from typing import Any, Dict, List, Optional
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from .utils import DetailedLogger, create_chat_model, enable_anthropic_tool_caching
from .docker_env import DockerEnvironment
from .context_sharing import SharedContextManager, AgentMessageBus
from .prompt_loader import get_patch_editor_prompt
from .context_window import SlidingWindowManager, create_window_manager
from .tools import (
    make_list_dir, make_view_file, make_view_symbol, make_view_outline,
    make_edit_file, make_search_replace, make_grep_content,
    make_trace_call_chain,
    make_share_findings, make_check_findings,
)

class PatchEditorAgent:
    def __init__(
        self,
        repo_path: str,
        commit: str,
        problem: str,
        max_steps=200,
        log_path="patch_editor.log",
        log_dir: str = "logs",  # Directory for detailed JSONL logs
        plan: Optional[str] = None,
        instance_id: str = "unknown",
        shared_docker_id: Optional[str] = None,  # Shared container ID
        model_name: str = "anthropic:claude-sonnet-4-20250514",  # model to use
        max_cost: float = 3.0,  # maximum cost per instance in USD
        enable_sliding_window: bool = True,  # Enable sliding window context management
        enable_prompt_caching: bool = True,  # Enable Anthropic prompt caching
        message_bus: Optional[AgentMessageBus] = None,  # Real-time inter-agent communication
        image_name: Optional[str] = None,
        last_n_observations: int = 0,
        truncate_view: bool = False,
    ):
        self.repo_path = Path(repo_path)
        self.commit = commit
        self.problem = problem
        self.max_steps = max_steps
        self.instance_id = instance_id
        self.shared_docker_id = shared_docker_id  # Use shared container
        self.model_name = model_name
        self.message_bus = message_bus  # Real-time inter-agent communication
        self.image_name = image_name
        self.truncate_view = truncate_view
        self._last_bus_check_time = 0.0  # Track when we last checked for messages
        self._has_made_edits = False  # Track if we've already made file edits
        self._validation_passed = False  # Track if reproducer confirmed patch works
        self._patch_shared = False  # Track if we've shared findings after editing
        self._validation_failure_count = 0  # Track consecutive validation failures
        # Convergence monitor: count consecutive reads per file without an edit.
        self._file_view_counts: dict = {}
        self._file_last_nudge: dict = {}  # last threshold at which we nudged for that file
        self._total_view_without_edit: int = 0  # Global counter of read-only steps without any edit
        self._total_view_nudge_threshold: int = 80  # First nudge at 80 read-only steps
        self.enable_sliding_window = enable_sliding_window
        self.enable_prompt_caching = enable_prompt_caching

        # Docker environment for file operations
        self.docker_env = None
        self.owns_docker = False  # Track if we own the docker env

        # Sliding window manager for context management
        self.window_manager: Optional[SlidingWindowManager] = None
        if enable_sliding_window:
            self.window_manager = create_window_manager(
                model_name=model_name,
                enable_prompt_caching=enable_prompt_caching,
                summarization_threshold=0.85,
                min_recent_turns=4,  # Keep more turns for patch editor (needs edit context)
                last_n_observations=last_n_observations,
            )

        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(exist_ok=True, parents=True)
        with open(self.log_path, "w") as f:
            f.write(f"[PatchEditorAgent] {time.ctime()}\n")

        # Initialize detailed logging
        self.detailed_log = DetailedLogger("patch_editor", instance_id, log_dir=log_dir, model_name=model_name, max_cost=max_cost)
        self.logger = logging.getLogger(f"PatchEditorAgent.{instance_id}")

        # Shared context for inter-agent communication
        self.shared_context = SharedContextManager.get_instance(instance_id, log_dir)

        # tools — all from shared factories in agent/tools.py
        self.list_dir = make_list_dir(self.docker_env)
        self.view_file = make_view_file(self.docker_env, max_lines_cap=100, truncate_output=getattr(self, "truncate_view", False))
        self.edit_file = make_edit_file(self.docker_env)
        self.search_replace = make_search_replace(self.docker_env)
        self.view_symbol = make_view_symbol(self.docker_env)
        self.view_outline = make_view_outline(self.docker_env)
        self.grep_content = make_grep_content(self.docker_env)
        self.trace_call_chain = make_trace_call_chain(self.docker_env)
        self.share_findings = make_share_findings(
            self.message_bus, agent_name="patch_editor",
            wait_for_validation=True,
            docker_env=self.docker_env,
        )
        self.check_findings = make_check_findings(self.message_bus, agent_name="patch_editor")

        self.model = create_chat_model(model_name)
        all_tools = [self.list_dir, self.view_file, self.edit_file, self.search_replace, self.view_symbol, self.view_outline, self.grep_content, self.trace_call_chain, self.share_findings, self.check_findings]
        self.model_with_tools = enable_anthropic_tool_caching(
            self.model.bind_tools(all_tools), self.model_name
        )

        # Build system prompt (role identity only) and initial message (everything else)
        system_prompt = get_patch_editor_prompt("system_prompt")
        if not system_prompt:
            raise ValueError("Missing patch_editor.system_prompt in prompts.yaml")

        initial_message = get_patch_editor_prompt(
            "initial_message",
            repo_path=self.repo_path,
            commit=self.commit,
            problem=self.problem
        ) or ""

        # Initialize messages with sliding window if enabled
        if self.window_manager:
            self.window_manager.set_system_message(system_prompt)

            if plan:
                # Parse plan JSON to extract structured fields for the template
                try:
                    import json as _json
                    plan_obj = _json.loads(plan) if isinstance(plan, str) else plan
                    plan_injection = get_patch_editor_prompt(
                        "plan_injection",
                        **plan_obj,
                    )
                except (ValueError, TypeError, AttributeError):
                    # Fall back to raw plan injection if JSON parsing fails
                    plan_injection = get_patch_editor_prompt("plan_injection_raw", plan=plan)
                if not plan_injection:
                    raise ValueError("Missing patch_editor.plan_injection in prompts.yaml")
                self.window_manager.set_plan_message(initial_message + "\n\n" + plan_injection)
            else:
                no_plan_msg = get_patch_editor_prompt("no_plan_message") or ""
                self.window_manager.set_plan_message(initial_message + "\n\n" + no_plan_msg)

            # Messages property will be computed from window manager
            self.messages = self.window_manager.get_messages()
        else:
            # Original message list (no sliding window)
            self.messages = [
                SystemMessage(content=system_prompt),
            ]
            if plan:
                try:
                    import json as _json
                    plan_obj = _json.loads(plan) if isinstance(plan, str) else plan
                    plan_injection = get_patch_editor_prompt(
                        "plan_injection",
                        **plan_obj,
                    )
                except (ValueError, TypeError, AttributeError):
                    plan_injection = get_patch_editor_prompt("plan_injection_raw", plan=plan)
                if not plan_injection:
                    raise ValueError("Missing patch_editor.plan_injection in prompts.yaml")
                self.messages.append(HumanMessage(initial_message + "\n\n" + plan_injection))
            else:
                no_plan_msg = get_patch_editor_prompt("no_plan_message") or ""
                self.messages.append(HumanMessage(initial_message + "\n\n" + no_plan_msg))

    def _generate_unified_diff(self) -> str:
        """
        Generate a unified diff for all changes made in the repository.
        Returns the diff as a string in unified format.
        Uses Docker to run git diff inside the container.

        Three-level fallback:
        1. git diff --unified=3 (unstaged changes)
        2. git diff HEAD --unified=3 (staged changes)
        3. git status --porcelain + per-file diffs (diagnostic)
        """
        if self.docker_env is None:
            self.logger.error("Docker environment not initialized for git diff")
            return ""

        try:
            # Level 1: unstaged changes
            returncode, stdout, stderr = self.docker_env.run_command(
                f"cd {self.docker_env.repo_path} && git diff --unified=3",
                timeout=60
            )
            if returncode == 0 and stdout.strip():
                self.logger.info(f"Generated unified diff via git diff ({len(stdout)} bytes)")
                return stdout

            # Level 2: staged changes (in case edits were staged)
            self.logger.info("git diff empty, trying git diff HEAD...")
            returncode, stdout, stderr = self.docker_env.run_command(
                f"cd {self.docker_env.repo_path} && git diff HEAD --unified=3",
                timeout=60
            )
            if returncode == 0 and stdout.strip():
                self.logger.info(f"Generated unified diff via git diff HEAD ({len(stdout)} bytes)")
                return stdout

            # Level 3: diagnostic - check git status and diff individual files
            self.logger.warning("git diff and git diff HEAD both empty, trying per-file diagnostics...")
            returncode, status_out, _ = self.docker_env.run_command(
                f"cd {self.docker_env.repo_path} && git status --porcelain",
                timeout=30
            )
            if returncode == 0 and status_out.strip():
                self.logger.info(f"git status shows changes:\n{status_out.strip()}")
                # Try to diff each changed file individually
                diff_parts = []
                for line in status_out.strip().split('\n'):
                    line = line.strip()
                    if line and len(line) > 3:
                        filepath = line[2:].strip()
                        # Remove rename arrow if present
                        if ' -> ' in filepath:
                            filepath = filepath.split(' -> ')[-1]
                        rc, fdiff, _ = self.docker_env.run_command(
                            f"cd {self.docker_env.repo_path} && git diff --unified=3 -- '{filepath}' 2>/dev/null || git diff HEAD --unified=3 -- '{filepath}' 2>/dev/null",
                            timeout=30
                        )
                        if rc == 0 and fdiff.strip():
                            diff_parts.append(fdiff)
                if diff_parts:
                    combined = "\n".join(diff_parts)
                    self.logger.info(f"Generated unified diff via per-file fallback ({len(combined)} bytes)")
                    return combined

            self.logger.warning("No changes detected by any diff strategy")
            return ""

        except Exception as e:
            self.logger.error(f"Failed to generate unified diff: {e}")
            return ""

    # Message types to SKIP (noisy status updates). Everything else is injected.
    _SKIP_TYPES = {
        "test_info", "status_update",
    }

    def _inject_bus_messages(self):
        """Check message bus and inject only actionable messages into context.

        Skips status updates, polling results, and other noise that wastes context tokens.
        Only injects: bug confirmation, test failures, validation results.
        """
        if not self.message_bus:
            return
        new_msgs = self.message_bus.read(
            since=self._last_bus_check_time,
            exclude_from="patch_editor",
        )
        if not new_msgs:
            return

        self._last_bus_check_time = time.time()

        # Filter out noisy messages, keep everything else
        actionable = [m for m in new_msgs if m["type"] not in self._SKIP_TYPES]
        if not actionable:
            self.logger.debug(f"[PatchEditor] Skipped {len(new_msgs)} non-actionable bus messages")
            return

        # Format compactly and detect validation status
        lines = []
        has_test_failure = False
        for m in actionable:
            data_str = str(m["data"])
            lines.append(f"[{m['from']}] {m['type']}: {data_str}")
            # Check validation signals from reproducer
            if m["type"] in ("validation_complete", "validation_passed", "validation_failed") and self._has_made_edits:
                data_text = str(m.get("data", ""))
                if m["type"] == "validation_passed" or "validated_passed" in data_text:
                    self._validation_passed = True
                elif "fail" in data_text.lower() or m["type"] == "validation_failed":
                    has_test_failure = True
            elif m["type"] in ("regression_test_results", "patch_validation"):
                data_text = str(m.get("data", ""))
                if "FAIL" in data_text or "failed" in data_text:
                    has_test_failure = True

        content = f"[UPDATE from other agents]\n" + "\n".join(lines)

        if has_test_failure and self._has_made_edits:
            content += "\nTest failures detected — analyze the failures and fix your patch."

        injection = HumanMessage(content=content)
        if self.window_manager:
            self.window_manager.add_message(injection)
        else:
            self.messages.append(injection)
        self.logger.info(f"[PatchEditor] Injected {len(actionable)} actionable messages (skipped {len(new_msgs) - len(actionable)})")

    # ---------------- run ----------------
    def run(self):
        self.detailed_log.log_start({"max_steps": self.max_steps, "repo": str(self.repo_path)})

        # Update shared context status
        self.shared_context.update_patch_editor_status("in_progress")

        # Check context from other agents (localizer, reproducer)
        localizer_ctx = self.shared_context.get_localizer_context()
        if localizer_ctx.get("top_candidates"):
            self.logger.info(f"Using localizer context: {len(localizer_ctx['top_candidates'])} candidates")

        # Use shared Docker container if available, otherwise create new one
        if self.shared_docker_id:
            # Reuse existing container
            self.docker_env = DockerEnvironment(self.instance_id, timeout=600, image_name=self.image_name, repo_path=str(self.repo_path))
            self.docker_env.container_id = self.shared_docker_id  # Use shared container
            self.owns_docker = False
            self.logger.info(f"Using shared Docker container: {self.shared_docker_id[:12]}")
        else:
            # Create new container
            self.docker_env = DockerEnvironment(self.instance_id, timeout=600, image_name=self.image_name, repo_path=str(self.repo_path))
            self.docker_env.__enter__()
            self.owns_docker = True

            if not self.docker_env.container_id:
                self.logger.error("Failed to start Docker container for patch editor")
                return {
                    "patch_editor_modified_file": ["(none)"],
                    "patch_editor_patch": ["Docker container failed to start"],
                    "patch_editor_unified_diff": [],
                    "done_count": 1,
                }
            self.logger.info(f"Docker container started: {self.docker_env.container_id[:12]}")

        # Re-create ALL tools that depend on docker_env (they captured None at __init__ time)
        self.list_dir = make_list_dir(self.docker_env)
        self.view_file = make_view_file(self.docker_env, max_lines_cap=100, truncate_output=getattr(self, "truncate_view", False))
        self.edit_file = make_edit_file(self.docker_env)
        self.search_replace = make_search_replace(self.docker_env)
        self.view_symbol = make_view_symbol(self.docker_env)
        self.view_outline = make_view_outline(self.docker_env)
        self.grep_content = make_grep_content(self.docker_env)
        self.trace_call_chain = make_trace_call_chain(self.docker_env)
        self.share_findings = make_share_findings(
            self.message_bus, agent_name="patch_editor",
            wait_for_validation=True,
            docker_env=self.docker_env,
        )
        all_tools = [self.list_dir, self.view_file, self.edit_file, self.search_replace, self.view_symbol, self.view_outline, self.grep_content, self.trace_call_chain, self.share_findings, self.check_findings]
        self.model_with_tools = enable_anthropic_tool_caching(
            self.model.bind_tools(all_tools), self.model_name
        )
        self.logger.info("Tools re-initialized with Docker environment")

        try:
            return self._run_loop()
        finally:
            # Only clean up if we own the docker env
            if self.owns_docker:
                self.docker_env.__exit__(None, None, None)
                self.logger.info("Docker container cleaned up")

    def _run_build_check(self):
        """Run a compile/build check for the current repo. Returns error string or None."""
        if not self.docker_env:
            return None
        repo = self.docker_env.repo_path

        # Detect language and run appropriate build check
        checks = [
            # Go
            (f"test -f {repo}/go.mod", f"cd {repo} && go build ./... 2>&1 | tail -20"),
            # Java (Maven)
            (f"test -f {repo}/pom.xml", f"cd {repo} && mvn compile -q 2>&1 | tail -20"),
            # Java (Gradle)
            (f"test -f {repo}/build.gradle", f"cd {repo} && ./gradlew classes -q 2>&1 | tail -20"),
            # TypeScript
            (f"test -f {repo}/tsconfig.json", f"cd {repo} && npx tsc --noEmit 2>&1 | tail -20"),
            # Python — syntax check on modified files only (fast)
        ]

        for check_cmd, build_cmd in checks:
            rc, out, _ = self.docker_env.run_command(check_cmd, timeout=5)
            if rc == 0:
                self.logger.info(f"[PatchEditor] Running build check: {build_cmd[:60]}...")
                rc2, stdout, stderr = self.docker_env.run_command(build_cmd, timeout=120)
                output = (stdout or "") + (stderr or "")
                if rc2 != 0:
                    error_msg = f"[BUILD FAILED]\n{output.strip()}"
                    self.logger.warning(f"[PatchEditor] Build check failed: {output[:200]}")
                    return error_msg
                self.logger.info("[PatchEditor] Build check passed")
                return None

        return None  # no build system detected, skip

    def _run_loop(self):
        """Internal run loop after Docker is initialized. Step limit + cost control."""
        modified_files = []

        # Step budget milestones for reminders
        budget_milestones = {
            int(self.max_steps * 0.25): lambda s: f"This is step {s} (25%), you still have {self.max_steps - s} remaining steps before submission.",
            int(self.max_steps * 0.50): lambda s: f"This is step {s} (50%), you still have {self.max_steps - s} remaining steps before submission.",
            int(self.max_steps * 0.75): lambda s: f"This is step {s} (75%), you still have {self.max_steps - s} remaining steps before submission.",
            int(self.max_steps * 0.90): lambda s: f"This is step {s} (90%), you still have {self.max_steps - s} remaining steps before submission.",
        }

        step = 0
        while step < self.max_steps:
            step += 1
            self.logger.info(f"Step {step}/{self.max_steps}")

            # Step budget reminders
            if step in budget_milestones:
                nudge_msg = HumanMessage(content=budget_milestones[step](step))
                if self.window_manager:
                    self.window_manager.add_message(nudge_msg)
                else:
                    self.messages.append(nudge_msg)

            # Progressive nudges to encourage submission (keep existing last-minute nudges)
            if step == self.max_steps - 5:
                nudge_msg = HumanMessage(content="You are running low on steps. Finalize your patch now — generate the unified diff and submit.")
                if self.window_manager:
                    self.window_manager.add_message(nudge_msg)
                else:
                    self.messages.append(nudge_msg)
            elif step == self.max_steps - 2:
                nudge_msg = HumanMessage(content="URGENT: Submit your patch NOW. Generate the unified diff and finish immediately.")
                if self.window_manager:
                    self.window_manager.add_message(nudge_msg)
                else:
                    self.messages.append(nudge_msg)

            # Auto-inject messages from other agents every step (N=1)
            self._inject_bus_messages()

            # Early exit if reproducer already validated our patch as passed
            if getattr(self, '_validation_passed', False) and self._has_made_edits:
                self.logger.info("[PatchEditor] Validation already passed — exiting early")
                unified_diff = self._generate_unified_diff()
                result = {
                    "patch_editor_modified_file": modified_files if modified_files else ["(none)"],
                    "patch_editor_patch": ["Validation passed"],
                    "patch_editor_unified_diff": [unified_diff] if unified_diff else [],
                    "done_count": 1,
                }
                self._log_window_stats_on_exit()
                self.detailed_log.log_result(result)
                return result

            # Log sliding window stats if enabled
            if self.window_manager:
                window_stats = self.window_manager.get_token_usage()
                self.logger.debug(
                    f"Context window: {window_stats['total_tokens']}/{window_stats['max_tokens']} tokens "
                    f"({window_stats['utilization_pct']}%)"
                )

            # Get current messages (from window manager if enabled)
            current_messages = self.window_manager.get_messages() if self.window_manager else self.messages

            # Build prompt_text for logging
            if step == 1:
                prompt_text = "\n".join([f"[{m.type}]: {m.content}" for m in current_messages])
            else:
                last_msg = current_messages[-1]
                prompt_text = f"[{last_msg.type}]: {last_msg.content}"

            # Log prompt BEFORE calling LLM (so it's captured even on failure)
            self.detailed_log.logger.info(
                f"\n{'='*60}\n[patch_editor] Step {step} SENDING PROMPT ({len(current_messages)} msgs)\n{'='*60}\n{prompt_text}\n{'='*60}"
            )

            # Get LLM response
            try:
                from .base_agent import BaseAgent
                current_messages = BaseAgent._sanitize_messages(current_messages)
                resp = self.model_with_tools.invoke(current_messages)

                # Log the LLM interaction
                response_text = resp.content if isinstance(resp.content, str) else str(resp.content)
                self.detailed_log.log_llm_call(prompt_text, response_text, {"step": step, "has_tools": bool(getattr(resp, "tool_calls", None))}, raw_response=resp)

                # Check cost limit
                if self.detailed_log.should_stop():
                    self.logger.warning(f"Cost limit exceeded at step {step}, stopping early")
                    # Submit whatever patch exists rather than returning empty
                    unified_diff = self._generate_unified_diff() if self._has_made_edits else None
                    if unified_diff:
                        self.logger.info(f"[PatchEditor] Submitting last patch on cost limit ({len(unified_diff)} bytes)")
                    self._log_window_stats_on_exit()
                    return {
                        "patch_editor_modified_file": modified_files if modified_files else ["(cost_limit_exceeded)"],
                        "patch_editor_patch": ["Cost limit reached — submitting latest patch"],
                        "patch_editor_unified_diff": [unified_diff] if unified_diff else [],
                        "done_count": 1,
                    }

            except Exception as e:
                self.logger.error(f"LLM call failed: {e}")
                self.detailed_log.log_error(e, f"Step {step}")
                break

            if getattr(resp, "tool_calls", None):
                msgs = []
                for c in resp.tool_calls:
                    fn = {
                        self.list_dir.name: self.list_dir,
                        self.view_file.name: self.view_file,
                        self.edit_file.name: self.edit_file,
                        self.search_replace.name: self.search_replace,
                        self.view_symbol.name: self.view_symbol,
                        self.view_outline.name: self.view_outline,
                        self.grep_content.name: self.grep_content,
                        self.trace_call_chain.name: self.trace_call_chain,
                        self.share_findings.name: self.share_findings,
                        self.check_findings.name: self.check_findings,
                    }.get(c["name"])

                    if fn:
                        try:
                            out = fn.invoke(c["args"])
                            self.detailed_log.log_tool_call(c["name"], c["args"], out)
                            self.logger.info(f"Tool {c['name']} called with {c['args']}")

                            # Convergence monitor: track repeated reads per file.
                            _path_arg = c["args"].get("path") if isinstance(c["args"], dict) else None
                            if _path_arg:
                                if c["name"] in ("view_file", "view_outline", "view_symbol", "grep_content"):
                                    self._file_view_counts[_path_arg] = self._file_view_counts.get(_path_arg, 0) + 1
                                    self._total_view_without_edit += 1
                                elif c["name"] in ("edit_file", "search_replace"):
                                    # An edit resets the read counter and the nudge state for this file.
                                    self._file_view_counts.pop(_path_arg, None)
                                    self._file_last_nudge.pop(_path_arg, None)
                                    self._total_view_without_edit = 0
                                    self._total_view_nudge_threshold = 80  # Reset threshold

                            # Track modified files and shared findings
                            if c["name"] in ("edit_file", "search_replace") and "path" in c["args"]:
                                modified_files.append(c["args"]["path"])
                                self._has_made_edits = True
                            elif c["name"] == "share_findings" and self._has_made_edits:
                                self._patch_shared = True
                                if self.message_bus and c["args"].get("finding_type") == "patch_generated":
                                    # Step 1: Build check
                                    build_error = self._run_build_check()
                                    if build_error:
                                        out += f"\n\n{build_error}\nFix the build errors before submitting the patch."
                                        self.message_bus.post("patch_editor", "build_failed", build_error)
                                        self.logger.warning(f"[PatchEditor] Build check failed, not posting diff")
                                        self._patch_shared = False
                                        # Don't block — let agent see error and fix
                                    else:
                                        # Step 2: Post diff
                                        try:
                                            unified_diff = self._generate_unified_diff()
                                            if unified_diff:
                                                self.message_bus.post("patch_editor", "patch_generated", unified_diff)
                                                self.logger.info("[PatchEditor] Auto-posted unified diff to bus")
                                        except Exception as e:
                                            self.logger.warning(f"[PatchEditor] Failed to auto-post diff: {e}")

                                        # Step 3: Block-wait for reproducer validation (zero LLM cost)
                                        # Polls message bus every 5s. Exits on:
                                        #   - validation_passed/failed/complete from reproducer
                                        #   - reproducer_done signal (reproducer finished/died)
                                        #   - timeout after 1200s
                                        import time as _time
                                        self.logger.info("[PatchEditor] Waiting for reproducer validation (idle, zero cost)...")
                                        wait_result = None
                                        wait_detail = ""
                                        max_wait_seconds = 1200
                                        wait_start = _time.time()
                                        last_log = wait_start

                                        while _time.time() - wait_start < max_wait_seconds:
                                            _time.sleep(5)
                                            elapsed = _time.time() - wait_start

                                            # Log progress every 300s
                                            if _time.time() - last_log >= 300:
                                                self.logger.info(f"[PatchEditor] Still waiting for validation ({int(elapsed)}s elapsed)...")
                                                last_log = _time.time()

                                            if not self.message_bus:
                                                continue

                                            # Read new messages since last check
                                            new = self.message_bus.read(
                                                since=self._last_bus_check_time,
                                                exclude_from="patch_editor",
                                            )

                                            # Also scan ALL messages for reproducer_done (may have been
                                            # posted before our poll window started)
                                            all_msgs = self.message_bus.read(exclude_from="patch_editor")
                                            reproducer_dead = any(
                                                m["type"] in ("reproducer_done", "reproducer_complete")
                                                for m in all_msgs
                                            )

                                            for m in new:
                                                msg_type = m["type"]
                                                data_str = str(m.get("data", ""))

                                                # Validation signals
                                                if msg_type in ("validation_complete", "validation_passed", "validation_failed"):
                                                    if msg_type == "validation_passed" or "validated_passed" in data_str:
                                                        wait_result = "passed"
                                                    else:
                                                        wait_result = "failed"
                                                        wait_detail = data_str[:3000]
                                                    self._last_bus_check_time = _time.time()
                                                    break

                                            # Check if reproducer is done (from full message scan)
                                            if not wait_result and reproducer_dead:
                                                self.logger.info("[PatchEditor] Reproducer finished — stopping wait")
                                                # Check if there's a validation message we missed
                                                for m in all_msgs:
                                                    if m["type"] in ("validation_failed", "validation_complete"):
                                                        data_str = str(m.get("data", ""))
                                                        if "validated_passed" in data_str:
                                                            wait_result = "passed"
                                                        else:
                                                            wait_result = "failed"
                                                            wait_detail = data_str[:3000]
                                                        break
                                                if not wait_result:
                                                    wait_result = "reproducer_done"

                                            if wait_result:
                                                break

                                        if wait_result == "passed":
                                            self._validation_passed = True
                                            self.logger.info("[PatchEditor] Validation PASSED — exiting")
                                            unified_diff = self._generate_unified_diff()
                                            result = {
                                                "patch_editor_modified_file": modified_files if modified_files else ["(none)"],
                                                "patch_editor_patch": ["Validation passed"],
                                                "patch_editor_unified_diff": [unified_diff] if unified_diff else [],
                                                "done_count": 1,
                                            }
                                            self._log_window_stats_on_exit()
                                            self.detailed_log.log_result(result)
                                            return result
                                        elif wait_result == "failed":
                                            self._validation_failure_count += 1
                                            self.logger.info(f"[PatchEditor] Validation FAILED (attempt {self._validation_failure_count}) — resuming to revise patch")
                                            failure_msg = HumanMessage(content=(
                                                f"[VALIDATION FAILED from reproducer — attempt {self._validation_failure_count}]\n{wait_detail}\n\n"
                                                "There are validation failures. You should carefully revise your patch and reflect on what went wrong before trying again."
                                            ))
                                            if self.window_manager:
                                                self.window_manager.add_message(failure_msg)
                                            else:
                                                self.messages.append(failure_msg)
                                            self._patch_shared = False
                                            self._inject_bus_messages()
                                        elif wait_result == "reproducer_done":
                                            self.logger.info("[PatchEditor] Reproducer done — injecting any remaining feedback and resuming")
                                            self._patch_shared = False
                                            self._inject_bus_messages()
                                        else:
                                            self.logger.info("[PatchEditor] No validation after 1200s — resuming")

                            # REAL-TIME CONTEXT SHARING: Update shared context after relevant tool calls
                            self._share_tool_findings(c["name"], c["args"], out)

                            msgs.append(ToolMessage(f"OBSERVATION:\n{out}", tool_call_id=c["id"]))
                        except Exception as e:
                            error_msg = f"Tool error: {str(e)}"
                            self.logger.error(error_msg)
                            self.detailed_log.log_error(e, f"Tool {c['name']}")
                            msgs.append(ToolMessage(f"OBSERVATION:\n{error_msg}", tool_call_id=c["id"]))
                    else:
                        self.logger.warning(f"Unknown tool: {c['name']}")

                # Add messages to window manager or raw list
                if self.window_manager:
                    self.window_manager.add_message(resp, increment_turn=True)
                    for tm in msgs:
                        self.window_manager.add_message(tm)
                else:
                    self.messages.append(resp)
                    self.messages.extend(msgs)

                # Convergence monitor: if any file has been re-read 10+ times past
                # the last nudge (without an intervening edit), append a nudge.
                for _fp, _cnt in list(self._file_view_counts.items()):
                    _last = self._file_last_nudge.get(_fp, 0)
                    if _cnt >= _last + 10:
                        self._file_last_nudge[_fp] = _cnt
                        nudge = HumanMessage(content=(
                            f"NUDGE: You have reviewed {_fp} {_cnt} times. "
                            f"This may indicate you're stuck in a loop. Please carefully "
                            f"decide your next step — reflect on what you've learned and "
                            f"whether continuing to inspect this file is the right move. "
                            f"You should be more effective and efficient for the next step."
                        ))
                        self.logger.warning(f"[ConvergenceMonitor] Nudging: {_fp} viewed {_cnt} times without edit")
                        if self.window_manager:
                            self.window_manager.add_message(nudge)
                        else:
                            self.messages.append(nudge)

                # Global convergence monitor: detect excessive viewing without any edits.
                if self._total_view_without_edit >= self._total_view_nudge_threshold:
                    total_views = self._total_view_without_edit
                    global_nudge = HumanMessage(content=(
                        f"WARNING: You have performed {total_views} file viewing operations "
                        f"(view_file, grep_content, view_symbol, view_outline) without making "
                        f"a single edit. You should avoid repeatedly viewing files without doing "
                        f"anything. You should reason and act more meaningfully — based on what "
                        f"you have already read, formulate a concrete fix and use edit_file to "
                        f"implement it. Continuing to only read files wastes your budget."
                    ))
                    self.logger.warning(
                        f"[ConvergenceMonitor] Global nudge: {total_views} views without any edit"
                    )
                    if self.window_manager:
                        self.window_manager.add_message(global_nudge)
                    else:
                        self.messages.append(global_nudge)
                    # Increase threshold for next nudge (escalating: 20, 30, 40, ...)
                    self._total_view_nudge_threshold = total_views + 10

                # Auto-exit: if we've made edits + shared patch + got validation
                if self._validation_passed and self._has_made_edits:
                    self.logger.info("[PatchEditor] Auto-exit: validation passed after sharing patch")
                    unified_diff = self._generate_unified_diff()
                    result = {
                        "patch_editor_modified_file": modified_files if modified_files else ["(none)"],
                        "patch_editor_patch": ["Validation passed"],
                        "patch_editor_unified_diff": [unified_diff] if unified_diff else [],
                        "done_count": 1,
                    }
                    self._log_window_stats_on_exit()
                    self.detailed_log.log_result(result)
                    return result

                continue

            # Check for final output
            txt = resp.content if isinstance(resp.content, str) else str(resp.content)

            # Look for DONE: format (new) or modified_target/patch format (old)
            if "DONE:" in txt or ("modified_target" in txt and "patch" in txt):
                # Extract done files and patch description
                done_files = modified_files.copy()  # Files we tracked
                patch_desc = txt

                if "DONE:" in txt:
                    done_part = txt.split("DONE:")[1].split("\n")[0]
                    done_files_from_text = [f.strip() for f in done_part.split(",") if f.strip()]
                    if done_files_from_text:
                        done_files = done_files_from_text
                    if "PATCH:" in txt:
                        patch_desc = txt.split("PATCH:")[1].strip()

                # Generate unified diff
                unified_diff = self._generate_unified_diff()

                # Warn about possible silent write failure
                if done_files and not unified_diff:
                    self.logger.warning(
                        f"[EmptyDiff] Modified files {done_files} but unified diff is EMPTY. "
                        f"Possible silent write failure (heredoc corruption, base64 issue, or file not in git tracking)."
                    )

                # Save unified diff to shared context
                if unified_diff:
                    self.shared_context.set_unified_diff(unified_diff)
                    # Share patch with other agents (reproducer will validate it)
                    # Skip if _auto_share_diff already posted the same diff
                    if self.message_bus:
                        if not hasattr(self, '_last_shared_diff') or self._last_shared_diff != unified_diff:
                            self._last_shared_diff = unified_diff
                            self.message_bus.post("patch_editor", "patch_generated", unified_diff)
                            self.logger.info("[PatchEditor] Shared patch with other agents via message bus")

                        # Wait for validation result from reproducer
                        # The reproducer needs time to: apply patch, run tests, report back
                        self.logger.info("[PatchEditor] Waiting for validation from reproducer...")
                        import time as _time
                        validation_status = None  # None=timeout, "passed", "failed"
                        validation_detail = ""
                        for _wait_round in range(36):  # Up to 180 seconds (36 x 5s)
                            # Check all possible validation message types
                            for msg_type in ("validation_complete", "patch_validation", "test_results"):
                                msg = self.message_bus.get_latest(msg_type)
                                if msg:
                                    msg_data = msg.get("data", {})
                                    if isinstance(msg_data, dict):
                                        msg_str = msg_data.get("status", str(msg_data))
                                        validation_detail = msg_data.get("detailed_output", "") or msg_data.get("output", "") or str(msg_data)
                                    else:
                                        msg_str = str(msg_data)
                                        validation_detail = msg_str
                                    self.logger.info(f"[PatchEditor] Got {msg_type}: {msg_str}")
                                    validation_status = "failed" if "fail" in msg_str.lower() else "passed"
                                    break
                            if validation_status is not None:
                                break
                            self.logger.info(f"[PatchEditor] Waiting for reproducer validation... ({(_wait_round+1)*5}s)")
                            _time.sleep(5)

                        if validation_status == "failed":
                            # Validation FAILED — inject feedback and loop back for refinement
                            self.logger.info("[PatchEditor] Validation FAILED - attempting refinement")
                            refine_msg = HumanMessage(
                                content=(
                                    f"[VALIDATION RESULTS FROM REPRODUCER]\n"
                                    f"Status: FAILED\n"
                                    f"Output:\n{validation_detail[:3000]}\n\n"
                                    f"Your patch did NOT fix the bug. The reproducer applied your patch "
                                    f"and re-ran the tests — they still fail.\n"
                                    f"Please analyze the failure, make corrected edits, "
                                    f"and respond with DONE: again."
                                )
                            )
                            if self.window_manager:
                                self.window_manager.add_message(resp, increment_turn=True)
                                self.window_manager.add_message(refine_msg)
                            else:
                                self.messages.append(resp)
                                self.messages.append(refine_msg)
                            continue  # Go back to while-loop for refinement
                        elif validation_status == "passed":
                            self.logger.info("[PatchEditor] Validation PASSED — done")
                        else:
                            self.logger.info("[PatchEditor] No validation received after 180s timeout")

                result = {
                    "patch_editor_modified_file": done_files if done_files else ["(none)"],
                    "patch_editor_patch": [patch_desc] if patch_desc else ["(none)"],
                    "patch_editor_unified_diff": [unified_diff] if unified_diff else [],
                    "done_count": 1,
                }
                self._log_window_stats_on_exit()
                self.detailed_log.log_result(result)
                self.logger.info(f"Patch editing complete: {done_files}")
                return result

            # Skip empty responses to avoid context bloat and wasted LLM calls
            resp_text = resp.content if isinstance(resp.content, str) else str(resp.content)
            if not resp_text.strip() or resp_text.strip() == '[]':
                self._empty_response_count = getattr(self, '_empty_response_count', 0) + 1
                if self._empty_response_count >= 3:
                    if self._has_made_edits:
                        self.logger.info("[PatchEditor] Multiple empty responses — submitting current patch")
                        unified_diff = self._generate_unified_diff()
                        result = {
                            "patch_editor_modified_file": modified_files if modified_files else ["(none)"],
                            "patch_editor_patch": ["Auto-submitted after empty responses"],
                            "patch_editor_unified_diff": [unified_diff] if unified_diff else [],
                            "done_count": 1,
                        }
                        self._log_window_stats_on_exit()
                        self.detailed_log.log_result(result)
                        return result
                    else:
                        self.logger.warning("[PatchEditor] Multiple empty responses with no edits — stopping")
                        break
                continue
            else:
                self._empty_response_count = 0

            # Add response to message history
            if self.window_manager:
                self.window_manager.add_message(resp, increment_turn=True)
            else:
                self.messages.append(resp)

        # Safety fallback (cost check normally exits the loop)
        unified_diff = self._generate_unified_diff()

        if modified_files and not unified_diff:
            self.logger.warning(
                f"[EmptyDiff] Modified files {modified_files} but unified diff is EMPTY. "
                f"Possible silent write failure."
            )

        result = {
            "patch_editor_modified_file": modified_files if modified_files else ["(none)"],
            "patch_editor_patch": [f"Loop ended at step {step}"] if modified_files else ["(none)"],
            "patch_editor_unified_diff": [unified_diff] if unified_diff else [],
            "done_count": 1,
        }
        self._log_window_stats_on_exit()
        self.detailed_log.log_result(result)
        self.logger.warning(f"Run loop ended at step {step}. Modified files: {modified_files}")
        return result

    def _log_window_stats_on_exit(self):
        """Log sliding window statistics when agent finishes."""
        if self.window_manager:
            stats = self.window_manager.get_stats()
            self.logger.info(
                f"[SlidingWindow] Final stats: "
                f"turns={stats['current_turn']}, "
                f"tokens={stats['total_tokens']}/{stats['max_tokens']} ({stats['utilization_pct']}%), "
                f"summarizations={stats['summarizations_performed']}, "
                f"tokens_saved={stats['tokens_saved']}"
            )

    def _share_tool_findings(self, tool_name: str, args: dict, output: str):
        """
        Share tool findings with other agents in real-time.
        Called after each tool execution to update structured state.
        """
        try:
            if tool_name in ("edit_file", "search_replace"):
                file_path = args.get("path", "")
                if file_path and "error" not in output.lower() and "not found" not in output.lower():
                    self.shared_context.add_modified_file(file_path, change_type="modified")
        except Exception as e:
            self.logger.warning(f"Failed to share tool findings: {e}")

    def _auto_share_diff(self):
        """Run `git diff` in Docker and post the unified diff to the message bus.

        Called after every successful edit_file so the reproducer's
        apply_patch() always has access to a real unified diff, regardless
        of what the LLM puts in share_findings("patch_generated", ...).
        Skips posting if the diff hasn't changed since last post.
        """
        if not self.docker_env or not self.message_bus:
            return
        try:
            rc, diff_out, _ = self.docker_env.run_command(
                f"cd {self.docker_env.repo_path} && git diff"
            )
            if rc == 0 and diff_out and diff_out.strip():
                diff_stripped = diff_out.strip()
                # Skip if same diff was already posted
                if hasattr(self, '_last_shared_diff') and self._last_shared_diff == diff_stripped:
                    self.logger.debug("[AutoShareDiff] Diff unchanged, skipping re-post")
                    return
                self._last_shared_diff = diff_stripped
                self.message_bus.post("patch_editor", "patch_generated", diff_stripped)
                self.logger.info(
                    f"[AutoShareDiff] Posted unified diff ({len(diff_out)} chars) to message bus"
                )
        except Exception as e:
            self.logger.warning(f"[AutoShareDiff] Failed to share diff: {e}")
