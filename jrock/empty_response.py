"""Diagnose an empty model response instead of printing a placeholder.

The old code did:

    return out or "(model returned an empty message)"

which swallowed the real cause. An empty completion is a FAILURE with a
specific reason, and the user needs to be told which one:

  * `finish_reason=length` with 0 completion tokens
        the token cap was consumed before any answer was produced. With a high
        thinking level the reasoning tokens can eat the whole allowance.
  * `finish_reason=length` with tokens but no text
        the model spent its budget on reasoning and never emitted content.
  * `finish_reason=content_filter`
        the provider blocked the output.
  * a tool-call-only response
        the model wanted to call a tool and produced no prose.
  * a 200 with an empty `choices` array
        the provider returned nothing at all.
  * an error object inside a 200
        some gateways report failures with HTTP 200 and an `error` key.

`diagnose_empty()` returns a structured verdict; `render_empty()` turns it into
one short actionable line. Nothing here invents content.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Causes that a retry with thinking off can plausibly fix.
RETRYABLE_WITH_OFF = {"length_zero_tokens", "length_reasoning_only"}


@dataclass
class EmptyVerdict:
    cause: str
    detail: str
    finish_reason: str | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    http_status: int | None = None
    retry_with_off: bool = False

    @property
    def is_empty(self) -> bool:
        return self.cause != "ok"


def _usage(body: dict) -> tuple[int | None, int | None]:
    """Return (completion_tokens, reasoning_tokens) from a usage block."""
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None, None
    ct = usage.get("completion_tokens")
    try:
        ct = int(ct) if ct is not None else None
    except (TypeError, ValueError):
        ct = None
    details = usage.get("completion_tokens_details")
    rt = None
    if isinstance(details, dict):
        try:
            rt = int(details.get("reasoning_tokens")) if details.get("reasoning_tokens") is not None else None
        except (TypeError, ValueError):
            rt = None
    return ct, rt


def diagnose_empty(body: Any, http_status: int = 200) -> EmptyVerdict:
    """Classify a provider response. Returns cause='ok' when there IS content."""
    if not isinstance(body, dict):
        return EmptyVerdict("bad_body", f"response was {type(body).__name__}, not a JSON object",
                            http_status=http_status)

    # 1) an error object smuggled inside a 200
    err = body.get("error")
    if err:
        msg = err.get("message") if isinstance(err, dict) else str(err)
        code = err.get("code") if isinstance(err, dict) else None
        return EmptyVerdict("error_in_200",
                            f"provider returned an error object: {msg}"
                            + (f" (code={code})" if code is not None else ""),
                            http_status=http_status)

    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return EmptyVerdict("no_choices",
                            "HTTP 200 but `choices` was empty or missing",
                            http_status=http_status)

    first = choices[0] if isinstance(choices[0], dict) else {}
    msg = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = msg.get("content")
    text = content.strip() if isinstance(content, str) else ""
    finish = first.get("finish_reason")
    ct, rt = _usage(body)

    if text:
        return EmptyVerdict("ok", "", finish_reason=finish, completion_tokens=ct,
                            reasoning_tokens=rt, http_status=http_status)

    # 2) tool-call-only response
    tool_calls = msg.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        names = [t.get("function", {}).get("name") for t in tool_calls
                 if isinstance(t, dict)]
        names = [n for n in names if n]
        return EmptyVerdict("tool_call_only",
                            f"model returned {len(tool_calls)} tool call(s) and no text"
                            + (f": {', '.join(names)}" if names else ""),
                            finish_reason=finish, completion_tokens=ct,
                            reasoning_tokens=rt, http_status=http_status)

    # 3) content filter
    if finish == "content_filter":
        return EmptyVerdict("content_filter",
                            "the provider blocked the output (content_filter)",
                            finish_reason=finish, completion_tokens=ct,
                            reasoning_tokens=rt, http_status=http_status)

    # 4) length exhaustion - the interesting one
    if finish == "length":
        if not ct:
            return EmptyVerdict("length_zero_tokens",
                                "hit the token cap before producing any output "
                                "(0 completion tokens)",
                                finish_reason=finish, completion_tokens=ct,
                                reasoning_tokens=rt, http_status=http_status,
                                retry_with_off=True)
        if rt and ct and rt >= ct:
            return EmptyVerdict("length_reasoning_only",
                                f"all {ct} completion tokens were reasoning tokens "
                                f"({rt} reasoning) - nothing left for the answer",
                                finish_reason=finish, completion_tokens=ct,
                                reasoning_tokens=rt, http_status=http_status,
                                retry_with_off=True)
        return EmptyVerdict("length_no_text",
                            f"hit the token cap ({ct} completion tokens) with no text",
                            finish_reason=finish, completion_tokens=ct,
                            reasoning_tokens=rt, http_status=http_status,
                            retry_with_off=True)

    # 5) whitespace-only content
    if isinstance(content, str):
        return EmptyVerdict("whitespace_only",
                            "model returned only whitespace",
                            finish_reason=finish, completion_tokens=ct,
                            reasoning_tokens=rt, http_status=http_status)

    return EmptyVerdict("empty_content",
                        f"model returned no content (finish_reason={finish or 'none'})",
                        finish_reason=finish, completion_tokens=ct,
                        reasoning_tokens=rt, http_status=http_status)


def render_empty(v: EmptyVerdict, thinking_level: str = "medium",
                 max_tokens: int | None = None) -> str:
    """One short, actionable line. Never a bare placeholder."""
    bits = [f"Model returned an empty response ({v.cause}"]
    if v.finish_reason:
        bits.append(f"finish_reason={v.finish_reason}")
    if v.completion_tokens is not None:
        bits.append(f"{v.completion_tokens} completion tokens")
    if v.reasoning_tokens:
        bits.append(f"{v.reasoning_tokens} reasoning tokens")
    head = ", ".join(bits) + ")."

    lines = [head, v.detail]

    if v.cause in RETRYABLE_WITH_OFF:
        if thinking_level in ("high", "max"):
            lines.append(f"Your thinking level is {thinking_level}, which can consume "
                         f"the whole output budget before any answer is written.")
        if max_tokens:
            lines.append(f"Configured max_tokens={max_tokens}.")
        lines.append("Try /think low (or /think off), or raise max_tokens.")
    elif v.cause == "content_filter":
        lines.append("Rephrase the request; the provider refused to answer this one.")
    elif v.cause == "tool_call_only":
        lines.append("The model wanted a tool call. Ask it to answer in plain text.")
    elif v.cause == "no_choices":
        lines.append("Try /set_model <name> or /models to pick a different model.")
    elif v.cause == "error_in_200":
        lines.append("This is a provider-side failure, not a prompt problem. "
                     "Check /check_ai and your account balance.")
    else:
        lines.append("Try /think low or /set_model <name>.")

    return "\n".join(lines)


def budget_warning(thinking_level: str, max_tokens: int | None,
                   thinking_budget: int | None) -> str | None:
    """Warn when the output cap cannot fit the reasoning budget.

    Returns None when the configuration is sane.
    """
    if not max_tokens or not thinking_budget:
        return None
    if thinking_level in ("off", "low"):
        return None
    if max_tokens <= thinking_budget:
        return (f"max_tokens={max_tokens} is not larger than the thinking budget "
                f"({thinking_budget} tokens for level {thinking_level}). The model "
                f"may spend the entire allowance on reasoning and return nothing. "
                f"Raise max_tokens above {thinking_budget} or lower /think.")
    return None
