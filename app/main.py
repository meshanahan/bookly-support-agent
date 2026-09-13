"""HTTP surface: one channel-agnostic endpoint.

Rule (Horthy): text and voice hit the same endpoint with a `mode` flag — a
telephony adapter would call the same `run_turn`.
Rule (Kramer): latency is measured and returned honestly (`llm_ms`, `total_ms`,
`llm_calls` are server time only), and a slow model must not hang the caller,
so the turn runs under a 25 s timeout with a friendly reply on failure.
The conversation store is in-memory behind two functions — swap it for Redis
without touching the agent.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import run_turn
from .llm import complete
from .state import Conversation

TIMEOUT_S = 25.0
TIMEOUT_REPLY = (
    "Sorry — that's taking longer than it should on our side. "
    "Could you say that once more, or would you like a teammate?"
)
ERROR_REPLY = (
    "Sorry — something went wrong on our side just then. "
    "Could you try that again, or would you like a teammate?"
)

app = FastAPI(title="Bookly support agent")
_STORE: dict[str, Conversation] = {}


def load_conversation(conversation_id: str | None) -> Conversation:
    if conversation_id and conversation_id in _STORE:
        return _STORE[conversation_id]
    return Conversation()


def save_conversation(conv: Conversation) -> None:
    _STORE[conv.id] = conv


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    text: str
    mode: str = "text"


class ResetRequest(BaseModel):
    conversation_id: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/reset")
def reset(req: ResetRequest) -> dict[str, str]:
    if req.conversation_id:
        _STORE.pop(req.conversation_id, None)
    conv = Conversation()
    save_conversation(conv)
    return {"conversation_id": conv.id, "state": conv.state}


@app.post("/chat")
async def chat(req: ChatRequest) -> dict[str, Any]:
    conv = load_conversation(req.conversation_id)
    conv.mode = req.mode if req.mode in ("text", "voice") else "text"
    t0 = time.perf_counter()

    # The turn runs against a copy and is committed only if it finishes. A
    # thread cannot be cancelled, so on timeout `run_turn` keeps going for a
    # while on whatever object it holds; if that were the stored conversation
    # it would apply state changes and even execute a return on a turn the
    # customer was just told had failed. `stop` asks it to stop at the next
    # round boundary; the copy is what makes that safe rather than merely
    # cheaper.
    working = conv.model_copy(deep=True)
    stop = threading.Event()
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(run_turn, working, req.text, complete, stop.is_set), TIMEOUT_S
        )
        reply, state, trace = result.reply, result.state, result.trace
        llm_ms, total_ms, llm_calls = result.llm_ms, result.total_ms, result.llm_calls
        conv = working  # commit: the turn finished
    except Exception as exc:
        stop.set()  # the abandoned turn is still running; ask it to stop
        # One friendly line, never a stack trace — and say which thing happened.
        slow = isinstance(exc, TimeoutError)  # asyncio.TimeoutError since 3.11
        reply, state, trace = (TIMEOUT_REPLY if slow else ERROR_REPLY), conv.state, []
        llm_ms, llm_calls = 0, 0
        total_ms = int((time.perf_counter() - t0) * 1000)
    save_conversation(conv)
    return {
        "conversation_id": conv.id,
        "reply": reply,
        "state": state,
        "ticket": conv.ticket,
        "tool_trace": trace,
        "llm_ms": llm_ms,
        "total_ms": total_ms,
        "llm_calls": llm_calls,
    }


_STATIC = Path(__file__).parent / "static"
if (_STATIC / "index.html").exists():  # index.html arrives in Prompt B
    app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")
