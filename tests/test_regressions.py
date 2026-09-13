"""Regression tests for three defects found by probing the built system.

Separate from `test_agent.py` so the ten tests the brief asked for stay exactly
as specified. Each test here corresponds to a behaviour that was observed
first and fixed second.
"""

from __future__ import annotations

import asyncio
import itertools
import time

from app import main
from app.agent import MAX_STEPS, HANDOFF_LINE, run_turn
from app.llm import LLMResult, ToolCall
from app.state import STATES, Conversation

_ids = itertools.count(1)


def use(name: str, tool_input: dict | None = None) -> LLMResult:
    call = ToolCall(id=f"toolu_r{next(_ids)}", name=name, input=dict(tool_input or {}))
    return LLMResult(
        text="",
        tool_calls=[call],
        raw_content=[{"type": "tool_use", "id": call.id, "name": call.name,
                      "input": call.input}],
        latency_ms=5,
        stop_reason="tool_use",
    )


def test_route_to_cannot_reach_the_handoff_state():
    """A model ignoring the `department` enum must not strand the customer.

    `human_handoff` is terminal and has no tools, so arriving there without
    `escalate_to_human` having run means the customer is told a teammate is
    coming while nothing was queued.
    """
    conv = Conversation()
    result = run_turn(conv, "hi", llm=lambda **kw: use("route_to", {"department": "human_handoff"}))

    assert conv.state == "triage", "route_to must not reach the terminal state"
    assert result.state == "triage"
    assert conv.ticket["handoff_summary"] == ""
    assert HANDOFF_LINE not in result.reply, "never promise a handoff that was not queued"
    # And the table itself no longer offers the edge from anywhere.
    assert not any("human_handoff" in s["exits"] for s in STATES.values())


def test_turn_starting_in_handoff_never_calls_the_model():
    """The state has no tools, so a model asked to reply there can only invent."""
    conv = Conversation()
    conv.state = "human_handoff"

    def explode(**kwargs):
        raise AssertionError("the model must not be called once a teammate owns this")

    result = run_turn(conv, "wait, can you still help me?", llm=explode)

    assert result.reply == HANDOFF_LINE
    assert result.llm_calls == 0
    assert result.llm_ms == 0
    assert conv.messages[-2]["content"] == "wait, can you still help me?", "record what they said"


def test_should_stop_halts_the_loop_at_a_round_boundary():
    """An abandoned turn stops spending calls instead of running to MAX_STEPS."""
    calls = {"n": 0}

    def llm(**kwargs):
        calls["n"] += 1
        return use("route_to", {"department": "orders"})

    conv = Conversation()
    # False for the first round, True afterwards: the in-flight call finishes,
    # nothing further is spent.
    flags = iter([False, True, True, True, True])
    run_turn(conv, "hello", llm=llm, should_stop=lambda: next(flags))

    assert calls["n"] == 1
    assert calls["n"] < MAX_STEPS


def test_a_timed_out_turn_commits_nothing(monkeypatch):
    """The stored conversation must be untouched by a turn the customer was
    told had failed — no messages, no state change, no tool side effects."""

    def slow(system, messages, tools=None, max_tokens=350):
        time.sleep(0.4)
        return use("route_to", {"department": "orders"})

    monkeypatch.setattr(main, "complete", slow)
    monkeypatch.setattr(main, "TIMEOUT_S", 0.15)

    response = asyncio.run(main.chat(main.ChatRequest(text="where is my order")))
    assert response["reply"] == main.TIMEOUT_REPLY
    assert response["state"] == "triage"

    stored = main._STORE[response["conversation_id"]]
    time.sleep(0.8)  # let the abandoned turn get as far as it is going to get

    assert stored.messages == [], "the abandoned turn wrote into the stored conversation"
    assert stored.state == "triage"
    assert stored.turns == 0
