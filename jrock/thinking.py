"""Thinking levels: how hard the model reasons before it answers.

This is a SETTING, not a chat mode. You never need it to talk to the agent.
It only changes how much reasoning the model is asked to do.

Levels, in order of increasing effort:

    off     no reasoning parameter is sent at all - fastest, cheapest
    low     short reasoning
    medium  default
    high    long reasoning, more tokens
    max     deepest reasoning, most tokens, slowest

Two provider styles are supported and the choice is driven by the model's own
`/models` metadata, never guessed:

  * OpenAI-compatible reasoning models  ->  {"reasoning_effort": "low|medium|high"}
  * budget-style thinking models        ->  {"thinking_budget": <tokens>}

`off` sends NOTHING. A model that does not advertise reasoning support gets
NOTHING - we never send an unsupported field and let the request 400.
"""
from __future__ import annotations

import difflib
import logging
import os
from typing import Any

log = logging.getLogger("jrock.thinking")

LEVELS: list[str] = ["off", "low", "medium", "high", "max"]
DEFAULT_LEVEL = "medium"

# Accepted spellings -> canonical level.
ALIASES: dict[str, str] = {
    "off": "off", "none": "off", "no": "off", "0": "off", "false": "off",
    "low": "low", "min": "low", "minimal": "low", "1": "low",
    "medium": "medium", "med": "medium", "mid": "medium", "normal": "medium",
    "default": "medium", "2": "medium",
    "high": "high", "hi": "high", "3": "high",
    "max": "max", "maximum": "max", "maximal": "max", "deepest": "max", "4": "max",
}

# Token budget for budget-style providers.
BUDGETS: dict[str, int] = {"off": 0, "low": 1024, "medium": 4096,
                           "high": 16384, "max": 32768}

# reasoning_effort for OpenAI-compatible providers. OpenAI only defines
# low/medium/high, so `max` maps to `high` and gets the extra depth from the
# budget when the model supports one.
EFFORTS: dict[str, str | None] = {"off": None, "low": "low", "medium": "medium",
                                  "high": "high", "max": "high"}

ONE_LINER = ("/think sets how much the model reasons before answering. "
             "It is a setting, not a chat mode - just type normally to talk to me.")

LEVEL_HELP: dict[str, str] = {
    "off": "no reasoning, fastest, cheapest",
    "low": "short reasoning",
    "medium": "default - balanced",
    "high": "long reasoning, more tokens",
    "max": "deepest reasoning, most tokens, slowest",
}


def normalize_level(value: Any) -> str | None:
    """Map any accepted spelling to a canonical level, or None if invalid."""
    if value is None:
        return None
    v = str(value).strip().lower()
    if not v:
        return None
    return ALIASES.get(v)


def closest_level(value: str) -> str | None:
    """Nearest valid level for a typo, or None when nothing is close."""
    v = str(value or "").strip().lower()
    if not v:
        return None
    matches = difflib.get_close_matches(v, LEVELS, n=1, cutoff=0.5)
    if matches:
        return matches[0]
    # also try the alias table (e.g. "maxx" -> "max")
    matches = difflib.get_close_matches(v, list(ALIASES), n=1, cutoff=0.6)
    return ALIASES[matches[0]] if matches else None


def level_from_env(default: str = DEFAULT_LEVEL) -> str:
    """Read THINKING_LEVEL from the environment, falling back to `default`."""
    raw = os.getenv("THINKING_LEVEL")
    lvl = normalize_level(raw)
    if raw and lvl is None:
        log.warning("THINKING_LEVEL=%r is not a valid level; using %s", raw, default)
    return lvl or default


def build_reasoning_params(level: str, supports_reasoning: bool | None = None,
                           reasoning_param: str | None = None) -> dict[str, Any]:
    """Build the provider request fields for a thinking level.

    Returns an EMPTY dict when:
      * the level is `off` (send no reasoning parameter at all), or
      * the model is known not to support reasoning.

    `supports_reasoning=None` means "unknown" - we then use the provider's
    default style (reasoning_effort) rather than silently dropping the setting.
    """
    lvl = normalize_level(level) or DEFAULT_LEVEL
    if lvl == "off":
        return {}
    if supports_reasoning is False:
        return {}

    param = reasoning_param
    if param == "thinking_budget":
        return {"thinking_budget": BUDGETS[lvl]}
    if param == "reasoning_effort":
        effort = EFFORTS[lvl]
        return {"reasoning_effort": effort} if effort else {}
    # Unknown style: OpenAI-compatible default.
    effort = EFFORTS[lvl]
    return {"reasoning_effort": effort} if effort else {}


def describe_params(params: dict[str, Any]) -> str:
    """One-line description of what will actually be sent, for debug logs."""
    if not params:
        return "no reasoning parameter"
    return ", ".join(f"{k}={v}" for k, v in params.items())


def detect_reasoning(raw: dict, model_id: str) -> tuple[bool | None, str | None]:
    """Read a /models entry and decide whether it supports reasoning.

    Returns (supports, param_style) where param_style is
    "reasoning_effort" | "thinking_budget" | None.
    """
    if not isinstance(raw, dict):
        return None, None

    # 1) explicit supported_parameters list (OpenRouter / Kilo)
    for key in ("supported_parameters", "supportedParameters", "parameters"):
        sup = raw.get(key)
        if isinstance(sup, list):
            low = {str(x).lower() for x in sup}
            if "reasoning_effort" in low:
                return True, "reasoning_effort"
            if low & {"thinking", "reasoning", "include_reasoning", "reasoning_budget"}:
                return True, "thinking_budget"
            # an explicit list without any reasoning entry is authoritative
            if low:
                return False, None

    # 2) capabilities block
    caps = raw.get("capabilities")
    if isinstance(caps, dict) and "reasoning" in caps:
        if bool(caps.get("reasoning")):
            return True, "reasoning_effort"
        return False, None

    # 3) flat flags
    for key in ("reasoning", "supports_reasoning", "include_reasoning"):
        if isinstance(raw.get(key), bool):
            return bool(raw[key]), ("reasoning_effort" if raw[key] else None)

    # 4) id heuristics - only when the provider told us nothing
    low = (model_id or "").lower()
    if any(p in low for p in ("o1", "o3", "o4", "gpt-5", "deepseek-r1", "qwq",
                              "thinking", "-reasoner", "reasoning")):
        return True, "reasoning_effort"
    return None, None
