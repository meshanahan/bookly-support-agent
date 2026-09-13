# Assumptions and honest limits

Plainly, what this demo does not do.

- **The data is mock.** Six orders live in a dict in `app/tools.py`. There is
  no database, no order service, no email delivery.

- **Identity is whatever email the customer states. There is no
  authentication.** Nothing verifies that the person typing
  `maya@bookly-demo.com` is Maya. A real deployment needs verified identity —
  a signed-in session, or an out-of-band verification step — before any order
  data is shown.

- **Order data is scoped to that stated email in code, not by prompt.**
  `lookup_order` and `propose_return` compare the order's owner against
  `ticket["email"]` in Python and return `order_not_found` for both a missing
  order and another customer's order, so existence is not leaked. No tool
  enumerates orders across customers, so there is nothing for "list every
  order" to call.

- **Confirmations are bound server-side to a specific proposal and turn — but
  whether the customer actually said "yes" is still the model's judgement.**
  `propose_return` stores the order and reason on the server;
  `confirm_return` takes no arguments, executes only the stored proposal, and
  refuses on the same turn it was proposed, so the customer must have had a
  turn to answer. It cannot execute a return the server didn't propose. It
  can still be triggered by a model that misreads an ambiguous reply. In
  production this belongs behind an explicit UI confirmation or an
  out-of-band approval, not a model's reading of a transcript.

- **The model can still misstate a tool result — observed, not theoretical.**
  Nothing forces the reply to match the JSON it was given. In the first Stage 4
  run the agent told a customer "Perfect, I've confirmed your return for order
  BK-10002" on a turn where it called no tool at all: the proposal was still
  pending and no RMA existed. The server was never fooled and nothing was
  executed, but the customer was misinformed. A prompt rule made it rare
  (5/5 correct after), small flat tool outputs help, and the eval caught it —
  but only an explicit UI confirmation removes it. See `DEMO_NOTES.md`.

- **The offline tests measure pass@1 on scripted decisions; the product needs
  pass^k.** Ten tests with a fake LLM prove the orchestration behaves given a
  model decision, and they are deterministic. They say nothing about whether
  the live model decides the same way twice. Each live scenario is therefore
  run three times and the consistency reported in `DEMO_NOTES.md`; three runs
  is a smoke test, not an eval suite.

- **Procedures earn their keep at scale, not at this size.** With seven tools
  a single prompt would work. Splitting them across triage, orders, returns
  and general pays off at thirty-plus tools and policies, where tool-selection
  accuracy degrades; here it buys a visible routing trace and shorter prompts.

- **There is no refund tool, on purpose.** Money movement is the
  highest-consequence action available to a support agent, so it stays behind
  `escalate_to_human`.

- **Latency figures are measured; 1,500 ms is a target.** `llm_ms`,
  `total_ms` and `llm_calls` are real server-side measurements and exclude
  browser STT/TTS and network time. `voice_ms` is measured in the browser from
  the end of speech recognition to the first spoken audio. `MAX_STEPS = 4`
  bounds LLM calls per turn — cost and loop risk — not wall-clock time.

- **Browser speech APIs stand in for a production STT/TTS vendor.**
  `SpeechRecognition` and `speechSynthesis` are free, unstreamed, and vary by
  browser. Production would use a streaming vendor over WebRTC. The spoken
  voice is therefore whatever the operating system happens to ship: the app
  prefers a Korean voice to match the wording and exposes a picker, but the
  list is machine-dependent and nothing here guarantees a given customer hears
  the same voice. A real deployment picks one vendor voice and keeps it.

- **Push-to-talk stands in for VAD-based turn detection.** Holding a button is
  an unambiguous end of turn with zero infrastructure. Production needs voice
  activity detection plus a turn-detection model.

- **Cut from scope:** refund issuance (escalation only), real authentication,
  RAG over a help center, streaming responses, VAD turn detection, and the
  phone channel. A phone channel would call the same `run_turn` — the endpoint
  is channel-agnostic by design.

- **The conversation store is a process-local dict.** Restarting the server
  drops every conversation, and it will not survive more than one worker. It
  is also unbounded and unlocked: nothing evicts old conversations, and two
  requests arriving for the same conversation at once would interleave their
  writes. A single customer with one browser tab cannot do this, which is why
  it is a limit rather than a bug, but Redis plus a per-conversation lock is
  the real answer.

- **A timed-out turn is abandoned, not cancelled.** `asyncio.wait_for` cannot
  stop a thread. The turn therefore runs against a deep copy that is committed
  only if it finishes, and a `should_stop` flag ends it at the next round
  boundary — so a turn the customer was told had failed cannot change state or
  execute a return behind them. It may still finish the one model call that
  was already in flight. See `DEMO_NOTES.md` for the measurements.

## What I would change first, in order

1. **Simulated-customer evaluations with an LLM judge, scored pass^k.** Before
   streaming voice and before persistence. The offline tests prove
   orchestration, not model decisions, and reliability across repeated runs is
   the actual product in customer experience.
2. **Verified identity, and an explicit UI confirmation for actions.** This
   retires the two honest gaps above — stated-email identity, and "yes" being
   the model's reading of a transcript.
3. **Streaming speech-to-text and text-to-speech over WebRTC with VAD turn
   detection,** plus a streamed LLM, to put first audio near 600 ms.

Tracing and a work queue follow; explicit state makes both cheap to add.
