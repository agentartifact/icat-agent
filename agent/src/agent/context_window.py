from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass
from enum import Enum

import litellm
from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
)


# Default context window sizes for different models
MODEL_CONTEXT_LIMITS = {
    "claude-sonnet-4-20250514": 200000,
    "claude-opus-4-20250514": 200000,
    "claude-3-5-sonnet-20241022": 200000,
    "claude-3-opus-20240229": 200000,
    "claude-3-sonnet-20240229": 200000,
    "claude-3-haiku-20240307": 200000,
    "gpt-4": 8192,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-5-mini": 1000000,
    "default": 100000,
}

# Reserve tokens for output
OUTPUT_RESERVE_TOKENS = 4096

# Minimum tokens to keep in working window (excluding system/plan)
MIN_WORKING_WINDOW_TOKENS = 20000


class MessageType(Enum):
    """Types of messages for context management."""
    SYSTEM = "system"          # System prompt - always cached, never trimmed
    PLAN = "plan"              # Plan injection - cached, never trimmed
    INTERACTION = "interaction"  # Regular conversation turns
    TOOL_RESULT = "tool_result"  # Tool call results
    SUMMARY = "summary"        # Summarized old interactions


@dataclass
class ManagedMessage:
    """A message with metadata for context window management."""
    message: BaseMessage
    msg_type: MessageType
    token_count: int = 0
    turn_number: int = 0
    can_trim: bool = True
    is_cached: bool = False

    def __post_init__(self):
        # System and plan messages cannot be trimmed
        if self.msg_type in (MessageType.SYSTEM, MessageType.PLAN):
            self.can_trim = False


@dataclass
class WindowConfig:
    """Configuration for the sliding window."""
    # Maximum tokens for the entire context (model limit - output reserve)
    max_context_tokens: int = 100000

    # When to trigger summarization (percentage of max_context_tokens)
    summarization_threshold: float = 0.85

    # Keep at least this many recent turns (each turn = human + assistant + tool msgs)
    min_recent_turns: int = 3

    # Target tokens after summarization
    target_tokens_after_summary: float = 0.6

    # Whether to enable prompt caching for Anthropic models
    enable_prompt_caching: bool = True

    # Model name for token counting
    model_name: str = "gpt-5-mini"

    # Keep only the last N tool observations verbatim; older ones get elided.
    # Set to 0 to disable. Inspired by SWE-agent's LastNObservations.
    last_n_observations: int = 0


class SlidingWindowManager:
    """
    Manages conversation context with sliding window and summarization.

    Key features:
    - Tracks token usage for all messages
    - Keeps system prompt and plan always in context (cached)
    - Summarizes old interactions when approaching context limit
    - Maintains recent turns for continuity
    """

    def __init__(
        self,
        config: Optional[WindowConfig] = None,
        model_name: str = "gpt-5-mini",
        summarizer_model: Optional[Any] = None,
    ):
        self.config = config or WindowConfig()
        self.model_name = model_name.replace("anthropic:", "")
        self.logger = logging.getLogger(f"SlidingWindowManager")

        # Auto-detect context limit from model
        self._set_context_limit()

        # Message storage
        self.system_message: Optional[ManagedMessage] = None
        self.plan_message: Optional[ManagedMessage] = None
        self.interactions: List[ManagedMessage] = []
        self.summaries: List[ManagedMessage] = []

        # Turn tracking
        self.current_turn = 0

        # Token tracking
        self.total_tokens = 0
        self.pinned_tokens = 0  # System + Plan tokens

        # Summarizer model (for generating summaries of old context)
        self.summarizer_model = summarizer_model

        # Statistics
        self.stats = {
            "summarizations_performed": 0,
            "turns_summarized": 0,
            "tokens_saved": 0,
            "cache_hits": 0,
        }

    def _set_context_limit(self):
        """Set context limit based on model."""
        base_model = self.model_name.split("/")[-1]  # Handle provider prefixes

        for model_pattern, limit in MODEL_CONTEXT_LIMITS.items():
            if model_pattern in base_model:
                self.config.max_context_tokens = limit - OUTPUT_RESERVE_TOKENS
                break
        else:
            self.config.max_context_tokens = MODEL_CONTEXT_LIMITS["default"] - OUTPUT_RESERVE_TOKENS

        self.logger.debug(f"Context limit set to {self.config.max_context_tokens} tokens for {self.model_name}")

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text using LiteLLM."""
        if not text:
            return 0
        try:
            return litellm.utils.token_counter(text=text, model=self.model_name)
        except Exception:
            # Fallback: rough estimate (4 chars per token)
            return len(text) // 4

    def _count_message_tokens(self, message: BaseMessage) -> int:
        """Count tokens in a message."""
        content = message.content if isinstance(message.content, str) else str(message.content)
        # Add overhead for message structure
        base_tokens = self._count_tokens(content)
        overhead = 4  # Approximate overhead for role, etc.
        return base_tokens + overhead

    def set_system_message(self, content: str) -> None:
        """Set the system message (cached, never trimmed)."""
        msg = SystemMessage(content=content)
        token_count = self._count_message_tokens(msg)

        self.system_message = ManagedMessage(
            message=msg,
            msg_type=MessageType.SYSTEM,
            token_count=token_count,
            can_trim=False,
            is_cached=self.config.enable_prompt_caching,
        )

        self._update_pinned_tokens()
        self.logger.debug(f"System message set ({token_count} tokens)")

    def set_plan_message(self, content: str) -> None:
        """Set the plan message (cached, never trimmed)."""
        msg = HumanMessage(content=content)
        token_count = self._count_message_tokens(msg)

        self.plan_message = ManagedMessage(
            message=msg,
            msg_type=MessageType.PLAN,
            token_count=token_count,
            can_trim=False,
            is_cached=self.config.enable_prompt_caching,
        )

        self._update_pinned_tokens()
        self.logger.debug(f"Plan message set ({token_count} tokens)")

    def _update_pinned_tokens(self):
        """Update count of pinned (non-trimmable) tokens."""
        self.pinned_tokens = 0
        if self.system_message:
            self.pinned_tokens += self.system_message.token_count
        if self.plan_message:
            self.pinned_tokens += self.plan_message.token_count

    def add_message(self, message: BaseMessage, increment_turn: bool = False) -> None:
        """
        Add a message to the interaction history.

        Args:
            message: The message to add
            increment_turn: Whether this starts a new turn
        """
        if increment_turn:
            self.current_turn += 1

        # Determine message type
        if isinstance(message, ToolMessage):
            msg_type = MessageType.TOOL_RESULT
        else:
            msg_type = MessageType.INTERACTION

        token_count = self._count_message_tokens(message)

        managed_msg = ManagedMessage(
            message=message,
            msg_type=msg_type,
            token_count=token_count,
            turn_number=self.current_turn,
            can_trim=True,
        )

        self.interactions.append(managed_msg)
        self._update_total_tokens()

        # Check if we need to summarize
        if self._should_summarize():
            self._perform_summarization()

    def add_messages(self, messages: List[BaseMessage]) -> None:
        """Add multiple messages at once."""
        for i, msg in enumerate(messages):
            # First message of batch starts a new turn
            self.add_message(msg, increment_turn=(i == 0))

    def _update_total_tokens(self):
        """Recalculate total token count."""
        self.total_tokens = self.pinned_tokens

        for summary in self.summaries:
            self.total_tokens += summary.token_count

        for interaction in self.interactions:
            self.total_tokens += interaction.token_count

    def _should_summarize(self) -> bool:
        """Check if we should trigger summarization."""
        threshold = self.config.max_context_tokens * self.config.summarization_threshold
        return self.total_tokens > threshold

    def _perform_summarization(self):
        """Summarize old interactions to free up context space."""
        self.logger.info(f"Context window at {self.total_tokens}/{self.config.max_context_tokens} tokens, performing summarization")

        # Calculate how many tokens we need to free
        target_tokens = int(self.config.max_context_tokens * self.config.target_tokens_after_summary)
        tokens_to_free = self.total_tokens - target_tokens

        if tokens_to_free <= 0:
            return

        # Find turns to summarize (keep recent turns)
        turns_to_summarize = []
        tokens_in_old_turns = 0

        # Group interactions by turn
        turn_groups: Dict[int, List[ManagedMessage]] = {}
        for msg in self.interactions:
            if msg.turn_number not in turn_groups:
                turn_groups[msg.turn_number] = []
            turn_groups[msg.turn_number].append(msg)

        # Sort turns and identify old ones to summarize
        sorted_turns = sorted(turn_groups.keys())

        # Keep at least min_recent_turns
        turns_to_keep = max(self.config.min_recent_turns, 1)

        for turn_num in sorted_turns[:-turns_to_keep]:
            turn_msgs = turn_groups[turn_num]
            turn_tokens = sum(m.token_count for m in turn_msgs)

            turns_to_summarize.append((turn_num, turn_msgs))
            tokens_in_old_turns += turn_tokens

            if tokens_in_old_turns >= tokens_to_free:
                break

        if not turns_to_summarize:
            self.logger.debug("No turns available to summarize")
            return

        # Generate summary of old turns
        summary_text = self._generate_summary(turns_to_summarize)

        if summary_text:
            # Create summary message
            summary_msg = HumanMessage(content=f"[SUMMARY OF PREVIOUS STEPS]\n{summary_text}\n[END SUMMARY]")
            summary_token_count = self._count_message_tokens(summary_msg)

            managed_summary = ManagedMessage(
                message=summary_msg,
                msg_type=MessageType.SUMMARY,
                token_count=summary_token_count,
                can_trim=False,  # Don't trim summaries
            )

            self.summaries.append(managed_summary)

            # Remove summarized interactions
            summarized_turn_nums = {t[0] for t in turns_to_summarize}
            self.interactions = [
                m for m in self.interactions
                if m.turn_number not in summarized_turn_nums
            ]

            # Update stats
            self.stats["summarizations_performed"] += 1
            self.stats["turns_summarized"] += len(turns_to_summarize)
            self.stats["tokens_saved"] += tokens_in_old_turns - summary_token_count

            self._update_total_tokens()

            self.logger.info(
                f"Summarized {len(turns_to_summarize)} turns, "
                f"freed {tokens_in_old_turns - summary_token_count} tokens, "
                f"now at {self.total_tokens} tokens"
            )

    def _generate_summary(self, turns_to_summarize: List[Tuple[int, List[ManagedMessage]]]) -> str:
        """Generate a summary of old turns."""
        # Build text to summarize
        turns_text = []

        for turn_num, msgs in turns_to_summarize:
            turn_text = f"Turn {turn_num}:\n"
            for msg in msgs:
                role = msg.message.__class__.__name__.replace("Message", "")
                content = msg.message.content
                if isinstance(content, str):
                    # Truncate very long content
                    if len(content) > 1000:
                        content = content[:500] + "\n...[truncated]...\n" + content[-200:]
                    turn_text += f"  {role}: {content}\n"
            turns_text.append(turn_text)

        full_text = "\n".join(turns_text)

        # If we have a summarizer model, use it
        if self.summarizer_model:
            try:
                summary_prompt = f"""Summarize the following agent interaction history concisely.
Focus on: key findings, files examined, actions taken, and important decisions.
Keep tool outputs brief - just note what was found or modified.

{full_text}

Provide a concise summary (max 300 words):"""

                response = self.summarizer_model.invoke([HumanMessage(content=summary_prompt)])
                return response.content if hasattr(response, 'content') else str(response)
            except Exception as e:
                self.logger.warning(f"Summarizer model failed: {e}, using extractive summary")

        # Fallback: extractive summary
        return self._extractive_summary(turns_to_summarize)

    def _extractive_summary(self, turns_to_summarize: List[Tuple[int, List[ManagedMessage]]]) -> str:
        """Create an extractive summary when LLM summarization isn't available."""
        summary_parts = []

        for turn_num, msgs in turns_to_summarize:
            for msg in msgs:
                content = msg.message.content if isinstance(msg.message.content, str) else str(msg.message.content)

                # Extract key information based on message type
                if isinstance(msg.message, ToolMessage):
                    # For tool results, extract file paths and key findings
                    lines = content.split('\n')[:3]
                    if lines:
                        summary_parts.append(f"Step {turn_num} tool result: {lines[0][:100]}")
                elif isinstance(msg.message, AIMessage):
                    # For AI messages, extract the main action/decision
                    if "FINAL:" in content or "DONE:" in content:
                        summary_parts.append(f"Step {turn_num}: {content[:200]}")
                    elif len(content) < 200:
                        summary_parts.append(f"Step {turn_num}: {content}")

        if summary_parts:
            return "Previous steps summary:\n" + "\n".join(summary_parts[:10])

        return f"Completed {len(turns_to_summarize)} exploration steps."

    def _is_anthropic_model(self) -> bool:
        """Check if the current model is an Anthropic model."""
        return "claude" in self.model_name.lower() or "anthropic" in self.model_name.lower()

    def _set_cache_control(self, message: BaseMessage) -> None:
        """Add Anthropic cache_control to a message's content.

        We add BOTH the cache_control field AND a text marker. The field is the
        official mechanism (used by direct Anthropic / litellm). The marker is a
        fallback for langchain's ChatOpenAI path, which strips cache_control
        during serialization — a request hook in agent/utils re-injects
        cache_control on the wire when it sees the marker.
        """
        from .utils import CACHE_CONTROL_MARKER
        content = message.content
        if isinstance(content, str):
            merged = (CACHE_CONTROL_MARKER + " " + content).rstrip()
            message.content = [
                {"type": "text",
                 "text": merged,
                 "cache_control": {"type": "ephemeral"}}
            ]
        elif isinstance(content, list):
            if content and isinstance(content[-1], dict):
                last = content[-1]
                last["cache_control"] = {"type": "ephemeral"}
                txt = last.get("text")
                if isinstance(txt, str) and CACHE_CONTROL_MARKER not in txt:
                    last["text"] = (CACHE_CONTROL_MARKER + " " + txt).rstrip()

    def _clear_cache_control(self, message: BaseMessage) -> None:
        """Remove cache_control from a message's content."""
        content = message.content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    block.pop("cache_control", None)
            # Simplify back to string if it was originally a string
            if len(content) == 1 and isinstance(content[0], dict) and content[0].get("type") == "text":
                keys = set(content[0].keys())
                if keys == {"type", "text"}:
                    message.content = content[0]["text"]

    def _elide_old_observations(self, interactions: List[ManagedMessage]) -> List[ManagedMessage]:
        """Replace old tool observations AND corresponding AI messages with short placeholders.

        Keeps the last N tool results verbatim along with their paired AI messages.
        For older turns:
        - ToolMessage content is replaced with "Old observation: (X lines omitted)"
        - AIMessage reasoning content is replaced with a short placeholder,
          but tool_calls are kept intact so tool_call_id pairing still works.

        Inspired by SWE-agent's LastNObservations, extended to trim AI-side history
        that otherwise accumulates without bound.
        """
        n = self.config.last_n_observations
        if n <= 0:
            return interactions  # disabled

        # Find indices of all tool result messages
        tool_indices = [
            i for i, m in enumerate(interactions)
            if m.msg_type == MessageType.TOOL_RESULT
        ]

        if len(tool_indices) <= n:
            return interactions  # not enough to elide

        # Cutoff: keep the last N tool results AND the AI message that precedes each.
        # Walk back from the first kept tool to include its paired AI turn so
        # kept observations still have coherent reasoning + tool_call context.
        first_kept_tool_idx = tool_indices[-n]
        first_kept_idx = first_kept_tool_idx
        j = first_kept_tool_idx - 1
        while j >= 0 and isinstance(interactions[j].message, AIMessage):
            first_kept_idx = j
            j -= 1
        elided_tool_count = sum(1 for i in tool_indices if i < first_kept_idx)

        from copy import deepcopy
        result = []
        ai_elided = 0
        for i, managed in enumerate(interactions):
            if i >= first_kept_idx:
                result.append(managed)
                continue
            msg = managed.message
            if managed.msg_type == MessageType.TOOL_RESULT:
                content = msg.content
                n_lines = (content if isinstance(content, str) else str(content)).count('\n') + 1
                elided = deepcopy(managed)
                elided.message = ToolMessage(
                    content=f"Old observation: ({n_lines} lines omitted)",
                    tool_call_id=getattr(msg, 'tool_call_id', 'unknown'),
                )
                elided.token_count = 10
                result.append(elided)
            elif isinstance(msg, AIMessage):
                tool_calls = getattr(msg, 'tool_calls', None) or []
                if tool_calls:
                    tool_name = tool_calls[0].get('name') if isinstance(tool_calls[0], dict) else getattr(tool_calls[0], 'name', 'tool')
                    placeholder = f"[Old AI turn: {tool_name} call, reasoning omitted]"
                else:
                    placeholder = "[Old AI turn: reasoning omitted]"
                elided = deepcopy(managed)
                elided.message = AIMessage(content=placeholder, tool_calls=tool_calls)
                elided.token_count = 10
                result.append(elided)
                ai_elided += 1
            else:
                result.append(managed)

        self.logger.info(
            f"[Elision] Eliding {elided_tool_count} of {len(tool_indices)} tool observations "
            f"and {ai_elided} AI turns (keeping last {n} turns)"
        )
        return result

    def get_messages(self) -> List[BaseMessage]:
        """
        Get all messages in the correct order for the LLM.

        For Anthropic models, adds cache_control breakpoints:
        - System message (always cached — stable across all turns)
        - Plan message (always cached — stable across all turns)
        - Last 2 user/tool messages (cache the conversation prefix up to recent turns)

        This follows SWE-agent's approach: the entire prefix before the last 2
        user messages is cached, so each new turn only pays for the new content.
        """
        from copy import deepcopy

        messages = []
        is_anthropic = self._is_anthropic_model() and self.config.enable_prompt_caching

        if self.system_message:
            msg = deepcopy(self.system_message.message) if is_anthropic else self.system_message.message
            if is_anthropic:
                self._set_cache_control(msg)
            messages.append(msg)

        if self.plan_message:
            msg = deepcopy(self.plan_message.message) if is_anthropic else self.plan_message.message
            if is_anthropic:
                self._set_cache_control(msg)
            messages.append(msg)

        for summary in self.summaries:
            messages.append(summary.message)

        # Elide old tool observations (keep last N verbatim, replace older ones)
        elided_interactions = self._elide_old_observations(self.interactions)
        for interaction in elided_interactions:
            messages.append(interaction.message)

        # Anthropic prompt-cache placement.
        #
        # Anthropic allows up to 4 cache breakpoints. Each breakpoint creates a
        # cache for the entire prefix up to that point. On a later request,
        # Anthropic reuses cached prefixes whose content matches exactly.
        #
        # We place breakpoints to maximise reuse:
        #   1. system   — never changes across the run.
        #   2. plan     — never changes after planning is done.
        #   3. "frozen" — anchored to a position that's been stable for >=1 turn,
        #                 advances every CACHE_FREEZE_STEP turns. The cache
        #                 written here lives long enough to be hit by many
        #                 subsequent turns.
        #   4. last     — current-tail breakpoint. Writes a cache that next turn
        #                 can match against (same content, with appended new msgs).
        if is_anthropic and messages:
            from copy import deepcopy as _deepcopy

            # Clear any stale cache_control AND the smuggled marker from
            # previous get_messages() runs so we don't accumulate markers.
            from .utils import CACHE_CONTROL_MARKER as _MARKER
            for msg in messages:
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, dict):
                            block.pop("cache_control", None)
                            t = block.get("text")
                            if isinstance(t, str) and _MARKER in t:
                                block["text"] = t.replace(_MARKER, "").lstrip()

            # 1+2: system + plan (first 2 entries in messages list).
            for msg in messages[:2]:
                self._set_cache_control(msg)

            # 3: tail breakpoint on the very last message (next turn will hit it).
            # NOTE: only 4 cache_control blocks total are allowed by Anthropic.
            # Reserve 1 slot for tool cache (set by enable_anthropic_tool_caching),
            # so we use only system + plan + tail = 3 from messages.
            if messages:
                if isinstance(messages[-1].content, str):
                    messages[-1] = _deepcopy(messages[-1])
                self._set_cache_control(messages[-1])

        return messages

    def get_messages_for_anthropic(self) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Get messages formatted for Anthropic API with cache control.

        Returns:
            Tuple of (system_prompt, messages_list) where messages_list
            has cache_control markers for caching.
        """
        system_content = ""
        if self.system_message:
            system_content = self.system_message.message.content

        messages = []

        # Add plan with cache control if present
        if self.plan_message:
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": self.plan_message.message.content,
                        "cache_control": {"type": "ephemeral"} if self.config.enable_prompt_caching else None
                    }
                ]
            })
            # Need a placeholder assistant response after cached user message
            # to maintain conversation flow

        # Add summaries
        for summary in self.summaries:
            messages.append({
                "role": "user",
                "content": summary.message.content
            })

        # Add interactions
        for interaction in self.interactions:
            msg = interaction.message
            role = "user" if isinstance(msg, (HumanMessage, ToolMessage)) else "assistant"

            if isinstance(msg, ToolMessage):
                # Tool messages need special handling
                messages.append({
                    "role": "user",
                    "content": f"[Tool Result]\n{msg.content}"
                })
            else:
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                messages.append({
                    "role": role,
                    "content": content
                })

        return system_content, messages

    def get_token_usage(self) -> Dict[str, int]:
        """Get current token usage statistics."""
        return {
            "total_tokens": self.total_tokens,
            "pinned_tokens": self.pinned_tokens,
            "interaction_tokens": sum(m.token_count for m in self.interactions),
            "summary_tokens": sum(m.token_count for m in self.summaries),
            "max_tokens": self.config.max_context_tokens,
            "utilization_pct": round(100 * self.total_tokens / self.config.max_context_tokens, 1),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get sliding window statistics."""
        return {
            **self.stats,
            "current_turn": self.current_turn,
            "total_interactions": len(self.interactions),
            "total_summaries": len(self.summaries),
            **self.get_token_usage(),
        }

    def clear_interactions(self):
        """Clear all interactions but keep system and plan."""
        self.interactions = []
        self.summaries = []
        self.current_turn = 0
        self._update_total_tokens()


class PromptCacheManager:
    """
    Manages prompt caching for Anthropic models.

    Anthropic's prompt caching allows caching the first parts of a prompt
    that don't change between requests, reducing costs and latency.
    """

    def __init__(self, enable_caching: bool = True):
        self.enable_caching = enable_caching
        self.cached_prefix_tokens = 0
        self.logger = logging.getLogger("PromptCacheManager")

        # Track cache statistics
        self.stats = {
            "requests_with_cache": 0,
            "estimated_cache_hits": 0,
            "estimated_tokens_saved": 0,
        }

    def format_system_prompt_for_cache(self, system_prompt: str) -> Union[str, List[Dict]]:
        """
        Format system prompt with cache control for Anthropic.

        For Anthropic models, returns a list with cache_control.
        For other models, returns the string as-is.
        """
        if not self.enable_caching:
            return system_prompt

        return [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"}
            }
        ]

    def prepare_messages_for_cache(
        self,
        messages: List[BaseMessage],
        cache_first_n: int = 2  # Cache system + plan typically
    ) -> List[Dict[str, Any]]:
        """
        Prepare messages with cache control markers.

        Args:
            messages: List of messages
            cache_first_n: Number of initial messages to mark for caching

        Returns:
            List of message dicts with cache_control where appropriate
        """
        result = []

        for i, msg in enumerate(messages):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            role = "assistant" if isinstance(msg, AIMessage) else "user"

            if isinstance(msg, ToolMessage):
                role = "user"
                content = f"[Tool Result: {msg.tool_call_id}]\n{content}"

            msg_dict = {
                "role": role,
                "content": content
            }

            # Add cache control for first N messages
            if self.enable_caching and i < cache_first_n:
                msg_dict["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"}
                    }
                ]
                self.stats["requests_with_cache"] += 1

            result.append(msg_dict)

        return result

    def update_cache_stats(self, response_metadata: Dict[str, Any]):
        """Update cache statistics from API response."""
        if "usage" in response_metadata:
            usage = response_metadata["usage"]

            # Anthropic returns cache_creation_input_tokens and cache_read_input_tokens
            cache_read = usage.get("cache_read_input_tokens", 0)
            if cache_read > 0:
                self.stats["estimated_cache_hits"] += 1
                self.stats["estimated_tokens_saved"] += cache_read
                self.logger.debug(f"Cache hit! Read {cache_read} cached tokens")

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return self.stats.copy()


def create_window_manager(
    model_name: str = "gpt-5-mini",
    enable_prompt_caching: bool = True,
    summarization_threshold: float = 0.85,
    min_recent_turns: int = 3,
    summarizer_model: Optional[Any] = None,
    last_n_observations: int = 0,
) -> SlidingWindowManager:
    """
    Factory function to create a properly configured SlidingWindowManager.

    Args:
        model_name: Name of the model being used
        enable_prompt_caching: Whether to enable Anthropic prompt caching
        summarization_threshold: When to trigger summarization (0-1)
        min_recent_turns: Minimum recent turns to keep
        summarizer_model: Optional LLM for generating summaries
        last_n_observations: Keep only last N tool observations verbatim (0=disabled)

    Returns:
        Configured SlidingWindowManager instance
    """
    config = WindowConfig(
        enable_prompt_caching=enable_prompt_caching,
        summarization_threshold=summarization_threshold,
        min_recent_turns=min_recent_turns,
        model_name=model_name.replace("anthropic:", ""),
        last_n_observations=last_n_observations,
    )

    return SlidingWindowManager(
        config=config,
        model_name=model_name,
        summarizer_model=summarizer_model,
    )
