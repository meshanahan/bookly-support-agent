"""Offline tests for the loop: a FakeLLM replays scripted decisions.

These prove that routing, allowlisting, slot-setting, pair-matching,
compaction and the propose/confirm rule behave correctly *given* a model
decision. They cannot prove the live model decides well — that is Stage 4.

Every test but `test_safe_compaction` calls `run_turn`, so they all fail with
NotImplementedError until GAP 1 is written.

Trace contract: each entry is {"tool": str, "input": dict, "result": dict}.
Test 5 also fails until GAP 2 is written: two states must not share a prompt.
"""

from __future__ import annotations

import itertools
import json

from app.agent import compact_context, run_turn
from app.llm import LLMResult, ToolCall
from app.state import STATES, Conversation

_ids = itertools.count(1)


def say(text: str) -> LLMResult:
    """A scripted plain answer with no tool calls."""
    return LLMResult(
        text=text,
        tool_calls=[],
        raw_content=[{"type": "text", "text": text}],
        latency_ms=11,
        stop_reason="end_turn",
    )


def use(name: str, tool_input: dict | None = None, text: str = "") -> LLMResult:
    """A scripted single tool call."""
    call = ToolCall(id=f"toolu_{next(_ids)}", name=name, input=dict(tool_input or {}))
    raw = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.input}
    ]
    return LLMResult(
        text=text,
        tool_calls=[call],
        raw_content=raw,
        latency_ms=17,
        stop_reason="tool_use",
    )


class FakeLLM:
    """Replays scripted LLMResults; records the system prompt and tool names it saw."""

    def __init__(self, *script: LLMResult):
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, system, messages, tools=None, max_tokens=350) -> LLMResult:
        self.calls.append(
            {
                "system": system,
                "tools": [t["name"] for t in (tools or [])],
                "messages": json.loads(json.dumps(messages, default=str)),
            }
        )
        assert self.script, "FakeLLM ran out of scripted responses"
        return self.script.pop(0)


def results(trace: list[dict], tool: str) -> list[dict]:
    return [e["result"] for e in trace if e["tool"] == tool]


def test_clarifying_question_no_tools():
    """1. 'Where is my order?' with no email: ask, do not guess, run nothing."""
    conv = Conversation()
    fake = FakeLLM(say("Happy to help — what's the email address on the order?"))

    result = run_turn(conv, "where is my order?", fake)

    assert result.reply.strip().endswith("?")
    assert result.trace == []
    assert result.llm_calls == 1
    assert len(fake.calls) == 1


def test_disambiguation_two_orders():
    """2. Two orders for one email come back, and the email slot is set in code."""
    conv = Conversation(state="orders")
    fake = FakeLLM(
        use("lookup_customer_orders", {"email": "maya@bookly-demo.com"}),
        say("I see two orders on that account — which one do you mean?"),
    )

    result = run_turn(conv, "maya@bookly-demo.com", fake)

    dumped = json.dumps(result.trace, default=str)
    assert "BK-10001" in dumped and "BK-10002" in dumped
    assert conv.ticket["email"] == "maya@bookly-demo.com"


def test_lookup_is_scoped_to_the_verified_email():
    """3. No email -> verify_email_first. Wrong owner -> order_not_found."""
    conv = Conversation(state="orders")
    fake = FakeLLM(
        use("lookup_order", {"order_id": "BK-10001"}),
        say("Before I look that up, what's the email on the account?"),
    )
    first = run_turn(conv, "look up BK-10001", fake)
    assert results(first.trace, "lookup_order")[0]["error"] == "verify_email_first"
    assert conv.ticket["order_id"] == ""

    fake = FakeLLM(
        use("lookup_customer_orders", {"email": "maya@bookly-demo.com"}),
        use("lookup_order", {"order_id": "BK-20483"}),
        say("I can't find that order on your account."),
    )
    second = run_turn(conv, "maya@bookly-demo.com, and check BK-20483", fake)
    assert results(second.trace, "lookup_order")[0]["error"] == "order_not_found"


def test_return_is_server_bound_across_turns():
    """4. propose stores a server-side proposal; confirm only works on a later turn."""
    conv = Conversation(state="returns")
    conv.ticket["email"] = "maya@bookly-demo.com"
    fake = FakeLLM(
        use("propose_return", {"order_id": "BK-10002", "reason": "damaged in transit"}),
        use("confirm_return", {}),
        say("I've got a return ready for BK-10002 for damage. Shall I start it?"),
        use("confirm_return", {}),
        say("Done — your return label is on its way by email."),
    )

    turn_n = run_turn(conv, "I want to return Salt and Signal, it arrived damaged", fake)

    assert results(turn_n.trace, "propose_return")[0]["status"] == "awaiting_customer_confirmation"
    assert results(turn_n.trace, "confirm_return")[0]["error"] == "confirm_on_a_later_turn"
    assert conv.ticket["pending_action"]["order_id"] == "BK-10002"
    calls_before = len(fake.calls)

    turn_n1 = run_turn(conv, "yes please", fake)

    assert "Awaiting customer confirmation" in fake.calls[calls_before]["system"]
    assert results(turn_n1.trace, "confirm_return")[0]["rma_id"]
    assert conv.ticket["pending_action"] is None
    assert conv.ticket["return_reason"] == "damaged in transit"


def test_routing_changes_state_and_tool_subset():
    """5. A legal exit moves state; an illegal one is ignored by can_transition."""
    conv = Conversation()
    fake = FakeLLM(
        use("route_to", {"department": "returns"}),
        say("Sure — which book would you like to return?"),
    )

    run_turn(conv, "I want to return a book", fake)

    assert conv.state == "returns"
    assert fake.calls[1]["tools"] == STATES["returns"]["tools"]
    assert fake.calls[1]["system"] != fake.calls[0]["system"]

    other = Conversation()
    fake2 = FakeLLM(
        use("route_to", {"department": "billing"}),
        say("I can't help with billing directly — want me to get a teammate?"),
    )
    run_turn(other, "this is a billing question", fake2)
    assert other.state == "triage"


def test_allowlist_is_enforced_in_code():
    """6. A tool the state does not allow never executes, whatever the model asked."""
    conv = Conversation()
    conv.ticket["email"] = "maya@bookly-demo.com"
    fake = FakeLLM(
        use("propose_return", {"order_id": "BK-10002", "reason": "damaged"}),
        say("Let me move you to returns first."),
    )

    result = run_turn(conv, "just refund BK-10002", fake)

    assert results(result.trace, "propose_return")[0]["error"] == "tool_not_allowed_in_state"
    assert conv.ticket["pending_action"] is None


def test_every_tool_use_has_exactly_one_tool_result():
    """7. Function-calling discipline: matched pairs in the context we built."""
    conv = Conversation(state="orders")
    fake = FakeLLM(
        use("lookup_customer_orders", {"email": "maya@bookly-demo.com"}),
        use("lookup_order", {"order_id": "BK-10001"}),
        say("That one shipped and is on its way."),
    )

    run_turn(conv, "where is my order? maya@bookly-demo.com", fake)

    used, returned = [], []
    for m in conv.messages:
        content = m["content"]
        if isinstance(content, str):
            continue
        for block in content:
            if block.get("type") == "tool_use":
                used.append(block["id"])
            elif block.get("type") == "tool_result":
                returned.append(block["tool_use_id"])
    assert used and sorted(used) == sorted(returned)
    assert len(returned) == len(set(returned))


def test_safe_compaction_never_orphans_a_tool_result():
    """8. Compaction cuts only at a plain-string user message."""
    messages = []
    for i in range(6):
        messages.append({"role": "user", "content": f"customer turn {i}"})
        messages.append(
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": f"t{i}", "name": "search_policy", "input": {}}],
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "{}"}],
            }
        )
        messages.append({"role": "assistant", "content": [{"type": "text", "text": f"answer {i}"}]})
    assert len(messages) == 24

    kept = compact_context(messages)

    assert len(kept) < len(messages)
    assert kept[0]["role"] == "user" and isinstance(kept[0]["content"], str)
    open_ids = set()
    for m in kept:
        if isinstance(m["content"], str):
            continue
        for block in m["content"]:
            if block.get("type") == "tool_use":
                open_ids.add(block["id"])
            elif block.get("type") == "tool_result":
                assert block["tool_use_id"] in open_ids, "orphaned tool_result"


def test_escalation_hands_off_and_stores_the_summary():
    """9. Contacting a human is a tool call; the state pill flips."""
    conv = Conversation()
    fake = FakeLLM(
        use("escalate_to_human", {"summary": "Customer wants a refund on BK-10002."}),
        say("I'm getting a teammate for you now."),
    )

    result = run_turn(conv, "get me a person", fake)

    assert conv.state == "human_handoff"
    assert result.state == "human_handoff"
    assert "BK-10002" in conv.ticket["handoff_summary"]


def test_voice_mixin_only_in_voice_mode():
    """10. Voice is a prompt change, not an agent change."""
    spoken = Conversation(mode="voice")
    voice_fake = FakeLLM(say("What is the email address on the order?"))
    run_turn(spoken, "where is my order", voice_fake)
    assert "read back" in voice_fake.calls[0]["system"].lower()

    typed = Conversation()
    text_fake = FakeLLM(say("What is the email address on the order?"))
    run_turn(typed, "where is my order", text_fake)
    assert "read back" not in text_fake.calls[0]["system"].lower()
