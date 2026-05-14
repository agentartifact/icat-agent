from __future__ import annotations
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import logging
import threading


class AgentMessageBus:
    """
    Thread-safe message bus for real-time communication between parallel agents.

    Agents can post messages (findings, patches, test results) and read messages
    from other agents. Supports blocking wait for specific message types.

    Message types:
      - localized_files: Localizer found buggy files
      - localized_functions: Localizer found buggy functions
      - test_info: Reproducer found test files and failure info
      - bug_confirmed: Reproducer confirmed the bug exists
      - patch_generated: Patch editor generated a patch diff
      - test_results: Reproducer tested a patch (pass/fail)
    """

    def __init__(self):
        self._messages: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._patch_ready = threading.Event()
        self._test_results_ready = threading.Event()
        self._validation_feedback_ready = threading.Event()
        self._validation_feedback: Optional[Dict[str, Any]] = None
        self.logger = logging.getLogger("AgentMessageBus")

    def post(self, from_agent: str, msg_type: str, data: Any) -> None:
        """Post a message visible to all agents."""
        with self._lock:
            msg_id = len(self._messages) + 1
            msg = {
                "id": msg_id,
                "from": from_agent,
                "type": msg_type,
                "data": data,
                "time": time.time(),
            }
            self._messages.append(msg)
            data_str = str(data)
            preview = data_str[:200] + "..." if len(data_str) > 200 else data_str
            self.logger.info(
                f"[MessageBus] #{msg_id} {from_agent} -> '{msg_type}' ({len(data_str)} chars): {preview}"
            )

        # Signal waiting agents
        if msg_type == "patch_generated":
            self._patch_ready.set()
        elif msg_type == "test_results":
            self._test_results_ready.set()
        elif msg_type in ("validation_passed", "validation_failed"):
            self._validation_feedback = msg
            self._validation_feedback_ready.set()

    def read(
        self,
        since: Optional[float] = None,
        msg_type: Optional[str] = None,
        exclude_from: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Read messages, optionally filtered by time, type, or sender."""
        with self._lock:
            msgs = []
            for m in self._messages:
                if since is not None and m["time"] <= since:
                    continue
                if msg_type is not None and m["type"] != msg_type:
                    continue
                if exclude_from is not None and m["from"] == exclude_from:
                    continue
                msgs.append(m)
            return msgs

    def read_formatted(
        self,
        since: Optional[float] = None,
        exclude_from: Optional[str] = None,
    ) -> str:
        """Read messages and format them as a human-readable string."""
        msgs = self.read(since=since, exclude_from=exclude_from)
        if not msgs:
            return ""
        lines = []
        for m in msgs:
            data_str = str(m["data"])
            lines.append(f"[{m['from']}] {m['type']}: {data_str}")
        return "\n".join(lines)

    def wait_for_patch(self, timeout: float = 300) -> bool:
        """Block until a patch_generated message arrives. Returns True if received."""
        return self._patch_ready.wait(timeout=timeout)

    def wait_for_test_results(self, timeout: float = 300) -> bool:
        """Block until a test_results message arrives. Returns True if received."""
        # Reset the event first so we wait for NEW results
        self._test_results_ready.clear()
        return self._test_results_ready.wait(timeout=timeout)

    def wait_for_validation(self, timeout: float = 600) -> Optional[Dict[str, Any]]:
        """Block until reproducer posts validation_passed or validation_failed.
        Returns the feedback message, or None on timeout."""
        self._validation_feedback_ready.clear()
        self._validation_feedback = None
        if self._validation_feedback_ready.wait(timeout=timeout):
            return self._validation_feedback
        return None

    def get_latest(self, msg_type: str) -> Optional[Dict[str, Any]]:
        """Get the most recent message of a given type."""
        with self._lock:
            for m in reversed(self._messages):
                if m["type"] == msg_type:
                    return m
            return None



class SharedContextManager:
    """
    not used for now
    """

    _lock = threading.RLock()
    _instances: Dict[str, 'SharedContextManager'] = {}

    @classmethod
    def get_instance(cls, instance_id: str, log_dir: str = "logs") -> 'SharedContextManager':
        """Get or create a SharedContextManager for an instance."""
        with cls._lock:
            key = f"{instance_id}:{log_dir}"
            if key not in cls._instances:
                cls._instances[key] = cls(instance_id, log_dir)
            return cls._instances[key]

    def __init__(self, instance_id: str, log_dir: str = "logs"):
        self.instance_id = instance_id
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.context_file = self.log_dir / f"{instance_id}_shared_context.json"
        self.logger = logging.getLogger(f"SharedContext.{instance_id}")

        # Initialize empty context if file doesn't exist
        if not self.context_file.exists():
            self._save_context(self._create_empty_context())

    def _create_empty_context(self) -> Dict[str, Any]:
        """Create an empty context structure."""
        return {
            "instance_id": self.instance_id,
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),

            # Localizer context
            "localizer": {
                "status": "not_started",  # not_started, in_progress, completed
                "top_candidates": [],  # List of {file, function, confidence, rationale}
                "call_chains": [],  # List of call chain analysis
                "explored_files": [],
                "findings": ""
            },

            # Reproducer context
            "reproducer": {
                "status": "not_started",
                "reproduction_script": None,
                "reproduction_result": None,  # pass, fail, error
                "reproduction_output": None,
                "regression_tests": {
                    "available_tests": [],  # Tests from regression_tests/tests.json
                    "tests_before_patch": [],  # Results before patch
                    "tests_after_patch": [],  # Results after patch
                },
                "expected_behavior": None,
                "actual_behavior": None
            },

            # Patch Editor context
            "patch_editor": {
                "status": "not_started",
                "modified_files": [],  # List of {file, function, lines, change_type}
                "patches": [],  # List of patch descriptions
                "unified_diff": None,
                "attempts": []  # List of edit attempts
            },

            # Communication log between agents
            "communication": [],  # List of {from_agent, to_agent, message, timestamp}

            # Trajectory steps (for debugging and analysis)
            "trajectory": []  # List of {agent, action, result, timestamp}
        }

    def _load_context(self) -> Dict[str, Any]:
        """Load context from JSON file."""
        with self._lock:
            if self.context_file.exists():
                try:
                    with open(self.context_file, 'r') as f:
                        return json.load(f)
                except json.JSONDecodeError:
                    self.logger.warning(f"Failed to load context, creating new one")
                    return self._create_empty_context()
            return self._create_empty_context()

    def _save_context(self, context: Dict[str, Any]):
        """Save context to JSON file."""
        with self._lock:
            context["last_updated"] = datetime.now().isoformat()
            with open(self.context_file, 'w') as f:
                json.dump(context, f, indent=2)

    # ========== Localizer Methods ==========

    def update_localizer_status(self, status: str):
        """Update localizer status."""
        context = self._load_context()
        context["localizer"]["status"] = status
        self._save_context(context)
        self.logger.info(f"[Localizer] Status updated to: {status}")

    def add_localizer_candidate(self, file: str, function: str = None,
                                 confidence: float = 0.0, rationale: str = ""):
        """Add a candidate buggy location from localizer."""
        context = self._load_context()
        candidate = {
            "file": file,
            "function": function,
            "confidence": confidence,
            "rationale": rationale,
            "timestamp": datetime.now().isoformat()
        }
        context["localizer"]["top_candidates"].append(candidate)
        self._save_context(context)
        self.logger.info(f"[Localizer] Added candidate: {file}:{function}")


    def set_localizer_findings(self, findings: str):
        """Set the final findings summary from localizer."""
        context = self._load_context()
        context["localizer"]["findings"] = findings
        context["localizer"]["status"] = "completed"
        self._save_context(context)
        self.logger.info(f"[Localizer] Findings saved")

    # ========== Reproducer Methods ==========

    def update_reproducer_status(self, status: str):
        """Update reproducer status."""
        context = self._load_context()
        context["reproducer"]["status"] = status
        self._save_context(context)
        self.logger.info(f"[Reproducer] Status updated to: {status}")

    def set_reproduction_script(self, script: str, expected: str = None, actual: str = None):
        """Set the reproduction script and expected behavior."""
        context = self._load_context()
        context["reproducer"]["reproduction_script"] = script
        context["reproducer"]["expected_behavior"] = expected
        context["reproducer"]["actual_behavior"] = actual
        self._save_context(context)
        self.logger.info(f"[Reproducer] Reproduction script saved")

    def set_reproduction_result(self, result: str, output: str = None):
        """Set the reproduction test result."""
        context = self._load_context()
        context["reproducer"]["reproduction_result"] = result
        context["reproducer"]["reproduction_output"] = output
        self._save_context(context)
        self.logger.info(f"[Reproducer] Reproduction result: {result}")

    def set_available_regression_tests(self, tests: List[str]):
        """Set available regression tests from tests.json."""
        context = self._load_context()
        context["reproducer"]["regression_tests"]["available_tests"] = tests
        self._save_context(context)
        self.logger.info(f"[Reproducer] {len(tests)} regression tests available")

    def add_regression_test_result(self, phase: str, test: str, result: str, output: str = ""):
        """Add a regression test result (before or after patch)."""
        context = self._load_context()
        key = f"tests_{phase}_patch"
        if key in context["reproducer"]["regression_tests"]:
            context["reproducer"]["regression_tests"][key].append({
                "test": test,
                "result": result,
                "output": output,
                "timestamp": datetime.now().isoformat()
            })
            self._save_context(context)
            self.logger.info(f"[Reproducer] Regression test {phase} patch: {test} -> {result}")

    # ========== Patch Editor Methods ==========

    def update_patch_editor_status(self, status: str):
        """Update patch editor status."""
        context = self._load_context()
        context["patch_editor"]["status"] = status
        self._save_context(context)
        self.logger.info(f"[PatchEditor] Status updated to: {status}")

    def add_modified_file(self, file: str, function: str = None,
                          lines: str = None, change_type: str = "modified"):
        """Track a file modification from patch editor."""
        context = self._load_context()
        context["patch_editor"]["modified_files"].append({
            "file": file,
            "function": function,
            "lines": lines,
            "change_type": change_type,
            "timestamp": datetime.now().isoformat()
        })
        self._save_context(context)
        self.logger.info(f"[PatchEditor] Modified: {file}:{function}")

    def add_patch_attempt(self, file: str, before: str, after: str, success: bool):
        """Track a patch editing attempt."""
        context = self._load_context()
        context["patch_editor"]["attempts"].append({
            "file": file,
            "before": before if before else None,
            "after": after if after else None,
            "success": success,
            "timestamp": datetime.now().isoformat()
        })
        self._save_context(context)
        self.logger.info(f"[PatchEditor] Attempt on {file}: {'success' if success else 'failed'}")

    def set_unified_diff(self, diff: str):
        """Set the final unified diff."""
        context = self._load_context()
        context["patch_editor"]["unified_diff"] = diff
        context["patch_editor"]["status"] = "completed"
        self._save_context(context)
        self.logger.info(f"[PatchEditor] Unified diff saved ({len(diff)} bytes)")

    # ========== Communication Methods ==========

    def send_message(self, from_agent: str, to_agent: str, message: str):
        """Send a message from one agent to another."""
        context = self._load_context()
        context["communication"].append({
            "from": from_agent,
            "to": to_agent,
            "message": message,
            "timestamp": datetime.now().isoformat()
        })
        self._save_context(context)
        self.logger.info(f"[Comm] {from_agent} -> {to_agent}: {message}")

    def get_messages_for(self, agent: str) -> List[Dict[str, Any]]:
        """Get all messages sent to an agent."""
        context = self._load_context()
        return [m for m in context["communication"] if m["to"] == agent or m["to"] == "all"]



    # ========== Trajectory Tracking ==========

    def add_trajectory_step(self, agent: str, action: str, result: str, metadata: Dict = None):
        """Add a trajectory step for debugging and analysis."""
        context = self._load_context()
        context["trajectory"].append({
            "agent": agent,
            "action": action,
            "result": result if result else None,
            "metadata": metadata or {},
            "timestamp": datetime.now().isoformat()
        })
        self._save_context(context)

    # ========== Query Methods ==========

    def get_localizer_context(self) -> Dict[str, Any]:
        """Get localizer context."""
        return self._load_context()["localizer"]

    def get_reproducer_context(self) -> Dict[str, Any]:
        """Get reproducer context."""
        return self._load_context()["reproducer"]

    def get_patch_editor_context(self) -> Dict[str, Any]:
        """Get patch editor context."""
        return self._load_context()["patch_editor"]



class AgentContext:
    """
    Shared context object that agents can use to communicate.
    This is passed through the graph state.
    """

    @staticmethod
    def get_localizer_context(state: Dict[str, Any]) -> str:
        """
        Extract localizer context from state for use by other agents.

        Returns:
            Formatted string with localizer findings.
        """
        files = state.get("localizer_file", [])
        rationale = state.get("localizer_rationale", [])

        if not files or not any(files):
            return ""

        context = "\n--- Localizer Context ---\n"
        for i, (file, rat) in enumerate(zip(files, rationale), 1):
            if file and file != "(not found)":
                context += f"{i}. File: {file}\n"
                if rat:
                    context += f"   Rationale: {rat}\n"

        return context

    @staticmethod
    def get_reproducer_context(state: Dict[str, Any]) -> str:
        """
        Extract reproducer context from state for use by other agents.

        Returns:
            Formatted string with reproducer findings including test output.
        """
        statuses = state.get("reproducer_status", [])

        if not statuses:
            return ""

        context = "\n--- Reproducer Context ---\n"
        for i, status in enumerate(statuses, 1):
            context += f"{i}. Test status: {status}\n"

        # Include the reproduction script and its output for other agents
        script = state.get("reproducer_script")
        if script:
            context += f"\nReproduction script:\n{script[:2000]}\n"

        outputs = state.get("reproducer_output", [])
        if outputs:
            latest_output = outputs[-1] if outputs else ""
            if latest_output:
                context += f"\nTest output:\n{latest_output[:2000]}\n"

        return context

    @staticmethod
    def get_patch_editor_context(state: Dict[str, Any]) -> str:
        """
        Extract patch editor context from state for use by other agents.

        Returns:
            Formatted string with patch editor findings.
        """
        modified = state.get("patch_editor_modified_file", [])
        patches = state.get("patch_editor_patch", [])

        if not modified or not any(modified):
            return ""

        context = "\n--- Patch Editor Context ---\n"
        for i, (mod, patch) in enumerate(zip(modified, patches), 1):
            if mod and mod != "(none)":
                context += f"{i}. Modified: {mod}\n"
                if patch and patch != "(none)":
                    context += f"   Patch: {patch}...\n"

        return context

    @staticmethod
    def get_all_context(state: Dict[str, Any]) -> str:
        """
        Get all available context from other agents.

        Returns:
            Formatted string with all agent contexts.
        """
        contexts = []

        localizer = AgentContext.get_localizer_context(state)
        if localizer:
            contexts.append(localizer)

        reproducer = AgentContext.get_reproducer_context(state)
        if reproducer:
            contexts.append(reproducer)

        patch_editor = AgentContext.get_patch_editor_context(state)
        if patch_editor:
            contexts.append(patch_editor)

        if not contexts:
            return ""

        return "\n".join(contexts)

    @staticmethod
    def should_use_context(state: Dict[str, Any], agent_name: str) -> bool:
        """
        Determine if an agent should use context from other agents.
        This allows for adaptive behavior based on what other agents have found.

        Args:
            state: The current graph state
            agent_name: Name of the agent checking for context

        Returns:
            True if context is available and should be used
        """
        if agent_name == "patch_editor":
            # Patch editor should always try to use context from localizer and reproducer
            return (
                bool(state.get("localizer_file")) or
                bool(state.get("reproducer_status"))
            )
        elif agent_name == "localizer":
            # Localizer could use reproducer context if available
            return bool(state.get("reproducer_status"))
        elif agent_name == "reproducer":
            # Reproducer could use localizer context to focus testing
            return bool(state.get("localizer_file"))

        return False



class AgentCommunicationLogger:
    """
    Logger for agent communication and context sharing.
    """

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.log = logging.getLogger(f"AgentComm.{agent_name}")

    def log_context_received(self, context_source: str, context_preview: str):
        """Log when an agent receives context from another agent."""
        preview = context_preview #[:100] + "..." if len(context_preview) > 100 else context_preview
        self.log.info(f"[{self.agent_name}] Received context from {context_source}: {preview}")

    def log_context_sent(self, context_data: str):
        """Log when an agent sends context to the shared state."""
        preview = context_data #[:100] + "..." if len(context_data) > 100 else context_data
        self.log.info(f"[{self.agent_name}] Sending context: {preview}")

    def log_no_context(self):
        """Log when no context is available."""
        self.log.info(f"[{self.agent_name}] No context available from other agents")

