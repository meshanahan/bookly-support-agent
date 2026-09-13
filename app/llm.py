"""The only file that knows an LLM vendor exists.

Rule (Kramer): latency is a requirement — every call is timed here and the
measured milliseconds travel back with the result; the default is a fast small
model.
Rule (one file to swap vendors): nothing else imports an SDK, and the import is
lazy, so the offline tests never need the SDK installed and never touch the
network.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_content: Any = None  # exactly what the assistant produced, for the context
    latency_ms: int = 0
    stop_reason: str = "end_turn"
    usage: dict[str, int] = field(default_factory=dict)


def complete(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 350,
) -> LLMResult:
    """One LLM call. Provider from env; timed here so latency is measured, not assumed."""
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    t0 = time.perf_counter()
    call = _openai if provider == "openai" else _anthropic
    result = call(system, messages, tools or [], max_tokens)
    result.latency_ms = int((time.perf_counter() - t0) * 1000)
    return result


def _anthropic(system, messages, tools, max_tokens) -> LLMResult:
    import anthropic  # lazy: tests never import the SDK

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=ANTHROPIC_MODEL, max_tokens=max_tokens, system=system,
        messages=messages, tools=tools,
    )
    return LLMResult(
        text="".join(b.text for b in resp.content if b.type == "text").strip(),
        tool_calls=[
            ToolCall(id=b.id, name=b.name, input=dict(b.input or {}))
            for b in resp.content if b.type == "tool_use"
        ],
        raw_content=[b.model_dump(exclude_none=True) for b in resp.content],
        stop_reason=resp.stop_reason or "end_turn",
        usage={"input": resp.usage.input_tokens, "output": resp.usage.output_tokens},
    )


def _openai(system, messages, tools, max_tokens) -> LLMResult:
    """Same LLMResult shape; the message translation stays inside this file."""
    from openai import OpenAI  # lazy

    resp = OpenAI(api_key=os.environ["OPENAI_API_KEY"]).chat.completions.create(
        model=OPENAI_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}] + _to_openai(messages),
        tools=[
            {"type": "function", "function": {
                "name": t["name"], "description": t.get("description", ""),
                "parameters": t["input_schema"]}}
            for t in tools
        ] or None,
    )
    msg = resp.choices[0].message
    calls = [
        ToolCall(id=c.id, name=c.function.name, input=json.loads(c.function.arguments or "{}"))
        for c in (msg.tool_calls or [])
    ]
    text = (msg.content or "").strip()
    raw = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls
    ]
    return LLMResult(
        text=text, tool_calls=calls, raw_content=raw,
        stop_reason=resp.choices[0].finish_reason or "end_turn",
        usage={"input": resp.usage.prompt_tokens, "output": resp.usage.completion_tokens},
    )


def _to_openai(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic-shaped content blocks -> OpenAI chat messages."""
    out: list[dict[str, Any]] = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
        elif m["role"] == "assistant":
            msg: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(b.get("text", "") for b in content
                                   if b.get("type") == "text") or None,
            }
            tool_calls = [
                {"id": b["id"], "type": "function",
                 "function": {"name": b["name"], "arguments": json.dumps(b.get("input", {}))}}
                for b in content if b.get("type") == "tool_use"
            ]
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        else:  # a batch of tool_result blocks becomes one OpenAI message each
            out += [
                {"role": "tool", "tool_call_id": b["tool_use_id"],
                 "content": json.dumps(b.get("content"))}
                for b in content
            ]
    return out
