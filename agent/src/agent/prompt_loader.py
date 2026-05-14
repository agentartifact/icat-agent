# agent/prompt_loader.py
"""
Prompt Loader - Loads and renders prompts from YAML configuration.

This module provides utilities to:
1. Load prompts from config/prompts.yaml
2. Render prompts with Jinja2 templating
3. Cache prompts for performance
"""
from __future__ import annotations
import os
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from functools import lru_cache
import yaml

try:
    from jinja2 import Template, Environment, BaseLoader
    HAS_JINJA2 = True
except ImportError:
    HAS_JINJA2 = False
    Template = None

log = logging.getLogger("PromptLoader")

# Default prompts file path
DEFAULT_PROMPTS_FILE = Path(__file__).parent.parent / "config" / "prompts.yaml"


class PromptLoader:
    """
    Loads and renders prompts from YAML configuration.

    Usage:
        loader = PromptLoader()
        prompt = loader.get_prompt("localizer", "system_prompt",
                                   repo_path="/testbed", commit="abc123", problem="Bug description")
    """

    _instance: Optional['PromptLoader'] = None

    def __init__(self, prompts_file: Optional[Path] = None):
        """
        Initialize the prompt loader.

        Args:
            prompts_file: Path to the YAML file containing prompts.
                         Defaults to config/prompts.yaml
        """
        self.prompts_file = prompts_file or DEFAULT_PROMPTS_FILE
        self._prompts: Optional[Dict[str, Any]] = None
        self._load_prompts()

    @classmethod
    def get_instance(cls, prompts_file: Optional[Path] = None) -> 'PromptLoader':
        """Get singleton instance of PromptLoader."""
        if cls._instance is None:
            cls._instance = cls(prompts_file)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset the singleton instance (useful for testing)."""
        cls._instance = None

    def _load_prompts(self):
        """Load prompts from YAML file."""
        if not self.prompts_file.exists():
            log.warning(f"Prompts file not found: {self.prompts_file}")
            self._prompts = {}
            return

        try:
            with open(self.prompts_file, 'r') as f:
                self._prompts = yaml.safe_load(f) or {}
            log.info(f"Loaded prompts from {self.prompts_file}")
        except Exception as e:
            log.error(f"Failed to load prompts from {self.prompts_file}: {e}")
            self._prompts = {}

    def reload(self):
        """Reload prompts from file (useful for development)."""
        self._load_prompts()

    def get_raw_prompt(self, agent: str, prompt_key: str) -> Optional[str]:
        """
        Get a raw prompt template without rendering.

        Args:
            agent: Agent name (e.g., "localizer", "patch_editor", "planner")
            prompt_key: Key within the agent section (e.g., "system_prompt", "plan_injection")

        Returns:
            Raw prompt template string or None if not found
        """
        if not self._prompts:
            return None

        agent_prompts = self._prompts.get(agent, {})
        if not agent_prompts:
            log.warning(f"No prompts found for agent: {agent}")
            return None

        prompt = agent_prompts.get(prompt_key)
        if prompt is None:
            log.warning(f"Prompt '{prompt_key}' not found for agent '{agent}'")

        return prompt

    def get_prompt(self, agent: str, prompt_key: str, **kwargs) -> Optional[str]:
        """
        Get a rendered prompt with variables substituted.

        Args:
            agent: Agent name (e.g., "localizer", "patch_editor", "planner")
            prompt_key: Key within the agent section (e.g., "system_prompt", "plan_injection")
            **kwargs: Variables to substitute in the template

        Returns:
            Rendered prompt string or None if not found
        """
        template_str = self.get_raw_prompt(agent, prompt_key)
        if template_str is None:
            return None

        return self.render_template(template_str, **kwargs)

    def render_template(self, template_str: str, **kwargs) -> str:
        """
        Render a template string with Jinja2 or simple string formatting.

        Args:
            template_str: Template string with {{ variable }} placeholders
            **kwargs: Variables to substitute

        Returns:
            Rendered string
        """
        if HAS_JINJA2:
            try:
                template = Template(template_str)
                return template.render(**kwargs)
            except Exception as e:
                log.warning(f"Jinja2 rendering failed: {e}, falling back to format")

        # Fallback: Simple {{ variable }} replacement
        result = template_str
        for key, value in kwargs.items():
            result = result.replace("{{ " + key + " }}", str(value))
            result = result.replace("{{" + key + "}}", str(value))

        return result

    def has_prompt(self, agent: str, prompt_key: str) -> bool:
        """Check if a prompt exists."""
        return self.get_raw_prompt(agent, prompt_key) is not None

    def list_agents(self) -> list:
        """List all agents with prompts defined."""
        return list(self._prompts.keys()) if self._prompts else []

    def list_prompts(self, agent: str) -> list:
        """List all prompt keys for an agent."""
        if not self._prompts:
            return []
        agent_prompts = self._prompts.get(agent, {})
        return list(agent_prompts.keys())


# Convenience functions for quick access
_default_loader: Optional[PromptLoader] = None


def get_loader() -> PromptLoader:
    """Get the default prompt loader instance."""
    global _default_loader
    if _default_loader is None:
        _default_loader = PromptLoader.get_instance()
    return _default_loader


def get_prompt(agent: str, prompt_key: str, **kwargs) -> Optional[str]:
    """
    Convenience function to get a rendered prompt.

    Args:
        agent: Agent name (e.g., "localizer", "patch_editor", "planner")
        prompt_key: Key within the agent section (e.g., "system_prompt", "plan_injection")
        **kwargs: Variables to substitute in the template

    Returns:
        Rendered prompt string or None if not found

    Example:
        from agent.prompt_loader import get_prompt

        system_prompt = get_prompt("localizer", "system_prompt",
                                   repo_path="/testbed",
                                   commit="abc123",
                                   problem="The function fails when...")
    """
    return get_loader().get_prompt(agent, prompt_key, **kwargs)


def get_raw_prompt(agent: str, prompt_key: str) -> Optional[str]:
    """
    Convenience function to get a raw prompt template.

    Args:
        agent: Agent name
        prompt_key: Prompt key

    Returns:
        Raw prompt template string or None
    """
    return get_loader().get_raw_prompt(agent, prompt_key)


# Agent-specific helper functions
def get_localizer_prompt(prompt_key: str = "system_prompt", **kwargs) -> Optional[str]:
    """Get a localizer agent prompt."""
    return get_prompt("localizer", prompt_key, **kwargs)


def get_patch_editor_prompt(prompt_key: str = "system_prompt", **kwargs) -> Optional[str]:
    """Get a patch editor agent prompt."""
    return get_prompt("patch_editor", prompt_key, **kwargs)


def get_reproducer_prompt(prompt_key: str = "system_prompt", **kwargs) -> Optional[str]:
    """Get a reproducer agent prompt."""
    return get_prompt("reproducer", prompt_key, **kwargs)


def get_analyze_issue_prompt(prompt_key: str = "system_prompt", **kwargs) -> Optional[str]:
    """Get an analyze issue prompt (used by analyze_issue_description tool)."""
    return get_prompt("analyze_issue", prompt_key, **kwargs)


def get_localizer_planner_prompt(prompt_key: str = "system_prompt", **kwargs) -> Optional[str]:
    """Get a localizer planner prompt (role-specific planner for localizer)."""
    return get_prompt("localizer_planner", prompt_key, **kwargs)


def get_patch_editor_planner_prompt(prompt_key: str = "system_prompt", **kwargs) -> Optional[str]:
    """Get a patch editor planner prompt (role-specific planner for patch editor)."""
    return get_prompt("patch_editor_planner", prompt_key, **kwargs)


def get_reproducer_planner_prompt(prompt_key: str = "system_prompt", **kwargs) -> Optional[str]:
    """Get a reproducer planner prompt (role-specific planner for reproducer)."""
    return get_prompt("reproducer_planner", prompt_key, **kwargs)


def get_plan_evaluation_prompt(prompt_key: str = "quality_prompt", **kwargs) -> Optional[str]:
    """Get a plan evaluation prompt (quality_prompt, alignment_prompt, or refinement_prompt)."""
    return get_prompt("plan_evaluation", prompt_key, **kwargs)
