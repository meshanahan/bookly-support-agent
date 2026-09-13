DECISIONS.md — Bookly support agent
Thesis

The model decides, the code enforces. A great CX agent uses the LLM for the two things only language can do — understand what the customer wants and phrase the reply — and puts every rule that must hold (who may see which data, what needs confirmation, what needs a human) in code the model cannot talk its way around.

Verdict: keep. Reworded from "what the model is unable to do" to "the model decides, the code enforces" — the same split Anthropic draws between workflows and agents and the split Decagon describes in Agent Operating Procedures (natural-language instructions, validation in code).

Decision 1 — Procedures with scoped tools (not a rigid state machine)

Chose: triage → orders / returns / general, each with 3–7 tools; route_to reachable from every state; the allowlist enforced in Python. Traded off: one extra model call for triage; a topic switch needs an explicit route. Why worth it: tool-selection accuracy degrades as the catalog grows, and routing to specialised prompts is a named production pattern. Every routing decision shows in the trace. Concede: at seven tools a single prompt would work; this earns its keep at 30+ tools and policies.

Verdict: keep the mechanism, change the framing. Don't present it as a state machine or decision tree — Decagon positions AOPs against rigid pre-programmed paths. Present it as procedures with loose edges: the model picks the path, code decides which tools are reachable.

Decision 2 — Safety by construction, with risk tiers

Chose: order data scoped to the stated email in code; no cross-customer listing tool; policy text only from search_policy; a return is a server-stored proposal confirmable only on a later turn; refunds stay behind escalate_to_human. Tool risk tiers: low = read-only lookups and policy; medium = propose/confirm return (server-bound); high = money movement → human. Traded off: an identity turn and a confirmation turn; no real authentication in the demo. Why worth it: function-calling agents are demonstrably weak at following written policy, so policy lives in code paths. This is also how Decagon says AOPs guard refunds and identity, and how OpenAI's guide treats high-risk actions. Concede: stated-email identity is not authentication; whether the customer really said "yes" is still model judgement. Both are in ASSUMPTIONS.md.

Verdict: keep — strongest decision. Add the risk-tier table to the deck and README.

Decision 3 — Stateless reducer, no framework, tested for consistency

Chose: run_turn(conv, text) -> TurnResult, an explicit 4-round loop, context built by hand, compaction that never orphans a tool result; ten offline tests with a fake LLM. Traded off: no free retries, streaming, tracing or checkpointing. Why worth it: the assignment asks to see orchestration directly; production customer-facing agents are mostly deterministic code with LLM steps at chosen points; the simplest solution first. Add: reliability is pass^k, not pass@1 — each Stage 4 scenario is run three times against the live model and consistency is reported, not a single lucky run. Concede: tracing and a queue are the first production additions; explicit state makes that cheap.

Verdict: keep. Add the 3× repeat to Stage 4 and quote the measured consistency on slide 4.

What I'd change first in production

Simulated-customer evaluations with an LLM judge, scored pass^k, before streaming voice or persistence. Reason: the offline tests prove orchestration, not model decisions, and reliability across repeated runs is the actual product in CX. Then verified identity plus an explicit UI confirmation for actions. Then streaming STT/TTS over WebRTC with VAD turn detection.

Cut list (→ ASSUMPTIONS.md)

Refund issuance, real authentication, RAG over a help center, streaming, phone channel, VAD turn detection.