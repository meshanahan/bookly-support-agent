"""The control flow: one explicit loop, ours end to end.

Rule (Horthy): own the control flow — `MAX_STEPS` tool rounds per user turn,
bounding LLM calls and cost (not wall-clock time).
Rule (Horthy): own the context window — we build the messages list, and
`compact_context` only ever cuts at a plain-string user message so no
`tool_result` is orphaned from its `tool_use`.
Rule (Horthy): stateless reducer — `run_turn(conv, text, llm) -> TurnResult`,
no globals, no threads, testable with a fake LLM.
Rule (Kramer): function-calling discipline — every `tool_use` gets exactly one
matching `tool_result`, and the state's allowlist is enforced here in Python.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from .llm import LLMResult, complete
from .prompts import build_system_prompt
from .state import STATES, Conversation, can_transition, is_tool_allowed
from .tools import TOOL_DISPATCH, schemas_for

MAX_STEPS = 4
KEEP_MESSAGES = 16
HANDOFF_LINE = "I'm connecting you with a teammate now — they'll pick up this conversation."
FALLBACK_LINE = "Let me get a teammate to take this from here."


@dataclass
class TurnResult:
    reply: str
    state: str
    trace: list[dict[str, Any]] = field(default_factory=list)
    llm_ms: int = 0
    total_ms: int = 0
    llm_calls: int = 0


def today() -> str:
    return date.today().isoformat()


def compact_context(messages: list[dict[str, Any]], keep: int = KEEP_MESSAGES) -> list[dict]:
    """Keep roughly the last `keep` messages, cutting only at a real customer turn.

    A plain-string user message is a real customer turn; a user message whose
    content is a list is a batch of `tool_result` blocks. Cutting at one of
    those would orphan a `tool_result` from its `tool_use` and the API would
    reject the request, so we walk forward to the next safe cut point and keep
    more history than asked for rather than break a pair.
    """
    if len(messages) <= keep:
        return list(messages)
    for i in range(len(messages) - keep, len(messages)):
        m = messages[i]
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return list(messages[i:])
    return list(messages)


def run_tool(name: str, tool_input: dict[str, Any] | None, ctx: dict[str, Any]) -> dict[str, Any]:
    """Execute one tool via the dispatch dict. Errors are compacted to one line."""
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return {"error": "unknown_tool", "tool": name}
    if not isinstance(tool_input, dict):
        return {"error": "invalid_tool_input", "tool": name}
    try:
        payload = fn(ctx=ctx, **tool_input)
    except TypeError as exc:
        return {"error": "invalid_tool_arguments", "detail": _one_line(exc)}
    except Exception as exc:  # never let a tool crash the turn
        return {"error": "tool_failed", "detail": _one_line(exc)}
    return payload if isinstance(payload, dict) else {"error": "tool_returned_non_dict"}


def _one_line(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}".replace("\n", " ")[:200]


def tool_result_block(tool_use_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """One `tool_result` block; content is JSON so the model reads small, exact data."""
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(payload, default=str),
    }


def _apply_effects(
    conv: Conversation, call: Any, payload: dict[str, Any], proposal: dict[str, Any]
) -> None:
    """Set state and ticket slots in code. The model never writes these.

    `proposal` is the pending return as it stood *before* this tool ran, because
    a successful `confirm_return` clears it and reports no reason of its own.
    """
    failed = "error" in payload
    if call.name == "route_to" and can_transition(conv.state, payload.get("routed")):
        conv.state = payload["routed"]
    elif call.name == "escalate_to_human" and not failed:
        conv.state = "human_handoff"
        conv.ticket["handoff_summary"] = payload.get("summary", "")
    elif call.name == "lookup_customer_orders" and not failed:
        conv.ticket["email"] = call.input["email"].strip()
    elif call.name == "lookup_order" and not failed:
        conv.ticket["order_id"] = payload["order_id"]
    elif call.name == "confirm_return" and not failed:
        conv.ticket["return_reason"] = proposal.get("reason", "")


def run_turn(
    conv: Conversation,
    text: str,
    llm: Callable[..., LLMResult] = complete,
) -> TurnResult:
    """Run one customer turn to completion and return what the UI needs.

    Args:
        conv: the conversation being mutated in place (messages, state, ticket).
        text: the customer's utterance — typed, or a speech-to-text transcript.
        llm: the completion function; tests inject a FakeLLM with the same shape.

    Returns:
        TurnResult(reply, state, trace, llm_ms, total_ms, llm_calls).

    GAP 1 (Eddie writes this). The pseudocode below is the contract the tests
    in tests/test_agent.py enforce:

        conv.turns += 1; append user message (plain string); t0 = now; llm_calls = 0
        for step in range(MAX_STEPS):
            system = build_system_prompt(conv.state, conv.mode, today, conv.ticket)
            conv.messages = compact_context(conv.messages)
            out = llm(system=system, messages=conv.messages,
                      tools=schemas_for(STATES[conv.state]["tools"]))
            llm_calls += 1; llm_ms += out.latency_ms
            append assistant message = out.raw_content
            if no tool calls: reply = out.text; break
            ctx = {"ticket": conv.ticket, "turn": conv.turns, "state": conv.state}
            results = []
            for call in out.tool_calls:
                if not is_tool_allowed(conv.state, call.name):
                    payload = {"error": "tool_not_allowed_in_state"}
                else:
                    payload = run_tool(call.name, call.input, ctx)   # exceptions -> {"error": "..."} one line
                trace.append(...); results.append(tool_result block for call.id)
                if call.name == "route_to" and can_transition(conv.state, payload.get("routed")):
                    conv.state = payload["routed"]
                elif call.name == "escalate_to_human":
                    conv.state = "human_handoff"; conv.ticket["handoff_summary"] = ...
                elif call.name == "lookup_customer_orders" and "error" not in payload:
                    conv.ticket["email"] = call.input["email"]
                elif call.name == "lookup_order" and "error" not in payload:
                    conv.ticket["order_id"] = payload["order_id"]
                elif call.name == "confirm_return" and "error" not in payload:
                    conv.ticket["return_reason"] = <from the executed proposal>
            append ONE user message whose content is the list of tool_result blocks
            if conv.state == "human_handoff": reply = out.text or fixed handoff line; break
        else:
            reply = out.text or fixed "let me get a teammate" line
        return TurnResult(reply, conv.state, trace, llm_ms, total_ms, llm_calls)

    Notes that the pseudocode assumes:
      - append the assistant message *before* running the tools, so the context
        is exactly what the model produced and each `tool_use` precedes its
        `tool_result`;
      - build `ctx` fresh each round so `turn` and `state` are current;
      - change `conv.state` *after* appending the result, so the next round
        picks up the new state's prompt and tool subset;
      - `confirm_return` returns no reason — read it off
        `conv.ticket["pending_action"]` before the tool clears it;
      - each trace entry is `{"tool": name, "input": dict, "result": dict}`,
        which is what the tests and the UI read.
    """
    conv.turns += 1
    conv.messages.append({"role": "user", "content": text})
    t0 = time.perf_counter()
    trace: list[dict[str, Any]] = []
    llm_ms = llm_calls = 0
    reply = ""
    out: LLMResult | None = None

    for _ in range(MAX_STEPS):
        system = build_system_prompt(conv.state, conv.mode, today(), conv.ticket)
        conv.messages = compact_context(conv.messages)
        out = llm(
            system=system,
            messages=conv.messages,
            tools=schemas_for(STATES[conv.state]["tools"]),
        )
        llm_calls += 1
        llm_ms += out.latency_ms
        # Append what the model produced before running anything, so every
        # tool_use is already in the context ahead of its tool_result.
        conv.messages.append({"role": "assistant", "content": out.raw_content})

        if not out.tool_calls:
            reply = out.text
            break

        # Fresh each round: `turn` and `state` must be the current ones.
        ctx = {"ticket": conv.ticket, "turn": conv.turns, "state": conv.state}
        results = []
        for call in out.tool_calls:
            proposal = dict(conv.ticket.get("pending_action") or {})
            if not is_tool_allowed(conv.state, call.name):
                payload = {"error": "tool_not_allowed_in_state", "tool": call.name}
            else:
                payload = run_tool(call.name, call.input, ctx)
            trace.append({"tool": call.name, "input": call.input, "result": payload})
            results.append(tool_result_block(call.id, payload))
            _apply_effects(conv, call, payload, proposal)
        # One user message carries every result from this round, so the pairs
        # stay together and compaction can never split them.
        conv.messages.append({"role": "user", "content": results})

        if conv.state == "human_handoff":
            reply = out.text or HANDOFF_LINE
            break
    else:
        reply = (out.text if out else "") or FALLBACK_LINE

    return TurnResult(
        reply=reply,
        state=conv.state,
        trace=trace,
        llm_ms=llm_ms,
        total_ms=int((time.perf_counter() - t0) * 1000),
        llm_calls=llm_calls,
    )
