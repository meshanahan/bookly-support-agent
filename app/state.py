"""Procedures with scoped tools, and the one conversation object.

Rule (Kramer, "script the agent"): each procedure — triage, orders, returns,
general — is a short system prompt, a small tool subset, and the exits it may
take. The edges are loose: `route_to` is reachable from every procedure, so
the model picks the path and this table only decides which tools are reachable
from it. It is not a pre-programmed decision tree.
Rule (Horthy): one `Conversation` object holds messages, state, mode and the
business data (`ticket`); the tool allowlist is enforced here in Python, not
merely by what the model was shown.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

# The procedure table. `human_handoff` is the one terminal procedure: no tools,
# no exits, because a teammate now owns the conversation.
STATES: dict[str, dict[str, list[str]]] = {
    "triage": {
        "tools": ["route_to", "escalate_to_human"],
        "exits": ["orders", "returns", "general", "human_handoff"],
    },
    "orders": {
        "tools": [
            "route_to",
            "lookup_customer_orders",
            "lookup_order",
            "search_policy",
            "escalate_to_human",
        ],
        "exits": ["returns", "general", "human_handoff"],
    },
    "returns": {
        "tools": [
            "route_to",
            "lookup_customer_orders",
            "lookup_order",
            "propose_return",
            "confirm_return",
            "search_policy",
            "escalate_to_human",
        ],
        "exits": ["orders", "general", "human_handoff"],
    },
    "general": {
        "tools": [
            "route_to",
            "search_policy",
            "send_password_reset",
            "escalate_to_human",
        ],
        "exits": ["orders", "returns", "human_handoff"],
    },
    "human_handoff": {"tools": [], "exits": []},
}

INITIAL_STATE = "triage"


def can_transition(frm: str, to: Any) -> bool:
    """True only if `to` is a declared exit of state `frm`."""
    if frm not in STATES or not isinstance(to, str):
        return False
    return to in STATES[frm]["exits"]


def is_tool_allowed(state: str, tool: Any) -> bool:
    """True only if `tool` is in the current state's allowlist."""
    if state not in STATES or not isinstance(tool, str):
        return False
    return tool in STATES[state]["tools"]


def new_ticket() -> dict[str, Any]:
    """The business data slots. Filled in code by `run_turn`, never by the model."""
    return {
        "email": "",
        "order_id": "",
        "return_reason": "",
        "pending_action": None,
        "handoff_summary": "",
    }


class Conversation(BaseModel):
    """Everything one customer inquiry needs; `run_turn` is a reducer over this."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: str = INITIAL_STATE
    mode: str = "text"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    turns: int = 0
    ticket: dict[str, Any] = Field(default_factory=new_ticket)
