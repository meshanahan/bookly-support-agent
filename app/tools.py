"""Tools: JSON schema in, small dict out, plain dispatch dict.

Rule (Horthy): tools are structured outputs — a schema the model fills, a
Python function that validates every model-generated input before it touches
data, and a small dict back.
Rule (safety by construction, by risk tier): low-risk tools are the read-only
lookups and policy text; medium-risk is the server-bound propose/confirm return
pair; high-risk is money movement, which has no tool at all and must reach a
human. Order data is scoped in code to the email the customer stated
(`ctx["ticket"]["email"]`), and a missing order and someone else's order return
the identical error, so existence is never leaked. No tool lists orders across
customers.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

ORDER_ID_RE = re.compile(r"^BK-\d{5}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
RETURN_WINDOW_DAYS = 30
REFUND_ETA = "5-7 business days after receipt"
PUBLIC_ORDER_FIELDS = (
    "order_id", "status", "carrier", "eta", "delivered_on", "items", "total", "refund",
)


def _day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _order(order_id, email, status, title, fmt, total, qty=1, carrier=None,
           eta_in=None, delivered_ago=None, refund=None) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "email": email,
        "status": status,
        "carrier": carrier,
        "eta": _day(eta_in) if eta_in is not None else None,
        "delivered_on": _day(-delivered_ago) if delivered_ago is not None else None,
        "items": [{"title": title, "format": fmt, "qty": qty}],
        "total": total,
        "refund": refund,
    }


# Six mock orders. maya@bookly-demo.com has two — one shipped, one delivered 12
# days ago — so "where is my order?" forces a clarifying question.
ORDERS: dict[str, dict[str, Any]] = {
    o["order_id"]: o
    for o in [
        _order("BK-10001", "maya@bookly-demo.com", "shipped", "The Lantern Cartographers",
               "paperback", 18.50, carrier="Wren Post", eta_in=3),
        _order("BK-10002", "maya@bookly-demo.com", "delivered", "Salt and Signal",
               "hardcover", 27.00, carrier="Wren Post", delivered_ago=12),
        _order("BK-20483", "theo@bookly-demo.com", "delivered", "Nine Doors to Winter",
               "paperback", 31.25, qty=2, carrier="Harbor Freight Co.", delivered_ago=45),
        _order("BK-30099", "theo@bookly-demo.com", "delivered", "A Grammar of Tides",
               "ebook", 9.99, carrier="digital", delivered_ago=4),
        _order("BK-44120", "priya@bookly-demo.com", "processing", "The Quiet Machinist",
               "paperback", 16.00, eta_in=5),
        _order("BK-55731", "priya@bookly-demo.com", "cancelled", "Field Notes for Sleepwalkers",
               "hardcover", 24.00,
               refund={"amount": 24.00, "status": "refunded", "eta": REFUND_ETA}),
    ]
}

POLICIES = {
    "shipping": (
        "Standard shipping is free on orders over $35 and arrives in 3-5 business days. "
        "Orders under $35 pay a flat rate at checkout."
    ),
    "returns": (
        "Physical books can be returned within 30 days of delivery if they are unread and "
        "in resalable condition. E-books are non-returnable once downloaded."
    ),
    "refunds": (
        "Refunds are issued to the original payment method 5-7 business days after we "
        "receive the returned item."
    ),
    "password_reset": (
        "We email a password reset link to the address on the account; it is valid for one "
        "hour. Bookly support never asks for, and will never accept, your password."
    ),
}


def _ticket(ctx: dict[str, Any] | None) -> dict[str, Any]:
    return (ctx or {}).get("ticket") or {}


def _scoped_order(order_id: Any, ctx: dict[str, Any] | None) -> tuple[dict | None, dict | None]:
    """Validate the id, require a stated email, and scope the order to it.

    Returns (order, error). A missing order and another customer's order both
    produce `order_not_found`, so ownership probing tells the caller nothing.
    """
    if not isinstance(order_id, str) or not ORDER_ID_RE.match(order_id.strip()):
        return None, {"error": "invalid_order_id_format", "expected": "BK-12345"}
    email = (_ticket(ctx).get("email") or "").strip().lower()
    if not email:
        return None, {"error": "verify_email_first"}
    order = ORDERS.get(order_id.strip())
    if order is None or order["email"].lower() != email:
        return None, {"error": "order_not_found"}
    return order, None


def route_to(department: Any = "", ctx: dict[str, Any] | None = None, **kwargs) -> dict[str, Any]:
    """Ask for a state change. `agent.py` applies it only if `can_transition` allows."""
    return {"routed": department}


def lookup_customer_orders(
    email: Any = "", ctx: dict[str, Any] | None = None, **kwargs
) -> dict[str, Any]:
    """Up to 5 orders for a stated email. No cross-customer listing exists."""
    if not isinstance(email, str) or not EMAIL_RE.match(email.strip()):
        return {"error": "invalid_email_format"}
    wanted = email.strip().lower()
    found = [
        {"order_id": o["order_id"], "status": o["status"], "items": o["items"]}
        for o in ORDERS.values()
        if o["email"].lower() == wanted
    ]
    return {"orders": found[:5]}


def lookup_order(order_id: Any = "", ctx: dict[str, Any] | None = None, **kwargs) -> dict[str, Any]:
    """One order, scoped to the ticket email."""
    order, error = _scoped_order(order_id, ctx)
    if error:
        return error
    return {k: order[k] for k in PUBLIC_ORDER_FIELDS}


def propose_return(
    order_id: Any = "", reason: Any = "", ctx: dict[str, Any] | None = None, **kwargs
) -> dict[str, Any]:
    """Store a server-side proposal; it can only be confirmed on a later turn."""
    order, error = _scoped_order(order_id, ctx)
    if error:
        return error
    if not isinstance(reason, str) or not reason.strip():
        return {"error": "reason_required"}
    if order["status"] != "delivered":
        return {"error": "order_not_delivered", "status": order["status"]}
    if any(item.get("format") == "ebook" for item in order["items"]):
        return {"error": "ebook_not_returnable", "status": order["status"]}
    days = (date.today() - date.fromisoformat(order["delivered_on"])).days
    if days > RETURN_WINDOW_DAYS:
        return {"error": "return_window_expired", "status": order["status"],
                "days_since_delivery": days}
    _ticket(ctx)["pending_action"] = {
        "action": "return",
        "order_id": order["order_id"],
        "reason": reason.strip(),
        "proposed_turn": (ctx or {}).get("turn"),
    }
    return {
        "status": "awaiting_customer_confirmation",
        "order_id": order["order_id"],
        "reason": reason.strip(),
        "refund_eta": REFUND_ETA,
    }


def confirm_return(ctx: dict[str, Any] | None = None, **kwargs) -> dict[str, Any]:
    """Execute the stored proposal — never model-supplied values. No arguments."""
    ticket = _ticket(ctx)
    pending = ticket.get("pending_action")
    if not pending or pending.get("action") != "return":
        return {"error": "nothing_to_confirm"}
    if pending.get("proposed_turn") == (ctx or {}).get("turn"):
        return {"error": "confirm_on_a_later_turn"}
    order_id = pending["order_id"]
    ticket["pending_action"] = None
    return {"rma_id": f"RMA-{order_id.split('-')[1]}", "label": "emailed", "order_id": order_id}


def search_policy(topic: Any = "", ctx: dict[str, Any] | None = None, **kwargs) -> dict[str, Any]:
    """The only source of policy text; the model must not invent any."""
    key = topic.strip().lower() if isinstance(topic, str) else ""
    if key not in POLICIES:
        return {"error": "unknown_topic", "topics": sorted(POLICIES)}
    return {"topic": key, "policy": POLICIES[key]}


def send_password_reset(
    email: Any = "", ctx: dict[str, Any] | None = None, **kwargs
) -> dict[str, Any]:
    """Email a reset link. The answer never reveals whether the account exists.

    Deliberately does not look in ORDERS: a known and an unknown address get
    the identical reply, so this cannot be used to enumerate customers.
    """
    if not isinstance(email, str) or not EMAIL_RE.match(email.strip()):
        return {"error": "invalid_email_format"}
    return {"sent_if_account_exists": True}


def escalate_to_human(
    summary: Any = "", ctx: dict[str, Any] | None = None, **kwargs
) -> dict[str, Any]:
    """Contacting a human is a tool call like any other."""
    text = summary if isinstance(summary, str) else str(summary)
    return {"handoff": "queued", "summary": text[:200]}


def _schema(name: str, description: str, properties: dict | None = None,
            required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties or {},
            "required": list(required),
        },
    }


_STR = {"type": "string"}
_ORDER_ID = {"type": "string", "pattern": r"^BK-\d{5}$"}

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    s["name"]: s
    for s in [
        _schema("route_to", "Move the conversation to the department that handles this inquiry.",
                {"department": {"type": "string", "enum": ["orders", "returns", "general"]}},
                ("department",)),
        _schema("lookup_customer_orders",
                "List the recent orders for the email address the customer gave you.",
                {"email": _STR}, ("email",)),
        _schema("lookup_order",
                "Get the status of one order. Requires the customer's email first.",
                {"order_id": _ORDER_ID}, ("order_id",)),
        _schema("propose_return",
                "Propose a return for one delivered order. This does not start it: read the "
                "proposal back to the customer and call confirm_return after they agree.",
                {"order_id": _ORDER_ID, "reason": _STR}, ("order_id", "reason")),
        _schema("confirm_return",
                "Execute the return already proposed to this customer, after they say yes on a "
                "later turn. Takes no arguments."),
        _schema("search_policy",
                "Look up Bookly policy text. The only allowed source of policy answers.",
                {"topic": {"type": "string",
                           "enum": ["shipping", "returns", "refunds", "password_reset"]}},
                ("topic",)),
        _schema("send_password_reset",
                "Email a password reset link to the customer's account address. Says "
                "nothing about whether that account exists.",
                {"email": _STR}, ("email",)),
        # `summary` is deliberately not required: when it was, the model asked
        # the customer what they needed before it would escalate, which is the
        # opposite of what someone asking for a person wants.
        _schema("escalate_to_human",
                "Hand the conversation to a human teammate. Pass whatever you already "
                "know as the summary, however brief; never ask for more first.",
                {"summary": _STR}),
    ]
}

TOOL_DISPATCH: dict[str, Any] = {
    "route_to": route_to,
    "lookup_customer_orders": lookup_customer_orders,
    "lookup_order": lookup_order,
    "propose_return": propose_return,
    "confirm_return": confirm_return,
    "search_policy": search_policy,
    "send_password_reset": send_password_reset,
    "escalate_to_human": escalate_to_human,
}


def schemas_for(names: list[str]) -> list[dict[str, Any]]:
    """Schemas for a state's tool list. Raises loudly on a tool with no schema."""
    missing = [n for n in names if n not in TOOL_SCHEMAS]
    if missing:
        raise KeyError(f"no tool schema for: {', '.join(missing)}")
    return [TOOL_SCHEMAS[n] for n in names]
