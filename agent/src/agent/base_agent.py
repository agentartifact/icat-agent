# agent/base_agent.py
"""Base class for all execution agents (localizer, patch_editor, reproducer)."""
from __future__ import annotations
import time
import logging
from pathlib import Path
from typing import Optional
from langchain_core.messages import HumanMessage, ToolMessage
from .utils import DetailedLogger
from .docker_env import DockerEnvironment
from .context_sharing import SharedContextManager, AgentMessageBus
from .context_window import create_window_manager


class BaseAgent:
    """Common boilerplate shared by localizer, patch_editor, and reproducer agents.

    Subclasses must set ROLE and implement _setup_tools(), _run_loop(), _docker_error_result().
    """

    ROLE = "base"  # Override in subclass: "localizer", "patch_editor", "reproducer"

    def __init__(
        self,
        repo_path: str,
        problem: str,
        instance_id: str = "unknown",
        commit: str = "",
        max_steps: int = 10,
        log_path: str = "agent.log",
        log_dir: str = "logs",
        plan: Optional[str] = None,
        shared_docker_id: Optional[str] = None,
        model_name: str = "openai:gpt-5-mini",
        max_cost: float = 3.0,
        message_bus: Optional[AgentMessageBus] = None,
        min_recent_turns: int = 3,
        image_name: Optional[str] = None,
        last_n_observations: int = 0,
    ):
        self.repo_path = Path(repo_path)
        self.commit = commit
        self.problem = problem
        self.instance_id = instance_id
        self.max_steps = max_steps
        self.plan = plan
        self.shared_docker_id = shared_docker_id
        self.model_name = model_name
        self.message_bus = message_bus
        self.image_name = image_name
        self._last_bus_check_time = 0.0

        # Docker (set up in run())
        self.docker_env = None
        self.owns_docker = False

        # Sliding window (always enabled)
        self.window_manager = create_window_manager(
            model_name=model_name,
            summarization_threshold=0.85,
            min_recent_turns=min_recent_turns,
            last_n_observations=last_n_observations,
        )

        # Logging
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "w") as f:
            f.write(f"[{self.__class__.__name__}] {time.ctime()}\n")
        self.detailed_log = DetailedLogger(
            self.ROLE, instance_id, log_dir=log_dir,
            model_name=model_name, max_cost=max_cost,
        )
        self.logger = logging.getLogger(f"{self.__class__.__name__}.{instance_id}")

        # Shared context
        self.shared_context = SharedContextManager.get_instance(instance_id, log_dir)

        # Tools and model (created in _setup_tools after docker_env is set)
        self.tools = []
        self.model = None
        self.model_with_tools = None

    # ---------- Plan / message management ----------

    def _setup_plan(self, get_prompt_fn, system_prompt_key, plan_key, no_plan_key, **system_kwargs):
        """Set up system prompt, initial message, and plan injection in the window manager."""
        system_prompt = get_prompt_fn(system_prompt_key)
        if not system_prompt:
            raise ValueError(f"Missing {self.ROLE}.{system_prompt_key} in prompts.yaml")

        initial_message = get_prompt_fn("initial_message", **system_kwargs) or ""

        self.window_manager.set_system_message(system_prompt)

        if self.plan:
            try:
                import json as _json
                plan_obj = _json.loads(self.plan) if isinstance(self.plan, str) else self.plan
                plan_msg = get_prompt_fn(
                    plan_key,
                    **plan_obj,
                )
            except (ValueError, TypeError, AttributeError):
                raw_key = plan_key + "_raw" if not plan_key.endswith("_raw") else plan_key
                plan_msg = get_prompt_fn(raw_key, plan=self.plan)
            if not plan_msg:
                raise ValueError(f"Missing {self.ROLE}.{plan_key} in prompts.yaml")
            self.window_manager.set_plan_message(initial_message + "\n\n" + plan_msg)
        else:
            no_plan_msg = get_prompt_fn(no_plan_key) or ""
            self.window_manager.set_plan_message(initial_message + "\n\n" + no_plan_msg)

        self.messages = self.window_manager.get_messages()

    def _add_message(self, msg, **kwargs):
        """Add a message to the conversation via sliding window manager."""
        self.window_manager.add_message(msg, **kwargs)

    # ---------- Docker management ----------

    def _setup_docker(self) -> bool:
        """Initialize Docker environment. Returns False on failure."""
        if self.shared_docker_id:
            self.docker_env = DockerEnvironment(self.instance_id, timeout=600, image_name=self.image_name, repo_path=str(self.repo_path))
            self.docker_env.container_id = self.shared_docker_id
            self.owns_docker = False
            self.logger.info(f"Using shared Docker container: {self.shared_docker_id[:12]}")
        else:
            self.docker_env = DockerEnvironment(self.instance_id, timeout=600, image_name=self.image_name, repo_path=str(self.repo_path))
            self.docker_env.__enter__()
            self.owns_docker = True
            if not self.docker_env.container_id:
                self.logger.error("Failed to start Docker container")
                return False
            self.logger.info(f"Docker container started: {self.docker_env.container_id[:12]}")
        return True

    def _cleanup_docker(self):
        """Clean up Docker if we own it."""
        if self.owns_docker and self.docker_env:
            self.docker_env.__exit__(None, None, None)
            self.logger.info("Docker container cleaned up")

    # ---------- Bus messages ----------

    # Message types to SKIP (noisy status updates). Everything else is injected.
    _SKIP_TYPES = {
        "test_info", "status_update",
    }

    def _inject_bus_messages(self):
        """Check message bus and inject only actionable messages into context."""
        if not self.message_bus:
            return
        new_msgs = self.message_bus.read(
            since=self._last_bus_check_time,
            exclude_from=self.ROLE,
        )
        if not new_msgs:
            return

        self._last_bus_check_time = time.time()

        actionable = [m for m in new_msgs if m["type"] not in self._SKIP_TYPES]
        if not actionable:
            return

        lines = [f"[{m['from']}] {m['type']}: {str(m['data'])}" for m in actionable]
        self._add_message(HumanMessage(
            content=f"[UPDATE from other agents]\n" + "\n".join(lines)
        ))
        self.logger.info(f"[{self.ROLE.title()}] Injected {len(actionable)} actionable messages")

    # ---------- LLM invocation with retry ----------

    @staticmethod
    def _sanitize_messages(messages):
        """Fix messages to avoid Anthropic API rejections.

        - Strip trailing whitespace from ALL text in ALL AI messages
          (Anthropic rejects 'final assistant content cannot end with trailing whitespace')
        """
        if not messages:
            return messages
        for msg in messages:
            if not (hasattr(msg, 'type') and msg.type == 'ai'):
                continue
            if isinstance(msg.content, str):
                msg.content = msg.content.rstrip()
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict) and block.get('type') == 'text':
                        block['text'] = block['text'].rstrip()
        return messages

    def _invoke_llm(self, messages=None):
        """Invoke the LLM with retry on transient errors.

        Returns the response, or raises after max_retries consecutive failures.
        Uses self._consecutive_llm_errors to track state across calls.
        """
        if messages is None:
            messages = self._get_current_messages()
        messages = self._sanitize_messages(messages)
        try:
            resp = self.model_with_tools.invoke(messages)
            self._consecutive_llm_errors = 0
            return resp
        except Exception as e:
            error_str = str(e)
            self._consecutive_llm_errors = getattr(self, "_consecutive_llm_errors", 0) + 1
            self.logger.error(f"LLM call failed (attempt {self._consecutive_llm_errors}): {e}")
            self.detailed_log.log_error(e, f"LLM call attempt {self._consecutive_llm_errors}")

            # Fatal: content policy violation — no point retrying
            if "invalid_prompt" in error_str or "usage policy" in error_str:
                self.logger.error(f"[FATAL] Prompt flagged by content policy — aborting agent")
                raise

            if self._consecutive_llm_errors >= 3:
                self.logger.error("3 consecutive LLM errors, aborting")
                raise
            return None  # caller should `continue` to next step

    # ---------- Run loop helpers ----------

    def _emit_nudges(self, step, early_msg, urgent_msg):
        """Emit progressive nudges at step thresholds."""
        if step == self.max_steps - 5:
            self._add_message(HumanMessage(content=early_msg))
        elif step == self.max_steps - 2:
            self._add_message(HumanMessage(content=urgent_msg))

    def _log_window_stats(self):
        """Log current context window utilization."""
        window_stats = self.window_manager.get_token_usage()
        self.logger.debug(
            f"Context window: {window_stats['total_tokens']}/{window_stats['max_tokens']} tokens "
            f"({window_stats['utilization_pct']}%)"
        )

    def _get_current_messages(self):
        """Get current messages from window manager."""
        return self.window_manager.get_messages()

    def _execute_tool_calls(self, resp) -> list | None:
        """Execute tool calls from LLM response. Returns tool messages or None if no calls."""
        if not getattr(resp, "tool_calls", None):
            return None

        tool_map = {t.name: t for t in self.tools}
        tool_msgs = []
        for call in resp.tool_calls:
            fn = tool_map.get(call["name"])
            if fn:
                try:
                    out = fn.invoke(call["args"])
                    self.detailed_log.log_tool_call(call["name"], call["args"], out)
                    self.logger.info(f"Tool {call['name']} called with {call['args']}")
                    tool_msgs.append(ToolMessage(f"OBSERVATION:\n{out}", tool_call_id=call["id"]))
                    self._on_tool_result(call, out)
                except Exception as e:
                    error_msg = f"Tool error: {str(e)}"
                    self.logger.error(error_msg)
                    self.detailed_log.log_error(e, f"Tool {call['name']}")
                    tool_msgs.append(ToolMessage(error_msg, tool_call_id=call["id"]))
            else:
                self.logger.warning(f"Unknown tool: {call['name']}")

        self._add_message(resp, increment_turn=True)
        for tm in tool_msgs:
            self._add_message(tm)
        return tool_msgs

    def _on_tool_result(self, call, output):
        """Hook for subclasses to react to tool results (e.g., share findings)."""
        pass

    def _log_window_stats_on_exit(self):
        """Log final sliding window statistics."""
        stats = self.window_manager.get_stats()
        if stats:
            self.logger.info(
                f"[SlidingWindow] Final stats: "
                f"turns={stats['current_turn']}, "
                f"tokens={stats['total_tokens']}/{stats['max_tokens']} ({stats['utilization_pct']}%), "
                f"summarizations={stats['summarizations_performed']}, "
                f"tokens_saved={stats['tokens_saved']}"
            )

    # ---------- Abstract methods ----------

    def _setup_tools(self):
        """Create tools and bind to model. Must be called after docker_env is set."""
        raise NotImplementedError

    def _run_loop(self):
        """Agent-specific run loop."""
        raise NotImplementedError

    def _docker_error_result(self) -> dict:
        """Return result dict when Docker fails to start."""
        raise NotImplementedError
