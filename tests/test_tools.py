"""Tool-level tests: validation, scoping, and the return rules — no LLM involved.

The last test fails until GAP 3 (`send_password_reset`) is written; it imports
inside the test so the rest of the file still runs.
"""

from __future__ import annotations

from app import tools
from app.state import new_ticket


def ctx_for(email: str = "", turn: int = 1, state: str = "returns") -> dict:
    ticket = new_ticket()
    ticket["email"] = email
    return {"ticket": ticket, "turn": turn, "state": state}


def test_invalid_order_id_format():
    ctx = ctx_for("maya@bookly-demo.com")
    assert tools.lookup_order(order_id="BK-1", ctx=ctx)["error"] == "invalid_order_id_format"
    assert tools.lookup_order(order_id="12345", ctx=ctx)["error"] == "invalid_order_id_format"


def test_order_not_found_is_identical_for_missing_and_someone_elses():
    """Ownership probing must tell the caller nothing."""
    ctx = ctx_for("maya@bookly-demo.com")
    missing = tools.lookup_order(order_id="BK-99999", ctx=ctx)
    other_customer = tools.lookup_order(order_id="BK-20483", ctx=ctx)
    assert missing == other_customer == {"error": "order_not_found"}

    assert tools.lookup_order(order_id="BK-20483", ctx=ctx_for(""))["error"] == "verify_email_first"


def test_ebook_return_is_refused():
    ctx = ctx_for("theo@bookly-demo.com")
    out = tools.propose_return(order_id="BK-30099", reason="changed my mind", ctx=ctx)
    assert out["error"] == "ebook_not_returnable"
    assert ctx["ticket"]["pending_action"] is None


def test_return_outside_the_30_day_window_is_refused():
    ctx = ctx_for("theo@bookly-demo.com")
    out = tools.propose_return(order_id="BK-20483", reason="damaged", ctx=ctx)
    assert out["error"] == "return_window_expired"
    assert out["status"] == "delivered"
    assert ctx["ticket"]["pending_action"] is None


def test_confirm_return_with_nothing_pending():
    assert tools.confirm_return(ctx=ctx_for("maya@bookly-demo.com")) == {
        "error": "nothing_to_confirm"
    }


def test_send_password_reset_is_identical_for_known_and_unknown_emails():
    """GAP 3: fails until the schema, function and dispatch entry exist."""
    from app.tools import TOOL_DISPATCH, TOOL_SCHEMAS, send_password_reset

    known = send_password_reset(email="maya@bookly-demo.com", ctx=ctx_for(state="general"))
    unknown = send_password_reset(email="nobody@example.com", ctx=ctx_for(state="general"))
    assert known == unknown == {"sent_if_account_exists": True}
    assert send_password_reset(email="not-an-email", ctx=ctx_for(state="general")) != known
    assert "send_password_reset" in TOOL_SCHEMAS
    assert "send_password_reset" in TOOL_DISPATCH
