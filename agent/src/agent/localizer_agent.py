from __future__ import annotations
from typing import Optional
from .utils import create_chat_model, enable_anthropic_tool_caching
from .base_agent import BaseAgent
from .context_sharing import AgentMessageBus
from .prompt_loader import get_localizer_prompt
from .tools import (
    make_find_files, make_grep_content, make_view_file,
    make_list_dir, make_trace_call_chain,
    make_share_findings, make_check_findings,
    make_view_symbol, make_view_outline,
)


class LocalizerAgent(BaseAgent):
    ROLE = "localizer"

    def __init__(
        self,
        repo_path: str,
        commit: str,
        problem: str,
        max_steps=200,
        log_path="localizer_agent.log",
        log_dir: str = "logs",
        plan: Optional[str] = None,
        instance_id: str = "unknown",
        shared_docker_id: Optional[str] = None,
        model_name: str = "openai:gpt-5-mini",
        max_cost: float = 3.0,
        message_bus: Optional[AgentMessageBus] = None,
        image_name: Optional[str] = None,
        last_n_observations: int = 0,
        truncate_view: bool = False,
    ):
        super().__init__(
            repo_path=repo_path, problem=problem, instance_id=instance_id,
            commit=commit, max_steps=max_steps, log_path=log_path,
            log_dir=log_dir, plan=plan, shared_docker_id=shared_docker_id,
            model_name=model_name, max_cost=max_cost, message_bus=message_bus,
            min_recent_turns=3, image_name=image_name,
            last_n_observations=last_n_observations,
        )
        self._setup_plan(
            get_localizer_prompt, "system_prompt", "plan_injection", "no_plan_message",
            repo_path=self.repo_path, commit=self.commit, problem=self.problem,
        )

    def _setup_tools(self):
        self.tools = [
            make_list_dir(self.docker_env),
            make_find_files(self.docker_env),
            make_grep_content(self.docker_env),
            make_view_file(self.docker_env, max_lines_cap=100, truncate_output=getattr(self, "truncate_view", False)),
            make_view_symbol(self.docker_env),
            make_view_outline(self.docker_env),
            make_trace_call_chain(self.docker_env),
            make_share_findings(self.message_bus, agent_name="localizer"),
            make_check_findings(self.message_bus, agent_name="localizer"),
        ]
        self.model = create_chat_model(self.model_name)
        self.model_with_tools = enable_anthropic_tool_caching(
            self.model.bind_tools(self.tools), self.model_name
        )

    def _docker_error_result(self):
        return {
            "localizer_file": ["(not found)"],
            "localizer_rationale": ["Docker container failed to start"],
            "done_count": 1,
        }

    def run(self):
        self.detailed_log.log_start({"max_steps": self.max_steps, "repo": str(self.repo_path)})
        self.shared_context.update_localizer_status("in_progress")

        if not self._setup_docker():
            return self._docker_error_result()
        self._setup_tools()

        try:
            return self._run_loop()
        finally:
            self._cleanup_docker()

    def _run_loop(self):
        self._findings_shared = False
        step = 0
        while step < self.max_steps:
            step += 1
            self.logger.info(f"Step {step}/{self.max_steps}")

            self._emit_nudges(
                step,
                "You are running low on steps. Start wrapping up — submit your localization results now using submit_results.",
                "URGENT: Submit your localization results NOW. You are almost out of steps.",
            )
            self._inject_bus_messages()
            self._log_window_stats()

            # Build prompt_text for logging
            current_messages = self._get_current_messages()
            if step == 1:
                prompt_text = "\n".join([f"[{m.type}]: {m.content}" for m in current_messages])
            else:
                last_msg = current_messages[-1]
                prompt_text = f"[{last_msg.type}]: {last_msg.content}"

            # Log prompt BEFORE calling LLM (so it's captured even on failure)
            self.detailed_log.logger.info(
                f"\n{'='*60}\n[localizer] Step {step} SENDING PROMPT ({len(current_messages)} msgs)\n{'='*60}\n{prompt_text}\n{'='*60}"
            )

            try:
                resp = self._invoke_llm()
                if resp is None:
                    continue  # transient error, retry next step
            except Exception:
                break  # 3 consecutive errors, abort

            response_text = resp.content if isinstance(resp.content, str) else str(resp.content)
            self.detailed_log.log_llm_call(prompt_text, response_text, {"step": step, "has_tools": bool(getattr(resp, "tool_calls", None))}, raw_response=resp)

            if self.detailed_log.should_stop():
                self.logger.warning(f"Cost limit exceeded at step {step}, stopping early")
                self._log_window_stats_on_exit()
                return {
                    "localizer_file": ["(cost_limit_exceeded)"],
                    "localizer_rationale": [f"Cost limit (${self.detailed_log.max_cost:.2f}) exceeded"],
                    "done_count": 1,
                }

            if self._execute_tool_calls(resp) is not None:
                # After sharing findings, exit — other agents take over
                if getattr(self, '_findings_shared', False):
                    self.logger.info("[Localizer] Findings shared — exiting")
                    result = {
                        "localizer_file": ["(findings shared via share_findings)"],
                        "localizer_rationale": ["Localization complete"],
                        "done_count": 1,
                    }
                    self._log_window_stats_on_exit()
                    self.detailed_log.log_result(result)
                    return result
                continue

            # Check for final output
            text = resp.content if isinstance(resp.content, str) else str(resp.content)
            if "FINAL:" in text or "File:" in text:
                files, rationale = [], ""
                if "FINAL:" in text:
                    final_part = text.split("FINAL:")[1].split("\n")[0]
                    files = [f.strip() for f in final_part.split(",") if f.strip()]
                    if "RATIONALE:" in text:
                        rationale = text.split("RATIONALE:")[1].strip()
                elif "File:" in text:
                    files = [text]

                for f in files:
                    self.shared_context.add_localizer_candidate(file=f, function=None, confidence=0.8, rationale=rationale)
                self.shared_context.set_localizer_findings(rationale or f"Found files: {', '.join(files)}")

                result = {
                    "localizer_file": files if files else ["(not found)"],
                    "localizer_rationale": [rationale] if rationale else [""],
                    "done_count": 1,
                }
                self._log_window_stats_on_exit()
                self.detailed_log.log_result(result)
                self.logger.info(f"Localization complete: {files}")
                return result

            self._add_message(resp, increment_turn=True)

        result = {
            "localizer_file": ["(not found)"],
            "localizer_rationale": ["Loop ended without result"],
            "done_count": 1,
        }
        self._log_window_stats_on_exit()
        self.detailed_log.log_result(result)
        self.logger.warning(f"Run loop ended at step {step} without finding files")
        return result

    def _on_tool_result(self, call, output):
        """Track tool results for auto-exit."""
        try:
            if call["name"] == "share_findings":
                self._findings_shared = True
            elif call["name"] == "view_file":
                file_path = call["args"].get("path", "")
                if file_path:
                    ctx = self.shared_context._load_context()
                    if file_path not in ctx["localizer"].get("explored_files", []):
                        ctx["localizer"]["explored_files"].append(file_path)
                        self.shared_context._save_context(ctx)
        except Exception as e:
            self.logger.warning(f"Failed in tool result handler: {e}")
