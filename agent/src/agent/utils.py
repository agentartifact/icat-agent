# agent/utils.py
"""Agent utilities: cost tracking, detailed logging, and result saving."""

import json
import logging
import traceback
from pathlib import Path
from datetime import datetime
from threading import Lock
from typing import Any, Dict, List, Optional

import litellm


DEFAULT_MAX_COST_PER_INSTANCE = 2.0


class OpenRouterResponseCapture:
    """Captures raw OpenRouter API responses via httpx event hook.

    OpenRouter returns usage.cost and usage.cost_details in the response body,
    but LangChain's ChatOpenAI strips them during parsing. This hook saves
    the raw response so we can extract the real cost later.
    """

    def __init__(self):
        self.last_raw_usage = None  # Most recent usage dict from OpenRouter

    def on_response(self, response):
        """httpx event hook — called for every HTTP response."""
        try:
            response.read()  # ensure body is loaded before parsing
            data = response.json()
            if isinstance(data, dict) and "usage" in data:
                self.last_raw_usage = data["usage"]
        except Exception:
            pass

# Singleton — shared across all OpenRouter models in the process
_openrouter_capture = OpenRouterResponseCapture()


# Marker we embed in text content so the request hook can identify which content
# blocks should have cache_control. LangChain's ChatOpenAI strips unknown fields
# (like cache_control) from message content during serialization, so we smuggle
# a marker in the text and a request hook re-injects cache_control on the wire.
CACHE_CONTROL_MARKER = "<<__CACHE_CONTROL_EPHEMERAL__>>"


def _inject_cache_control_on_request(request):
    """httpx request event hook: scan body for CACHE_CONTROL_MARKER, strip it
    from text content, and add a sibling cache_control field on that block.

    Also handles tool definitions: if a tool's description starts with the marker,
    move cache_control to the tool dict (Anthropic-via-OpenRouter format).
    """
    try:
        # Read the request body once. After this, request.content is bytes.
        try:
            body_bytes = request.read()
        except Exception:
            body_bytes = request.content if isinstance(request.content, (bytes, bytearray)) else None
        if not body_bytes:
            return
        text = body_bytes.decode("utf-8")
        if CACHE_CONTROL_MARKER not in text:
            return
        data = json.loads(text)
        modified = False

        def _process_content_blocks(blocks):
            nonlocal modified
            if not isinstance(blocks, list):
                return
            for blk in blocks:
                if not isinstance(blk, dict):
                    continue
                t = blk.get("text")
                if isinstance(t, str) and CACHE_CONTROL_MARKER in t:
                    blk["text"] = t.replace(CACHE_CONTROL_MARKER, "").lstrip()
                    blk["cache_control"] = {"type": "ephemeral"}
                    modified = True

        # Messages
        for msg in data.get("messages", []) or []:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, list):
                _process_content_blocks(content)
            elif isinstance(content, str) and CACHE_CONTROL_MARKER in content:
                # Convert string content to list-with-block format and strip marker.
                msg["content"] = [{
                    "type": "text",
                    "text": content.replace(CACHE_CONTROL_MARKER, "").lstrip(),
                    "cache_control": {"type": "ephemeral"},
                }]
                modified = True

        # System (some clients pass it separately)
        sys_v = data.get("system")
        if isinstance(sys_v, list):
            _process_content_blocks(sys_v)
        elif isinstance(sys_v, str) and CACHE_CONTROL_MARKER in sys_v:
            data["system"] = [{
                "type": "text",
                "text": sys_v.replace(CACHE_CONTROL_MARKER, "").lstrip(),
                "cache_control": {"type": "ephemeral"},
            }]
            modified = True

        # Tools: marker may sit in description; promote to top-level cache_control.
        for tool in data.get("tools", []) or []:
            if not isinstance(tool, dict):
                continue
            fn = tool.get("function") or {}
            desc = fn.get("description")
            if isinstance(desc, str) and CACHE_CONTROL_MARKER in desc:
                fn["description"] = desc.replace(CACHE_CONTROL_MARKER, "").lstrip()
                tool["cache_control"] = {"type": "ephemeral"}
                modified = True

        if modified:
            new_body = json.dumps(data).encode("utf-8")
            # Replace the request body with the new bytes via httpx's stream API.
            try:
                import httpx as _httpx
                request.stream = _httpx.ByteStream(new_body)
            except Exception:
                request._content = new_body
            request._content = new_body
            request.headers["Content-Length"] = str(len(new_body))
            # Drop chunked encoding if it was set, since we have a fixed body now.
            request.headers.pop("Transfer-Encoding", None)
    except Exception:
        pass


def get_openrouter_cost() -> float | None:
    """Return the cost from the last OpenRouter API call, or None."""
    usage = _openrouter_capture.last_raw_usage
    if not usage:
        return None
    # BYOK: actual cost is in cost_details.upstream_inference_cost
    cost_details = usage.get("cost_details") or {}
    upstream = cost_details.get("upstream_inference_cost")
    if upstream is not None:
        return float(upstream)
    # Non-BYOK: cost is directly in usage.cost
    cost = usage.get("cost")
    if cost is not None:
        return float(cost)
    return None


def enable_anthropic_tool_caching(bound_model, model_name: str):
    """Inject cache_control on the last tool definition so Anthropic caches
    the entire tools array. Works for ChatOpenAI bindings going through
    OpenRouter to Anthropic, and is a no-op for non-Anthropic models.

    For OpenRouter (langchain ChatOpenAI), langchain serialization strips the
    top-level `cache_control` field on tool dicts. We also embed the
    CACHE_CONTROL_MARKER inside the last tool's description; the request hook
    detects it and re-adds top-level cache_control on the wire.
    """
    if not ("anthropic" in model_name.lower() or "claude" in model_name.lower()):
        return bound_model
    try:
        kwargs = getattr(bound_model, 'kwargs', None)
        if not kwargs or 'tools' not in kwargs:
            return bound_model
        tools = kwargs['tools']
        if not tools:
            return bound_model
        # Mark the LAST tool. Cache covers everything up to & including it.
        last = tools[-1]
        if isinstance(last, dict):
            last['cache_control'] = {"type": "ephemeral"}
            fn = last.get('function')
            if isinstance(fn, dict):
                desc = fn.get('description', '') or ''
                if CACHE_CONTROL_MARKER not in desc:
                    fn['description'] = (CACHE_CONTROL_MARKER + " " + desc).strip()
    except Exception:
        pass
    return bound_model


def create_chat_model(model_name: str, **kwargs):
    """Wrapper around init_chat_model that enables prompt caching.

    Supports:
    - openai:* models (OpenAI direct)
    - anthropic:* models (Anthropic with prompt caching)
    - openrouter/* models (via OpenRouter API using ChatOpenAI with custom base_url)
    """
    import os

    # OpenRouter models: use ChatOpenAI with OpenRouter base_url (no litellm)
    if model_name.startswith("openrouter/"):
        import httpx
        from langchain_openai import ChatOpenAI
        openrouter_model = model_name[len("openrouter/"):]  # e.g. "minimax/minimax-m2.5"
        api_key = os.environ.get("OPENROUTER_API_KEY", "")

        # Create httpx client with hooks:
        #  - request: re-inject cache_control fields stripped by langchain serialization
        #  - response: capture raw usage/cost from OpenRouter
        http_client = httpx.Client(
            event_hooks={
                "request": [_inject_cache_control_on_request],
                "response": [_openrouter_capture.on_response],
            },
            timeout=300,
        )

        chat_kwargs = {
            "model": openrouter_model,
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": api_key,
            "http_client": http_client,
        }

        # For Anthropic models via OpenRouter: route directly to Anthropic provider
        # to enable prompt caching (cache_control). Azure provider doesn't support it.
        # Also request OpenRouter to include detailed usage (cache_read/creation tokens).
        if "anthropic" in model_name.lower() or "claude" in model_name.lower():
            chat_kwargs["extra_body"] = {
                "provider": {
                    "order": ["Anthropic"],
                    "allow_fallbacks": False,
                },
                "usage": {"include": True},
            }

        # MiniMax-specific: reasoning via extra_body, disable Responses API
        if "minimax" in model_name.lower():
            chat_kwargs["use_responses_api"] = False  # Force Chat Completions (Responses API breaks via OpenRouter)
            chat_kwargs["reasoning_effort"] = "high"
            chat_kwargs["model_kwargs"] = {
                "parallel_tool_calls": True,
            }

        # OpenAI GPT-5.4: set reasoning_effort to high
        if "gpt-5.4" in model_name.lower() and "mini" not in model_name.lower() and "nano" not in model_name.lower():
            chat_kwargs["reasoning_effort"] = "high"

        # Pass through any extra kwargs
        chat_kwargs.update(kwargs)
        return ChatOpenAI(**chat_kwargs)

    # Standard litellm-backed models
    from langchain.chat_models import init_chat_model
    if model_name.startswith("openai:"):
        kwargs.setdefault("store", True)
    elif model_name.startswith("anthropic:") or "claude" in model_name.lower():
        # Enable prompt caching for Anthropic models
        extra_headers = kwargs.pop("extra_headers", {})
        extra_headers.setdefault("anthropic-beta", "prompt-caching-2024-07-31")
        kwargs.setdefault("model_kwargs", {})
        kwargs["model_kwargs"]["extra_headers"] = extra_headers
    return init_chat_model(model_name, **kwargs)


# --- Cost Limit Exceptions ---

class CostLimitExceededError(Exception):
    pass

class InstanceCostLimitExceededError(CostLimitExceededError):
    def __init__(self, current_cost: float, max_cost: float):
        self.current_cost = current_cost
        self.max_cost = max_cost
        super().__init__(f"Instance cost limit exceeded: ${current_cost:.4f} >= ${max_cost:.2f}")

class TotalCostLimitExceededError(CostLimitExceededError):
    def __init__(self, current_cost: float, max_cost: float):
        self.current_cost = current_cost
        self.max_cost = max_cost
        super().__init__(f"Total cost limit exceeded: ${current_cost:.4f} >= ${max_cost:.2f}")


# --- Global Stats (thread-safe) ---

class GlobalStats:
    def __init__(self):
        self.total_cost: float = 0.0
        self.total_tokens_sent: int = 0
        self.total_tokens_received: int = 0
        self.total_api_calls: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_cost": round(self.total_cost, 6),
            "total_tokens_sent": self.total_tokens_sent,
            "total_tokens_received": self.total_tokens_received,
            "total_api_calls": self.total_api_calls,
        }


_global_stats = GlobalStats()
_global_stats_lock = Lock()


def get_global_stats() -> Dict[str, Any]:
    with _global_stats_lock:
        return _global_stats.to_dict()


# --- Instance Stats ---

class InstanceStats:
    def __init__(self):
        self.instance_cost: float = 0.0
        self.tokens_sent: int = 0
        self.tokens_received: int = 0
        self.api_calls: int = 0

    def __add__(self, other: 'InstanceStats') -> 'InstanceStats':
        result = InstanceStats()
        result.instance_cost = self.instance_cost + other.instance_cost
        result.tokens_sent = self.tokens_sent + other.tokens_sent
        result.tokens_received = self.tokens_received + other.tokens_received
        result.api_calls = self.api_calls + other.api_calls
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_cost": round(self.instance_cost, 6),
            "tokens_sent": self.tokens_sent,
            "tokens_received": self.tokens_received,
            "total_tokens": self.tokens_sent + self.tokens_received,
            "api_calls": self.api_calls,
        }


# --- Cost Tracker ---

def _litellm_model_name(model: str) -> str:
    """Strip provider prefix (e.g. 'openai:gpt-5-mini' -> 'gpt-5-mini')."""
    return model.split(":", 1)[-1] if ":" in model else model


def _cost_from_litellm_response(usage_dict: Dict[str, Any], model: str) -> float:
    """Use litellm.completion_cost for accurate pricing (handles caching, reasoning tokens, etc.)."""
    try:
        model_response = litellm.ModelResponse(model=_litellm_model_name(model), usage=usage_dict)
        return litellm.cost_calculator.completion_cost(
            completion_response=model_response, model=_litellm_model_name(model)
        )
    except Exception:
        # Fallback to simple calculation
        input_tokens = usage_dict.get('prompt_tokens', 0)
        output_tokens = usage_dict.get('completion_tokens', 0)
        try:
            info = litellm.get_model_info(_litellm_model_name(model))
            in_cost = info.get('input_cost_per_token', 3e-06)
            out_cost = info.get('output_cost_per_token', 1.5e-05)
        except Exception:
            in_cost, out_cost = 3e-06, 1.5e-05
        return (input_tokens * in_cost) + (output_tokens * out_cost)


class CostTracker:
    """Tracks costs for LLM API calls using LiteLLM pricing."""

    def __init__(self, max_cost: float = DEFAULT_MAX_COST_PER_INSTANCE, total_cost_limit: float = 0.0):
        self.max_cost = max_cost
        self.total_cost_limit = total_cost_limit
        self.stats = InstanceStats()
        self.calls: List[Dict[str, Any]] = []
        self.logger = logging.getLogger("CostTracker")

    def add_call_from_response(self, response: Any, model: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        input_tokens, output_tokens = 0, 0
        usage_dict = None

        # Extract token_usage dict from LangChain response_metadata
        if hasattr(response, 'response_metadata'):
            meta = response.response_metadata
            if isinstance(meta, dict):
                if meta.get('token_usage'):
                    # OpenAI format
                    usage_dict = meta['token_usage']
                elif meta.get('usage'):
                    # Anthropic format — convert to litellm-compatible dict
                    raw = meta['usage']
                    usage_dict = {
                        'prompt_tokens': raw.get('input_tokens', 0) + raw.get('cache_read_input_tokens', 0) + raw.get('cache_creation_input_tokens', 0),
                        'completion_tokens': raw.get('output_tokens', 0),
                        'cache_read_input_tokens': raw.get('cache_read_input_tokens', 0),
                        'cache_creation_input_tokens': raw.get('cache_creation_input_tokens', 0),
                    }

        # Extract token counts (for stats tracking)
        if usage_dict:
            input_tokens = usage_dict.get('prompt_tokens', 0)
            output_tokens = usage_dict.get('completion_tokens', 0)
        elif hasattr(response, 'usage_metadata'):
            usage = response.usage_metadata
            if isinstance(usage, dict):
                input_tokens = usage.get('input_tokens', 0)
                output_tokens = usage.get('output_tokens', 0)
            else:
                input_tokens = getattr(usage, 'input_tokens', 0)
                output_tokens = getattr(usage, 'output_tokens', 0)

        # Calculate cost
        # Try OpenRouter's raw captured cost first (most accurate)
        openrouter_cost = get_openrouter_cost()

        if openrouter_cost is not None:
            cost = openrouter_cost
        elif usage_dict:
            cost = _cost_from_litellm_response(usage_dict, model)
        else:
            # Fallback: build a minimal usage dict from what we have
            cost = _cost_from_litellm_response(
                {'prompt_tokens': input_tokens, 'completion_tokens': output_tokens},
                model,
            )

        return self._update_stats(input_tokens, output_tokens, cost, model, metadata)

    def add_call(self, model: str, input_tokens: int, output_tokens: int,
                 metadata: Optional[Dict] = None, cost: Optional[float] = None) -> Dict[str, Any]:
        if cost is None:
            cost = _cost_from_litellm_response(
                {'prompt_tokens': input_tokens, 'completion_tokens': output_tokens},
                model,
            )
        return self._update_stats(input_tokens, output_tokens, cost, model, metadata)

    def _update_stats(self, input_tokens: int, output_tokens: int, cost: float,
                      model: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        with _global_stats_lock:
            _global_stats.total_cost += cost
            _global_stats.total_tokens_sent += input_tokens
            _global_stats.total_tokens_received += output_tokens
            _global_stats.total_api_calls += 1

        self.stats.instance_cost += cost
        self.stats.tokens_sent += input_tokens
        self.stats.tokens_received += output_tokens
        self.stats.api_calls += 1

        # Capture cache info from the most recent OpenRouter response (if any)
        cache_read = 0
        cache_creation = 0
        try:
            raw = _openrouter_capture.last_raw_usage or {}
            details = raw.get("prompt_tokens_details") or {}
            cache_read = int(details.get("cached_tokens") or raw.get("cache_read_input_tokens") or 0)
            cache_creation = int(raw.get("cache_creation_input_tokens") or 0)
        except Exception:
            pass

        call_record = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "cost": round(cost, 6),
            "cumulative_instance_cost": round(self.stats.instance_cost, 6),
            "cumulative_global_cost": round(_global_stats.total_cost, 6),
            "metadata": metadata or {}
        }
        self.calls.append(call_record)

        self.logger.debug(
            f"in={input_tokens:,}, out={output_tokens:,}, "
            f"cost=${cost:.4f}, total=${self.stats.instance_cost:.4f}"
        )

        if self.total_cost_limit > 0 and _global_stats.total_cost >= self.total_cost_limit:
            raise TotalCostLimitExceededError(_global_stats.total_cost, self.total_cost_limit)
        # max_cost is treated as a true per-instance total across all sub-agents in this process.
        if self.max_cost > 0 and _global_stats.total_cost >= self.max_cost:
            raise InstanceCostLimitExceededError(_global_stats.total_cost, self.max_cost)

        return call_record

    def check_limit(self) -> bool:
        return self.max_cost > 0 and _global_stats.total_cost >= self.max_cost

    def get_remaining_budget(self) -> float:
        if self.max_cost <= 0:
            return float('inf')
        return max(0, self.max_cost - _global_stats.total_cost)

    def get_summary(self) -> Dict[str, Any]:
        return {
            "total_calls": self.stats.api_calls,
            "total_input_tokens": self.stats.tokens_sent,
            "total_output_tokens": self.stats.tokens_received,
            "total_tokens": self.stats.tokens_sent + self.stats.tokens_received,
            "total_cost_usd": round(self.stats.instance_cost, 6),
            "max_cost_usd": self.max_cost,
            "remaining_budget_usd": round(self.get_remaining_budget(), 6),
            "limit_exceeded": self.check_limit(),
            "global_stats": get_global_stats(),
            "calls": self.calls
        }

    def get_total_cost(self) -> float:
        return round(self.stats.instance_cost, 6)


# --- Detailed Logger ---

class DetailedLogger:
    """Logs all agent interactions to .log (human-readable) and .json (machine-readable) files."""

    def __init__(self, agent_name: str, instance_id: str, log_dir: str = "logs",
                 model_name: str = "openai:gpt-5-mini",
                 max_cost: float = DEFAULT_MAX_COST_PER_INSTANCE, total_cost_limit: float = 0.0):
        self.agent_name = agent_name
        self.instance_id = instance_id
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True, parents=True)
        self.model_name = model_name
        self.max_cost = max_cost
        self.interaction_count = 0
        self._json_entries = []

        self.text_log_path = self.log_dir / f"{instance_id}_{agent_name}.log"
        self.json_log_path = self.log_dir / f"{instance_id}_{agent_name}.json"

        self.logger = logging.getLogger(f"{agent_name}.{instance_id}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers = []
        self.logger.propagate = False

        self.file_handler = logging.FileHandler(self.text_log_path, mode='w')
        self.file_handler.setLevel(logging.DEBUG)
        self.file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        self.logger.addHandler(self.file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        self.logger.addHandler(console_handler)

        self.cost_tracker = CostTracker(max_cost=max_cost, total_cost_limit=total_cost_limit)

    def log_start(self, config: Dict[str, Any]):
        self.logger.info(f"{'='*80}")
        self.logger.info(f"[{self.agent_name}] STARTING | Instance: {self.instance_id} | Model: {self.model_name} | Max Cost: ${self.max_cost:.2f}")
        self.logger.info(f"Config: {json.dumps(config, indent=2)}")
        self.logger.info(f"{'='*80}")
        self._write_json({"type": "agent_start", "config": config, "cost_limits": {"max_cost": self.max_cost}})

    def log_plan(self, plan: str):
        self.logger.info(f"[{self.agent_name}] PLAN RECEIVED\n{plan}")
        self._write_json({"type": "plan", "plan": plan})

    def log_context_received(self, source: str, context: Dict[str, Any]):
        self.logger.info(f"[{self.agent_name}] CONTEXT from {source}: {json.dumps(context, indent=2, default=str)}")
        self._write_json({"type": "context_received", "source": source, "context": context})

    def log_llm_call_from_response(self, prompt: str, response: Any, metadata: Optional[Dict] = None):
        self.interaction_count += 1
        model = (metadata or {}).get("model", self.model_name)

        response_text = ""
        if hasattr(response, 'content'):
            response_text = response.content if isinstance(response.content, str) else str(response.content)
        else:
            response_text = str(response)

        try:
            cost_info = self.cost_tracker.add_call_from_response(
                response=response, model=model,
                metadata={"interaction": self.interaction_count, "agent": self.agent_name}
            )
        except CostLimitExceededError:
            cost_info = {"cost": 0, "input_tokens": 0, "output_tokens": 0, "limit_exceeded": True}

        self._log_llm_interaction(prompt, response_text, cost_info, metadata)

    def log_llm_call(self, prompt: str, response: str, metadata: Optional[Dict] = None,
                     input_tokens: int = None, output_tokens: int = None,
                     raw_response: Any = None):
        self.interaction_count += 1
        model = (metadata or {}).get("model", self.model_name)

        # If raw LLM response object is provided, extract accurate token counts from it
        if raw_response is not None:
            try:
                cost_info = self.cost_tracker.add_call_from_response(
                    response=raw_response, model=model,
                    metadata={"interaction": self.interaction_count, "agent": self.agent_name}
                )
            except CostLimitExceededError:
                cost_info = {"cost": 0, "input_tokens": 0, "output_tokens": 0, "limit_exceeded": True}
        else:
            if input_tokens is None:
                input_tokens = litellm.utils.token_counter(text=prompt, model=model.replace("anthropic:", "")) if prompt else 0
            if output_tokens is None:
                output_tokens = litellm.utils.token_counter(text=response, model=model.replace("anthropic:", "")) if response else 0

            try:
                cost_info = self.cost_tracker.add_call(
                    model=model, input_tokens=input_tokens, output_tokens=output_tokens,
                    metadata={"interaction": self.interaction_count, "agent": self.agent_name}
                )
            except CostLimitExceededError:
                cost_info = {"cost": 0, "input_tokens": input_tokens, "output_tokens": output_tokens, "limit_exceeded": True}

        self._log_llm_interaction(prompt, response, cost_info, metadata)

    def _log_llm_interaction(self, prompt: str, response: str, cost_info: Dict, metadata: Optional[Dict] = None):
        in_tok = cost_info.get('input_tokens', 0)
        out_tok = cost_info.get('output_tokens', 0)
        cost = cost_info.get('cost', 0)
        step = (metadata or {}).get("step", self.interaction_count)

        self.logger.info(
            f"\n{'='*60}\n"
            f"[{self.agent_name}] Step {step} | "
            f"cost=${cost:.6f} (in:{in_tok:,} out:{out_tok:,}) total=${self.cost_tracker.stats.instance_cost:.4f}\n"
            f"{'='*60}\n"
            f"PROMPT:\n{prompt}\n"
            f"{'-'*40}\n"
            f"RESPONSE:\n{response}\n"
            f"{'='*60}"
        )

        self._write_json({
            "type": "llm_interaction", "interaction_number": self.interaction_count,
            "prompt": prompt, "response": response, "metadata": metadata or {},
            "tokens": {"input": in_tok, "output": out_tok, "total": in_tok + out_tok},
            "cost": cost_info
        })

    def log_tool_call(self, tool_name: str, args: Dict[str, Any], result: str):
        self.logger.info(f"  TOOL: {tool_name}({json.dumps(args)})\n  RESULT: {result}")
        self._write_json({"type": "tool_call", "tool": tool_name, "args": args, "result": result})

    def log_parsed_plans(self, stage: str, plans: Dict[str, Any]):
        self.logger.info(f"[{self.agent_name}] Parsed plans from {stage}")
        self._write_json({"type": "parsed_plans", "stage": stage, "plans": plans})

    def log_result(self, result: Dict[str, Any]):
        summary = self.cost_tracker.get_summary()
        self.logger.info(
            f"[{self.agent_name}] COMPLETED | "
            f"Cost: ${summary['total_cost_usd']:.4f} | "
            f"Tokens: {summary['total_tokens']:,} | "
            f"Calls: {summary['total_calls']}"
        )
        self._write_json({"type": "agent_result", "result": result, "cost_summary": summary})

    def log_error(self, error: Exception, context: Optional[str] = None):
        self.logger.error(f"[{self.agent_name}] ERROR: {type(error).__name__}: {error}")
        if context:
            self.logger.error(f"Context: {context}")
        self.logger.error(traceback.format_exc())
        self._write_json({
            "type": "error", "error": str(error), "error_type": type(error).__name__,
            "context": context, "traceback": traceback.format_exc()
        })

    def log_step(self, step_num: int, description: str, details: Optional[Dict[str, Any]] = None):
        self.logger.info(f"[{self.agent_name}] Step {step_num}: {description}")
        self._write_json({"type": "step", "step_num": step_num, "description": description, "details": details})

    def log_message(self, level: str, message: str):
        getattr(self.logger, level.lower(), self.logger.info)(message)
        self._write_json({"type": "message", "level": level, "message": message})

    def log_file_operation(self, operation: str, file_path: str, content: Optional[str] = None):
        self.logger.info(f"[{self.agent_name}] FILE {operation.upper()}: {file_path}")
        self._write_json({
            "type": "file_operation", "operation": operation,
            "file_path": file_path, "content_length": len(content) if content else 0
        })

    def get_cost_summary(self) -> Dict[str, Any]:
        return self.cost_tracker.get_summary()

    def get_instance_stats(self) -> InstanceStats:
        return self.cost_tracker.stats

    def is_cost_limit_exceeded(self) -> bool:
        return self.cost_tracker.check_limit()

    def get_remaining_budget(self) -> float:
        return self.cost_tracker.get_remaining_budget()

    def should_stop(self) -> bool:
        if self.cost_tracker.check_limit():
            self.logger.warning(
                f"[{self.agent_name}] Total cost limit exceeded: "
                f"${_global_stats.total_cost:.4f} >= ${self.max_cost:.2f}"
            )
            return True
        return False

    def close(self):
        if hasattr(self, 'file_handler') and self.file_handler:
            self.file_handler.close()
            self.logger.removeHandler(self.file_handler)

    def _write_json(self, entry: Dict[str, Any]):
        entry.setdefault("timestamp", datetime.now().isoformat())
        entry.setdefault("agent", self.agent_name)
        self._json_entries.append(entry)
        with open(self.json_log_path, "w") as f:
            json.dump(self._json_entries, f, indent=2, default=str)
